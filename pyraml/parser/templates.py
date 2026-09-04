"""Template variables: `<<name | !action | !action>>`.

Implements docs/08-templates-and-endpoints.md section 7. A resource-type or
trait body is scanned **once**, at declaration time, producing an index that
substitution looks up without re-scanning the same strings at every application
site — a resource type applied to 200 endpoints scans its body once.

The index is keyed by **node identity**, not by position. An earlier design
numbered the nodes in a preorder walk, which made every consumer depend on two
walks agreeing; go-raml still does, and the walks do not agree, because step 3
of section 5.1 filters optional methods out of the tree before step 4 re-reads
the index. Identity keys survive that filtering by construction, and `Node`
already hashes by identity for the provenance overlay's sake. See section 7.1.

See ../../CLAUDE.md and docs/12-performance.md section 12: no per-character
Python loops. The recasing functions below use `str.split`/slicing and
precompiled regexes instead of go-raml's byte loops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import inflect

from pyraml.errors import ErrorKind, RamlError
from pyraml.yamlnode import TAG_STR, Node, NodeKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyraml.parser.structural_merge import ProvenanceOverlay
    from pyraml.registry import ParseCtx

__all__ = [
    'KNOWN_ACTIONS',
    'TEMPLATE_ACTIONS',
    'VariableIndex',
    'VariableInfo',
    'apply_template_action',
    'collect_required_variables',
    'collect_variables_index',
    'compile_source_provenance',
    'iter_nodes',
    'parse_template_variables',
]

#: What one scan of a template body produces: every `<<...>>` bearing scalar,
#: keyed by the node itself.
type VariableIndex = dict[Node, list[VariableInfo]]


@dataclass(frozen=True, slots=True)
class VariableInfo:
    """One `<<name | !action>>` occurrence, as found in a scalar's literal text.

    `substring` is the exact `<<...>>` text as written, so substitution is
    `str.replace(substring, value, 1)` with no re-parsing (docs/08 section 7.1).
    """

    name: str
    substring: str
    actions: tuple[str, ...]


# -- section 7.3: the ten transform functions --------------------------------

# Splits on runs of space/underscore/hyphen; used by the two camelCase
# functions to find word boundaries.
_WORD_SPLIT: Final = re.compile(r'[ _\-]+')

# Zero-width match immediately before an internal capital letter: not at the
# start of the string, and not already preceded by the target separator (so a
# compound word that is already split is not split again). One compiled regex
# per separator, per docs/12-performance.md section 12.
_BEFORE_CAP_UNDERSCORE: Final = re.compile(r'(?<!^)(?<!_)(?=[A-Z])')
_BEFORE_CAP_HYPHEN: Final = re.compile(r'(?<!^)(?<!-)(?=[A-Z])')

# The RAML TCK fixtures depend on these three inflecting; general pluralisers
# treat `medium` as uncountable and have no rule for the other two (go-raml
# trait.go carries the identical override list).
_IRREGULAR: Final[tuple[tuple[str, str], ...]] = (
    ('medium', 'media'),
    ('memorandum', 'memoranda'),
    ('vortex', 'vortices'),
)
_PLURAL_OF: Final[dict[str, str]] = dict(_IRREGULAR)
_SINGULAR_OF: Final[dict[str, str]] = {plural: singular for singular, plural in _IRREGULAR}

_INFLECT: Final = inflect.engine()


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
    if not value:
        return value
    if value in _SINGULAR_OF:
        return _SINGULAR_OF[value]
    result = _INFLECT.singular_noun(value)
    return result or value


def _pluralize(value: str) -> str:
    if not value:
        return value
    if value in _PLURAL_OF:
        return _PLURAL_OF[value]
    return _INFLECT.plural(value)


#: Module-level dispatch table (docs/12-performance.md section 18): every
#: dispatch that would otherwise be an `if action == "...":` chain is a table.
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

    An unrecognised `action` returns `value` unchanged, mirroring go-raml's
    `applyTemplateAction` default case. Every action returns the empty string
    unchanged on empty input (explicit for `!singularize`/`!pluralize`;
    incidental but true for the other eight).
    """
    transform = TEMPLATE_ACTIONS.get(action)
    return value if transform is None else transform(value)


# -- section 7.2: parsing `<<name | !action | !action>>` ---------------------


def parse_template_variables(text: str, location: str) -> list[VariableInfo]:
    """Find every `<<...>>` placeholder in one scalar's literal text.

    Scans with `str.find` rather than a regex on purpose (docs/12-performance
    section 12): the `| !action` grammar inside the braces is easier to read
    as an explicit split than as a single regex, and `<<`/`>>` scanning gains
    nothing measurable from one.
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


# -- section 7.1: the variable index ------------------------------------------


def iter_nodes(node: Node) -> Iterator[Node]:
    """`node` and every descendant, depth-first and left to right.

    One helper rather than a copy of the traversal in each consumer. Iterative,
    so template depth cannot reach CPython's recursion limit
    (docs/12-performance.md section 14).
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
    invalidate — including the optional-method filtering of section 5.1, which
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
    have been filtered out of the tree (docs/08 section 5.1 step 4): the spec's
    own `corpResource` declares `<<TextAboutPost>>` inside a `post?`, and
    `/queues` — which has no `post` — must not be required to supply it.
    """
    names: set[str] = set()
    for current in iter_nodes(node):
        variables = index.get(current)
        if variables:
            names.update(variable.name for variable in variables)
    return names


# -- sections 6.2 and 7: substitution, recording where each value came from ----


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
    `caller_scope`. That is the whole of decision D2 (docs/08 section 6.2):
    static goes to the declaration site, dynamic to the application site.

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
    return Node(node.kind, node.tag, node.value, content, node.line, node.column, node.end_line, node.end_column)


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

    compiled = Node(node.kind, node.tag, text, None, node.line, node.column, node.end_line, node.end_column)
    overlay[compiled] = caller_scope
    return compiled
