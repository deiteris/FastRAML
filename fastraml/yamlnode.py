"""The YAML node model.

Everything above this module works with `Node`, never with a YAML library type.
That boundary gives a node object this parser controls: identity-hashable,
slotted, and carrying the source positions every diagnostic needs.

RAML cannot use a plain `yaml.safe_load`, because it needs declaration order,
source positions, the `!include` tag left unresolved, the leading `#%RAML`
comment, and — for the trait and resource-type merge — the tree itself after
decoding. See docs/03-yaml-and-io.md.
"""

from __future__ import annotations

import re
import sys
from contextvars import ContextVar
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Final, Protocol

import yaml
from yaml.nodes import ScalarNode

from fastraml.errors import ErrorKind, RamlError
from fastraml.positions import Position

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    'AUTHORED_NODES',
    'DEFAULT_MAX_DEPTH',
    'MAX_NODES',
    'Node',
    'NodeKind',
    'backend_name',
    'compose',
    'decode_source',
    'duplicate_keys',
    'end_column',
    'end_line',
    'is_null',
    'last_leaf',
    'mark_subtree',
    'node_error',
    'pairs',
    'read_head',
    'with_content',
    'with_value',
]

try:  # pragma: no cover - depends on how PyYAML was built
    from yaml import CSafeLoader as _Loader

    _BACKEND = 'libyaml'
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _Loader  # type: ignore[assignment]

    _BACKEND = 'python'


