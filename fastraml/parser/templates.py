"""Template variables: `<<name | !action | !action>>`.

Implements docs/08-templates-and-endpoints.md § 5. A resource-type or trait
body is scanned once, at declaration time, producing an index that
substitution looks up at every application site without re-scanning.

The index is keyed by node identity, not by position: optional-method
filtering removes subtrees between the scan and its use (docs/08 § 3.1), and
identity keys survive that by construction.

The recasing functions use `str.split`, slicing and precompiled regexes, not
per-character loops (docs/12-performance.md § 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Self

from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.parser.facets import make_string_facet
from fastraml.parser.includes import note_include_ref
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, is_null, node_error, pairs, with_content, with_value

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pluralizer import Pluralizer

    from fastraml.parser.directives import DirectiveRef
    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.parser.structural_merge import ProvenanceOverlay
    from fastraml.registry import ParseCtx, Raml
    from fastraml.types.base import ScalarFacet

__all__ = [
    'KNOWN_ACTIONS',
    'RESERVED_PARAMETERS',
    'TEMPLATE_ACTIONS',
    'TemplateDefinition',
    'VariableIndex',
    'VariableInfo',
    'apply_template_action',
    'check_parameters',
    'collect_required_variables',
    'collect_variables_index',
    'compile_source_provenance',
    'find_template_definition',
    'iter_nodes',
    'make_template_definition',
    'parameter_node',
    'parse_template_variables',
]

#: The three parameters the parser injects at every application site. They are
#: always accepted and never required of the author (docs/08 § 3.1 and § 3.2).
RESERVED_PARAMETERS: Final = frozenset({'resourcePath', 'resourcePathName', 'methodName'})


def parameter_node(value: str) -> Node:
    """A template parameter value as a plain string scalar.

    Read-only, and never inserted into a compiled tree by pointer, so one node
    can serve every application site of a resource.
    """
    return Node(NodeKind.SCALAR, TAG_STR, value)


#: What one scan of a template body produces: every `<<...>>` bearing scalar,
#: keyed by the node itself.
type VariableIndex = dict[Node, list[VariableInfo]]

FACET_USAGE: Final = 'usage'


# -- the two template declarations (docs/08 § 3) -------------------------------


@dataclass(slots=True, eq=False)
class TemplateDefinition:
    """What a trait and a resource type share: a body kept as YAML, scanned once.

    One entry of a `traits:` or `resourceTypes:` map, or the whole of a Trait
    or ResourceType fragment. The two differ in which keys the body may carry
    and in how it is applied, not in how it is stored.
    """

    id: int
    name: str
    location: str
    usage: ScalarFacet[str] | None = None
    #: The body as written, minus `usage:`. `None` for an empty or linked one.
    source: Node | None = None
    declared_variables: set[str] = field(default_factory=set)
    variable_index: VariableIndex = field(default_factory=dict)
    #: `traits: {paged: !include ...}`: the target's own definition, filled in
    #: by `fragments.py`, which owns fragment decoding.
    link: Self | None = None
    link_uri: str | None = None
    #: The namespace the *body* resolves its type names in: this template's
    #: declaration site, never the site it is applied at.
    anchor: ReferenceResolver | None = None
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.name!r})'

    def resolved(self) -> Self:
        """Itself, or the definition an `!include` pointed at."""
        return self if self.link is None else self.link


def make_template_definition[T: TemplateDefinition](  # noqa: PLR0913 - the declaration, plus what differs per kind
    cls: type[T],
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    *,
    what: str,
    retain: Callable[[T, Node], Node] | None = None,
) -> T:
    """Decode one template declaration. Everything but `usage:` is kept as YAML.

    `retain` sees every other key and returns the node to keep for it, which is
    where a resource type checks its keys and chomps `?` off an optional method.
    The body is a fresh mapping, so a merge into it cannot reach the declaring
    document — the same reason stage 1 rebuilds an endpoint's body.
    """
    # A declaration an extension document added is that document's, body and
    # all; one it only amended stays the declaring document's (docs/19 § 5.3).
    location, scope = raml.document_site(value_node, location, raml.current_ctx())
    definition = cls(
        id=raml.next_id(),
        name=key_node.value if key_node is not None else '',
        location=location,
        anchor=scope.anchor,
        key_pos=(key_node if key_node is not None else value_node).position,
        value_pos=value_node.full_position,
    )
    if is_null(value_node):
        return definition
    if value_node.tag == TAG_INCLUDE:
        definition.link_uri = note_include_ref(raml, value_node, location)
        return definition
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error(f'{what} definition must be a mapping', location, value_node)

    kept: list[Node] = []
    for key, value in pairs(value_node):
        if key.value == FACET_USAGE:
            definition.usage = make_string_facet(raml, key, value, location)
        else:
            kept.append(key if retain is None else retain(definition, key))
            kept.append(value)
    if kept:
        definition.source = with_content(value_node, kept)
        definition.declared_variables, definition.variable_index = collect_variables_index(definition.source, location)
    return definition


def find_template_definition[T: TemplateDefinition](
    ref: DirectiveRef, lookup: Callable[[ReferenceResolver, str], T | None], *, what: str, info_key: str
) -> T:
    """Resolve a template name in the namespace of the document that wrote it.

    Lexical, with no application-site fallback: a name written inside a fragment
    resolves against that fragment's own declarations and `uses:` only, which
    is what keeps a typed fragment self-contained (docs/04 § 4).
    """
    anchor = ref.scope.anchor if ref.scope is not None else None
    if anchor is None:
        raise RamlError.new(f'no scope to resolve a {what} name in', ref.location, ref.value_pos)
    try:
        definition = lookup(anchor, ref.name)
    except LookupError as err:
        raise RamlError.wrap(f'get {what} definition', err, ref.location, ref.value_pos) from err
    if definition is None:
        raise RamlError.new(f'{what} not found', ref.location, ref.value_pos, info={info_key: ref.name})
    return definition


def check_parameters(
    declared: set[str],
    params: dict[str, Node],
    location: str,
    position: Position,
    *,
    required: set[str] | None = None,
) -> None:
    """Both directions: nothing supplied undeclared, nothing required unsupplied.

    The two sets differ for a resource type. Everything the template mentions is
    accepted as a parameter, but only what survives optional-method filtering is
    *required* — otherwise `/queues` would have to supply the `<<TextAboutPost>>`
    that only appears inside the `post?` it does not have (docs/08 § 3.1).
    For a trait the two coincide.

    Reserved parameters are always accepted and never required: the parser
    injects them at every application site.
    """
    accumulator = Accumulator()
    for name in params:
        if name not in RESERVED_PARAMETERS and name not in declared:
            accumulator.add(RamlError.new('unexpected parameter', location, position, info={'parameter': name}))
    for name in declared if required is None else required:
        if name not in RESERVED_PARAMETERS and name not in params:
            accumulator.add(RamlError.new('missing required parameter', location, position, info={'parameter': name}))
    accumulator.raise_if_any()


@dataclass(frozen=True, slots=True)
class VariableInfo:
    """One `<<name | !action>>` occurrence, as found in a scalar's literal text.

    `substring` is the exact `<<...>>` text as written, so substitution is
    `str.replace(substring, value, 1)` with no re-parsing (docs/08 § 5).
    """

    name: str
    substring: str
    actions: tuple[str, ...]


# -- the ten transform functions (docs/08 § 5) --------------------------------

# Splits on runs of space/underscore/hyphen; used by the two camelCase
# functions to find word boundaries.
_WORD_SPLIT: Final = re.compile(r'[ _\-]+')

# Zero-width match immediately before an internal capital letter: not at the
# start of the string, and not already preceded by the target separator (so a
# compound word that is already split is not split again). One compiled regex
# per separator (docs/12-performance.md § 2).
_BEFORE_CAP_UNDERSCORE: Final = re.compile(r'(?<!^)(?<!_)(?=[A-Z])')
_BEFORE_CAP_HYPHEN: Final = re.compile(r'(?<!^)(?<!-)(?=[A-Z])')

# `!singularize` and `!pluralize` need a dictionary, not a rule. go-raml uses
# `go-pluralize`, a port of Blake Embrey's JavaScript `pluralize`; `pluralizer`
# ports the same library, so the two agree by construction.
#
# These four registrations restore exact parity: the first three are what
# go-raml adds on top of the library, and `sms` is in go-pluralize's irregular
# table but not the Python port's. `tests/unit/data/pluralize_parity.tsv`
# guards the result (docs/08 § 5).
_IRREGULAR: Final[tuple[tuple[str, str], ...]] = (
    ('medium', 'media'),
    ('memorandum', 'memoranda'),
    ('vortex', 'vortices'),
    ('sms', 'sms'),
)


def _get_pluralizer() -> Pluralizer:
    global _PLURALIZER  # noqa: PLW0603 - one immutable rules engine, built lazily
    if _PLURALIZER is not None:
        return _PLURALIZER

    from pluralizer import Pluralizer  # noqa: PLC0415 - only two template actions need it

    engine = Pluralizer()
    for singular, plural in _IRREGULAR:
        # Registers both directions, and keeps the input's casing.
        engine.add_irregular_rule(singular, plural)
    _PLURALIZER = engine
    return _PLURALIZER


_PLURALIZER: Pluralizer | None = None


def _words(value: str) -> list[str]:
    return [word for word in _WORD_SPLIT.split(value) if word]


def _upper_camel_case(value: str) -> str:
    """`userId -> UserId`, `hello_world -> HelloWorld`.

    Each word's first letter is capitalised; the remainder of the word is left
    untouched. That is what makes `userId` (a single word: no separator to
    split on) become `UserId` rather than `Userid` — the spec's own example.
    """
    return ''.join(word[:1].upper() + word[1:] for word in _words(value))


def _lower_camel_case(value: str) -> str:
    """`UserId -> userId`, `hello_world -> helloWorld` (spec's own examples)."""
    words = _words(value)
    if not words:
        return ''
    head, *tail = words
    return head[:1].lower() + head[1:] + ''.join(word[:1].upper() + word[1:] for word in tail)


def _upper_underscore_case(value: str) -> str:
    return _BEFORE_CAP_UNDERSCORE.sub('_', value).upper()


def _lower_underscore_case(value: str) -> str:
    return _BEFORE_CAP_UNDERSCORE.sub('_', value).lower()


def _upper_hyphen_case(value: str) -> str:
    return _BEFORE_CAP_HYPHEN.sub('-', value).upper()


def _lower_hyphen_case(value: str) -> str:
    return _BEFORE_CAP_HYPHEN.sub('-', value).lower()


def _singularize(value: str) -> str:
    return _get_pluralizer().singular(value) if value else value


def _pluralize(value: str) -> str:
    return _get_pluralizer().plural(value) if value else value


#: Action name to transform.
TEMPLATE_ACTIONS: Final[dict[str, Callable[[str], str]]] = {
    '!uppercase': str.upper,
    '!lowercase': str.lower,
    '!uppercamelcase': _upper_camel_case,
    '!lowercamelcase': _lower_camel_case,
    '!upperunderscorecase': _upper_underscore_case,
    '!lowerunderscorecase': _lower_underscore_case,
    '!upperhyphencase': _upper_hyphen_case,
    '!lowerhyphencase': _lower_hyphen_case,
    '!singularize': _singularize,
    '!pluralize': _pluralize,
}

KNOWN_ACTIONS: Final = frozenset(TEMPLATE_ACTIONS)


def apply_template_action(value: str, action: str) -> str:
    """Apply one of the ten RAML transform functions to `value`.

    An unrecognised `action` returns `value` unchanged, as go-raml does. Every
    action maps `''` to `''`.
    """
    transform = TEMPLATE_ACTIONS.get(action)
    return value if transform is None else transform(value)


# -- parsing `<<name | !action | !action>>` (docs/08 § 5) ---------------------


def parse_template_variables(text: str, location: str) -> list[VariableInfo]:
    """Find every `<<...>>` placeholder in one scalar's literal text.

    Scans with `str.find`: the `| !action` grammar inside the braces reads more
    clearly as an explicit split than as one regex.
    """
    variables: list[VariableInfo] = []
    pos = 0
    while True:
        start = text.find('<<', pos)
        if start < 0:
            break
        content_start = start + 2
        end = text.find('>>', content_start)
        if end < 0:
            message = 'unclosed template variable'
            raise RamlError.new(message, location, kind=ErrorKind.PARSING)
        content = text[content_start:end]
        name, actions = _parse_variable_content(content, location)
        variables.append(VariableInfo(name, text[start : end + 2], tuple(actions)))
        pos = end + 2
    return variables


def _parse_variable_content(content: str, location: str) -> tuple[str, list[str]]:
    """Split `name | !action | !action` on `|`, stripping spaces per part."""
    name: str | None = None
    actions: list[str] = []
    for raw_part in content.split('|'):
        part = raw_part.strip()
        if not part:
            continue
        if name is None:
            if part[0] == '!':
                message = 'action without variable name'
                raise RamlError.new(message, location, kind=ErrorKind.PARSING)
            name = part
            continue
        if part[0] != '!':
            message = "invalid action, must start with '!'"
            raise RamlError.new(message, location, kind=ErrorKind.PARSING, info={'action': part})
        if part not in KNOWN_ACTIONS:
            message = 'unknown action'
            raise RamlError.new(message, location, kind=ErrorKind.PARSING, info={'action': part})
        actions.append(part)
    if name is None:
        message = 'missing variable name'
        raise RamlError.new(message, location, kind=ErrorKind.PARSING)
    return name, actions


# -- the variable index (docs/08 § 5) -----------------------------------------


def iter_nodes(node: Node) -> Iterator[Node]:
    """`node` and every descendant, depth-first and left to right.

    Iterative, so template depth cannot reach CPython's recursion limit.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.content))


def collect_variables_index(node: Node, location: str) -> tuple[set[str], VariableIndex]:
    """Scan a template body once: the declared variables, and where each occurs.

    Only `!!str` scalars are scanned. The result is keyed by the node itself,
    which `Node`'s identity hashing makes exact and which nothing downstream can
    invalidate, including the optional-method filtering of docs/08 § 3.1, which
    removes whole subtrees between this scan and its use.
    """
    declared_variables: set[str] = set()
    index: VariableIndex = {}
    for current in iter_nodes(node):
        if current.kind is not NodeKind.SCALAR or current.tag != TAG_STR:
            continue
        try:
            variables = parse_template_variables(current.value, location)
        except RamlError as err:
            message = 'parse template variables'
            raise RamlError.wrap(message, err, location, current.position, kind=ErrorKind.PARSING) from err
        if not variables:
            continue
        declared_variables.update(variable.name for variable in variables)
        index[current] = variables
    return declared_variables, index


def collect_required_variables(node: Node, index: VariableIndex) -> set[str]:
    """The variable names reachable from the subtree rooted at `node`.

    Which parameters an application must supply, asked *after* optional methods
    have been filtered out of the tree (docs/08 § 3.1, step 5): the spec's
    own `corpResource` declares `<<TextAboutPost>>` inside a `post?`, and
    `/queues` — which has no `post` — must not be required to supply it.
    """
    names: set[str] = set()
    for current in iter_nodes(node):
        variables = index.get(current)
        if variables:
            names.update(variable.name for variable in variables)
    return names


# -- substitution, recording where each value came from (docs/08 § 4.1) -------


def compile_source_provenance(
    node: Node,
    params: dict[str, Node],
    index: VariableIndex,
    caller_scope: ParseCtx,
    overlay: ProvenanceOverlay,
) -> Node:
    """Substitute `params` into a template body, marking what the caller supplied.

    Static content is left **unmarked** and therefore keeps the template's own
    declaration scope; only nodes that received a value are recorded, as
    `caller_scope`: static content resolves at its declaration and substituted
    values resolve at their application site (docs/08 § 4.1).

    Unchanged node pointers are shared with the input, so the result is still a
    valid key set for the overlay and for the merge that follows.
    """
    if node.kind is NodeKind.SCALAR:
        return _compile_scalar(node, params, index, caller_scope, overlay)

    modified = False
    content: list[Node] = []
    for child in node.content:
        compiled = compile_source_provenance(child, params, index, caller_scope, overlay)
        modified = modified or compiled is not child
        content.append(compiled)
    if not modified:
        # A container is structural: it keeps the enclosing scope, and reusing
        # it keeps every mark already recorded against it reachable.
        return node
    return with_content(node, content)


def _compile_scalar(
    node: Node,
    params: dict[str, Node],
    index: VariableIndex,
    caller_scope: ParseCtx,
    overlay: ProvenanceOverlay,
) -> Node:
    variables = index.get(node)
    if not variables:
        return node

    for variable in variables:
        param = params.get(variable.name)
        if param is not None and param.kind is not NodeKind.SCALAR:
            # A complex parameter replaces the node rather than being spliced
            # into its text. The subtree came from the caller, so it resolves
            # there — and the mark on its root stops `mark_graft` descending.
            overlay[param] = caller_scope
            return param

    text = node.value
    substituted = False
    for variable in variables:
        param = params.get(variable.name)
        if param is None:
            continue
        value = param.value
        for action in variable.actions:
            value = apply_template_action(value, action)
        text = text.replace(variable.substring, value, 1)
        substituted = True
    if not substituted:
        # An unsubstituted scalar is static: it keeps the declaration scope.
        return node

    compiled = with_value(node, text)
    overlay[compiled] = caller_scope
    return compiled