#: The YAML 1.2 core schema, as the resolver table PyYAML's scanner consults.
#:
#: RAML 1.0 is defined over YAML 1.2; PyYAML implements YAML 1.1. Left alone it
#: reads `no` as a boolean, `12:30:00` as the integer 45000 and `1e3` as a
#: string — so `example: no` on a string type would silently become `False`, and
#: the spec's own `lunchtime: 12:30:00` would become a number.
#:
#: The patterns are `ruamel.yaml`'s YAML 1.2 resolvers, which track the spec and
#: agree with `gopkg.in/yaml.v3` — the library go-raml uses, and therefore what
#: the TCK is scored against. `tests/conformance` composes the
#: whole corpus through both and fails on any disagreement.
#:
#: `null`, `str`, `seq`, `map` and `timestamp` are left as PyYAML has them: the
#: oracle shows they already agree. Timestamps stay implicit, as in go-yaml, and
#: `datanode` keeps their text rather than converting.
_YAML_1_2_RESOLVERS: Final = (
    (
        'tag:yaml.org,2002:bool',
        re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'),
        'tTfF',
    ),
    (
        'tag:yaml.org,2002:int',
        re.compile(
            r"""^(?:[-+]?0b[0-1_]+
            |[-+]?0o?[0-7_]+
            |[-+]?[0-9_]+
            |[-+]?0x[0-9a-fA-F_]+)$""",
            re.VERBOSE,
        ),
        '-+0123456789',
    ),
    (
        'tag:yaml.org,2002:float',
        re.compile(
            r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
            |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
            |[-+]?\.[0-9_]+(?:[eE][-+][0-9]+)?
            |[-+]?\.(?:inf|Inf|INF)
            |\.(?:nan|NaN|NAN))$""",
            re.VERBOSE,
        ),
        '-+.0123456789',
    ),
)


class _RamlLoader(_Loader):  # type: ignore[valid-type, misc]
    """`_Loader` with YAML 1.2 scalar resolution.

    Only the implicit-resolver table and `resolve` itself change. The scanner is
    untouched, which matters: PyYAML ships a current libyaml, so
    `[ http://example.com ]` — a colon inside a plain scalar in flow context —
    already parses correctly.
    """

    def resolve(self, kind: Any, value: str, implicit: Any) -> Any:
        """`BaseResolver.resolve`, specialised for implicit scalars.

        libyaml calls this once per scalar. The stock implementation
        concatenates `resolvers + wildcard_resolvers` per call, allocating a
        list to append one that is always empty here.

        The short path assumes the table has no wildcard (`None`) key and no
        path resolvers are registered; `_assert_resolver_shape` checks both at
        import. `value[:1]` also covers the empty string.
        """
        if kind is ScalarNode and implicit[0]:
            for tag, regexp in self.yaml_implicit_resolvers.get(value[:1], ()):
                if regexp.match(value):
                    return tag
            return self.DEFAULT_SCALAR_TAG
        return super().resolve(kind, value, implicit)


_REPLACED: Final = frozenset(tag for tag, _pattern, _first in _YAML_1_2_RESOLVERS)
_RamlLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag not in _REPLACED]
    for first, resolvers in _Loader.yaml_implicit_resolvers.items()
}
for _tag, _pattern, _first_chars in _YAML_1_2_RESOLVERS:
    _RamlLoader.add_implicit_resolver(_tag, _pattern, list(_first_chars))


def _assert_resolver_shape() -> None:
    """The two preconditions `_RamlLoader.resolve`'s short path relies on.

    Checked once at import. If a future PyYAML registers a wildcard or path
    resolver, the specialised `resolve` would silently ignore it and resolve
    scalars to the wrong tag, so the import fails instead.
    """
    if None in _RamlLoader.yaml_implicit_resolvers:
        message = 'PyYAML registered a wildcard implicit resolver; yamlnode.resolve must handle it'
        raise RuntimeError(message)
    if _RamlLoader.yaml_path_resolvers:
        message = 'PyYAML registered a path resolver; yamlnode.resolve must handle it'
        raise RuntimeError(message)


_assert_resolver_shape()


def backend_name() -> str:
    """Which YAML backend is in use: `'libyaml'` or `'python'`.

    Reported by `fastraml info`: libyaml is roughly an order of magnitude
    faster, and benchmark baselines are fingerprinted by backend (docs/12 § 4).
    """
    return _BACKEND


class NodeKind(IntEnum):
    SCALAR = 0
    MAPPING = 1
    SEQUENCE = 2


# YAML tags, in the short form the parser matches on.
TAG_STR: Final = '!!str'
TAG_INT: Final = '!!int'
TAG_FLOAT: Final = '!!float'
TAG_BOOL: Final = '!!bool'
TAG_NULL: Final = '!!null'
TAG_TIMESTAMP: Final = '!!timestamp'
TAG_MAP: Final = '!!map'
TAG_SEQ: Final = '!!seq'
TAG_MERGE: Final = '!!merge'
TAG_INCLUDE: Final = '!include'

_STANDARD_TAG_PREFIX: Final = 'tag:yaml.org,2002:'

#: The short form of each tag the resolver assigns, built once rather than per
#: node: slicing a new string for every one of a document's nodes was the
#: composer's third-largest allocation (docs/12 § 2).
_SHORT_TAGS: Final = {
    _STANDARD_TAG_PREFIX + name: '!!' + name
    for name in ('str', 'int', 'float', 'bool', 'null', 'timestamp', 'map', 'seq', 'merge', 'binary', 'set', 'omap')
}

#: The `content` of every node created without children. Shared, so a scalar
#: allocates no list; safe because no pass edits a node's `content` in place
#: (`with_content` builds a new node instead). `tests/conftest.py` checks it
#: is still empty after the whole suite.
_NO_CONTENT: Final[list[Node]] = []

#: YAML 1.1 reads these as line breaks; YAML 1.2 says they are ordinary
#: characters. PyYAML's scanner implements 1.1, so an unquoted scalar containing
#: one is split and fails elsewhere (docs/03 § 2.1).
_LINE_SEPARATORS: Final = ('\u2028', '\u2029')

#: The ceiling on every recursive descent whose depth is bounded only by the
#: input: document conversion here, unwrap and recursion marking in P9, the
#: discriminator and custom-facet walks in P10, and the JSON Schema walks. They
#: defend the same C stack, so they share one number, surfaced as
#: `ParseOptions.max_depth` and carried on `Raml.max_depth` (docs/12 § 3).
#: Defined here because `yamlnode` is the lowest layer that needs it.
DEFAULT_MAX_DEPTH: Final = 200

#: Maximum number of nodes one document may expand to. YAML aliases are expanded
#: rather than shared (see `compose`), so this is what bounds a "billion laughs"
#: expansion.
MAX_NODES: Final = 1_000_000


class Node:
    """One YAML node with its source span.

    `content` is **flat**: for a mapping it is `[key0, value0, key1, value1, ...]`
    rather than a list of pairs. Decoders walk it with `range(0, len(content), 2)`
    and allocate no tuples, and the structural merge can splice pairs by
    position. Use `pairs()` when readability matters more than allocation.

    `Node` deliberately defines no `__eq__` or `__hash__`, so it keeps identity
    semantics. That is a requirement, not an oversight: the provenance overlay
    is a `dict[Node, ParseCtx]` keyed by object identity, and the trait merge
    preserves node identity so those lookups stay valid.
    See docs/08-templates-and-endpoints.md § 4.

    A node is never edited after it is built, which is what lets `content`
    default to one shared empty list and `position` be built once.
    """

    __slots__ = ('_position', 'column', 'content', 'end_column', 'end_line', 'kind', 'line', 'tag', 'value')

    def __init__(  # noqa: PLR0913, PLR0917 - a node is eight fields of plain data
        self,
        kind: NodeKind,
        tag: str,
        value: str = '',
        content: list[Node] | None = None,
        line: int = 1,
        column: int = 1,
        end_line: int = 1,
        end_column: int = 1,
    ) -> None:
        self.kind = kind
        self.tag = tag
        self.value = value
        self.content: list[Node] = content if content is not None else _NO_CONTENT
        self.line = line
        self.column = column
        self.end_line = end_line
        self.end_column = end_column
        self._position: Position | None = None

    def __repr__(self) -> str:
        where = f'{self.line}:{self.column}'
        if self.kind is NodeKind.SCALAR:
            return f'Node({self.tag} {self.value!r} @{where})'
        return f'Node({self.tag} x{len(self.content)} @{where})'

    @property
    def position(self) -> Position:
        """The span of this node's own token.

        Built on first use and kept: template application shares a trait's
        nodes by pointer, so every entity decoded from one node shares its
        `Position` rather than holding a copy (docs/12 § 2).
        """
        position = self._position
        if position is None:
            position = self._position = Position(self.line, self.column, self.end_line, self.end_column)
        return position

    @property
    def full_position(self) -> Position:
        """The span of this node including every descendant.

        For a scalar this equals `position`. For a mapping or sequence it runs
        to the end of the last leaf, so an editor underlines the whole block.
        """
        if not self.content:
            return self.position
        leaf = last_leaf(self)
        return Position(self.line, self.column, leaf.end_line, leaf.end_column)


def with_content(model: Node, content: list[Node]) -> Node:
    """A fresh node with `model`'s kind, tag and span, holding `content`.

    The one way a pass rebuilds a container: a filter, a merge or a
    substitution never edits the tree it read, and every retained child keeps
    its identity, so provenance lookups still find it.
    """
    return Node(
        model.kind,
        model.tag,
        model.value,
        content,
        model.line,
        model.column,
        model.end_line,
        model.end_column,
    )


def with_value(model: Node, value: str) -> Node:
    """A fresh scalar with `model`'s tag and span, reading `value`."""
    return Node(NodeKind.SCALAR, model.tag, value, None, model.line, model.column, model.end_line, model.end_column)


def pairs(node: Node) -> Iterator[tuple[Node, Node]]:
    """Iterate a mapping node's key/value pairs."""
    content = node.content
    for index in range(0, len(content) - 1, 2):
        yield content[index], content[index + 1]


def mark_subtree[V](marks: dict[Node, V], node: Node, value: V) -> None:
    """Map `node` and every descendant to `value`, set-if-absent.

    The whole subtree, because `Node` has no parent pointer: a decoder asks
    about the node in its hand, however deep. A node already in `marks` keeps
    its value, and its subtree is not entered, so a more specific mark
    recorded earlier survives. Iterative, so depth costs no stack.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current in marks:
            continue
        marks[current] = value
        stack += current.content


def last_leaf(node: Node) -> Node:
    """The deepest last-child descendant. Iterative, so depth costs nothing."""
    while node.content:
        node = node.content[-1]
    return node


def end_line(node: Node) -> int:
    """The last source line occupied by this node and its descendants."""
    return last_leaf(node).end_line


def end_column(node: Node) -> int:
    """The exclusive end column of this node's last descendant token."""
    return last_leaf(node).end_column


def is_null(node: Node) -> bool:
    """Whether this node is YAML null — an absent value, `~`, or `null`.

    RAML uses an empty value widely (`get:`, `/users:`, `types:`), so decoders
    test this constantly.
    """
    return node.tag == TAG_NULL


def duplicate_keys(node: Node) -> list[tuple[str, Node]]:
    """Keys appearing more than once in a mapping, with their later key nodes.

    YAML permits duplicates; RAML does not, and no RAML construct gives them a
    meaning. `compose` records rather than rejects them so that each decoder can
    report the duplicate at the right position with the right message.
    """
    if node.kind is not NodeKind.MAPPING:
        return []
    seen: set[str] = set()
    found: list[tuple[str, Node]] = []
    for key, _value in pairs(node):
        if key.value in seen:
            found.append((key.value, key))
        else:
            seen.add(key.value)
    return found


def node_error(
    message: str,
    location: str,
    node: Node | None = None,
    *,
    kind: ErrorKind = ErrorKind.PARSING,
    info: Mapping[str, object] | None = None,
) -> RamlError:
    """Build a diagnostic pointing at `node`'s full span.

    Returned rather than raised, because a decoder more often hands the result
    to an `Accumulator` than raises it, and because a factory call keeps the
    message text out of the `raise` statement.
    """
    position = None
    if node is not None:
        position = node.full_position
        authors = AUTHORED_NODES.get()
        if authors:
            author = authors.get(node)
            if author is not None:
                # The position is in the file that wrote the node, whatever
                # document the caller was decoding (docs/19 § 5.3).
                location = author.location
    return RamlError.new(message, location, position, kind=kind, info=info)


class _Located(Protocol):
    @property
    def location(self) -> str: ...


#: The nodes an extension document wrote, and which document wrote each. Set by
#: `Raml.reporting_authorship` for the length of a parse; read only when a
#: diagnostic is built.
AUTHORED_NODES: ContextVar[Mapping[Node, _Located] | None] = ContextVar('AUTHORED_NODES', default=None)


def read_head(text: str) -> str:
    """The first line of a document, stripped of its line ending.

    RAML identifies a fragment by its first line — `#%RAML 1.0 Trait` — which is
    a YAML comment. The line is read but **not removed**: composing the full
    text keeps every subsequent line number correct.
    """
    newline = text.find('\n')
    line = text if newline < 0 else text[:newline]
    return line.rstrip('\r \t')


def _mark_position(node: yaml.Node) -> tuple[int, int, int, int]:
    """PyYAML marks are 0-based; the model is 1-based."""
    start = node.start_mark
    end = node.end_mark
    return (start.line + 1, start.column + 1, end.line + 1, end.column + 1)


class _Converter:
    """Converts a PyYAML node graph into a `Node` tree.

    PyYAML's composer returns the *same object* for every alias of an anchor, so
    its result is a graph. Every algorithm above this layer assumes a tree, and
    the provenance overlay keys on node identity, so a shared node would carry
    one resolution scope for two logical positions. Aliases are therefore
    expanded into independent copies, bounded by `MAX_NODES`.
    """

    __slots__ = ('_in_progress', '_max_depth', '_max_nodes', '_produced', '_uri')

    def __init__(self, uri: str, max_depth: int, max_nodes: int) -> None:
        self._uri = uri
        self._max_depth = max_depth
        self._max_nodes = max_nodes
        self._produced = 0
        self._in_progress: set[int] = set()

    def convert(self, node: yaml.Node, depth: int = 0) -> Node:
        if depth > self._max_depth:
            message = 'document nesting too deep'
            raise RamlError.new(
                message,
                self._uri,
                Position(*_mark_position(node)[:2]),
                kind=ErrorKind.PARSING,
                info={'limit': self._max_depth},
            )

        self._produced += 1
        if self._produced > self._max_nodes:
            message = 'document expands to too many nodes'
            raise RamlError.new(
                message,
                self._uri,
                Position(*_mark_position(node)[:2]),
                kind=ErrorKind.PARSING,
                info={'limit': self._max_nodes},
            )

        # `_mark_position` inlined: this runs once per node, and the call and
        # tuple cost more than the arithmetic.
        start = node.start_mark
        end = node.end_mark
        line = start.line + 1
        column = start.column + 1
        stop_line = end.line + 1
        stop_column = end.column + 1

        if isinstance(node, yaml.ScalarNode):
            tag = self._tag_of(node.tag, line, column, stop_line, stop_column)
            # The raw text is kept even for resolved scalars: RAML needs the
            # literal form of a `date-only` example, and `!!int` bounds are
            # parsed exactly rather than through float.
            return Node(
                NodeKind.SCALAR,
                tag,
                node.value,
                None,
                line,
                column,
                stop_line,
                stop_column,
            )

        identity = id(node)
        if identity in self._in_progress:
            message = 'recursive YAML anchor'
            raise RamlError.new(
                message,
                self._uri,
                Position(line, column, stop_line, stop_column),
                kind=ErrorKind.PARSING,
            )
        self._in_progress.add(identity)
        try:
            if isinstance(node, yaml.MappingNode):
                content: list[Node] = []
                for key, value in node.value:
                    key_node = self.convert(key, depth + 1)
                    # Decoders compare and hash mapping keys constantly;
                    # interning makes equal keys share one object.
                    key_node.value = sys.intern(key_node.value)
                    content.append(key_node)
                    content.append(self.convert(value, depth + 1))
                kind = NodeKind.MAPPING
            else:
                content = [self.convert(item, depth + 1) for item in node.value]
                kind = NodeKind.SEQUENCE
        finally:
            self._in_progress.discard(identity)

        tag = self._tag_of(node.tag, line, column, stop_line, stop_column)
        return Node(kind, tag, '', content, line, column, stop_line, stop_column)

    def _tag_of(self, tag: str, line: int, column: int, stop_line: int, stop_column: int) -> str:
        """Shorten the tag and reject an unknown local one.

        `tag:yaml.org,2002:str` becomes `!!str`; anything else passes through.

        `!include` is the only local tag RAML defines. Without the rejection,
        `!includeexample.json` (a missing space) is a valid YAML local tag on an
        empty scalar and silently parses as an empty value; the TCK has a
        fixture for it.
        """
        short = _SHORT_TAGS.get(tag)
        if short is not None:
            return short
        if tag.startswith(_STANDARD_TAG_PREFIX):
            return '!!' + tag[len(_STANDARD_TAG_PREFIX) :]
        if tag.startswith('!') and not tag.startswith('!!') and tag != TAG_INCLUDE:
            raise RamlError.new(
                'unknown tag',
                self._uri,
                Position(line, column, stop_line, stop_column),
                kind=ErrorKind.PARSING,
                info={'tag': tag},
            )
        return tag


def _empty_mapping() -> Node:
    return Node(NodeKind.MAPPING, TAG_MAP, '', [], 1, 1, 1, 1)


def decode_source(data: bytes) -> str:
    """Bytes from a loader as text.

    RAML is UTF-8. `utf-8-sig` also strips a byte order mark, which would
    otherwise precede `#%RAML` and defeat the fragment-header check. This is
    the parser's only bytes-to-text decision.
    """
    return data.decode('utf-8-sig')


def compose(
    source: str,
    *,
    uri: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = MAX_NODES,
) -> Node:
    """Parse one YAML document into a `Node` tree.

    A document that is empty, or holds nothing but comments, composes to an
    empty mapping rather than failing. Each fragment decoder then decides
    whether an empty body is valid for its kind: a `Trait` fragment may be
    empty, an API without a `title` may not.

    Raises `RamlError` for a YAML syntax error, for nesting beyond `max_depth`,
    for alias expansion beyond `max_nodes`, and for a recursive anchor.
    """
    text = source
    try:
        root = yaml.compose(text, Loader=_RamlLoader)
    except yaml.MarkedYAMLError as err:
        raise (_line_separator_error(text, uri) or _syntax_error(err, uri)) from err
    except yaml.YAMLError as err:
        raise (_line_separator_error(text, uri) or RamlError.new(str(err), uri, kind=ErrorKind.PARSING)) from err

    if root is None:
        return _empty_mapping()

    return _Converter(uri, max_depth, max_nodes).convert(root)


def _line_separator_error(text: str, uri: str) -> RamlError | None:
    """The diagnostic for a U+2028/U+2029 that the scanner read as a line break.

    Consulted only after composition has failed. The separators are replaced
    with spaces and the document composed again: only if that succeeds were
    they the cause. Otherwise the original diagnostic stands, so a file that
    merely contains a quoted, valid separator keeps its real error.

    One space per character keeps every offset, so the reported position is
    the character's own (docs/03 § 2.1).
    """
    if not any(separator in text for separator in _LINE_SEPARATORS):
        return None

    neutral = text
    for separator in _LINE_SEPARATORS:
        neutral = neutral.replace(separator, ' ')
    try:
        yaml.compose(neutral, Loader=_RamlLoader)
    except yaml.YAMLError:
        return None

    index = min(found for found in (text.find(s) for s in _LINE_SEPARATORS) if found >= 0)
    return RamlError.new(
        'unquoted line separator character',
        uri,
        Position(text.count('\n', 0, index) + 1, index - (text.rfind('\n', 0, index) + 1) + 1),
        kind=ErrorKind.PARSING,
        info={'character': f'U+{ord(text[index]):04X}', 'hint': 'quote the value or remove the character'},
    )


def _syntax_error(err: yaml.MarkedYAMLError, uri: str) -> RamlError:
    """Turn a PyYAML error into a positioned diagnostic.

    PyYAML embeds the position in prose (`in "<unicode string>", line 3, column
    5`). That is dropped: the position is carried as structured data instead, so
    it is not printed twice.
    """
    mark = err.problem_mark or err.context_mark
    position = Position(mark.line + 1, mark.column + 1) if mark is not None else None
    message = err.problem or err.context or str(err)
    info = {'context': err.context} if err.context and err.problem else None
    return RamlError.new(message.strip(), uri, position, kind=ErrorKind.PARSING, info=info)
