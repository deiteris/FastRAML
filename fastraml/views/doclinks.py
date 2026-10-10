"""Links from prose to what the document declares (docs/16 § 11).

Every `description:`, and a documentation item's `content:`, is Markdown, and
a CommonMark reference link whose label the prose does not define is read as
a name: `` [`User`] ``, `[list them][GET /users]`, `[Getting started]`. This is
rustdoc's intra-doc link, with RAML's names in place of Rust paths.

**A name resolves where the prose was written.** Through the same lookup a
`type:` written in that file uses, `ReferenceResolver`, so its own declarations
and its `uses:` aliases are in scope and nothing here restates a RAML scoping
rule. A description a subtype inherits, or a trait contributes, keeps the file
that wrote it: the facet's `location` names it.

**Markdown decides what a link is.** The prose is parsed by markdown-it-py,
the port of the parser the viewer renders with, given a reference table that
answers every label it is asked about. So a label in a code span, in a fenced
block or under one the author defined is never a candidate, by CommonMark's
rules rather than by a pattern approximating them. The table answers with a
sentinel, which is how a link that resolves to nothing is told from a link
the author defined.

**Cost.** Prose with no `[` is not parsed. A text is parsed once per scope,
however many entities share it, and each name is looked up once per scope.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from fastraml.parser.documentation import DocumentationItem
from fastraml.parser.endpoints import EndPoint, Operation, Response
from fastraml.parser.fragments import APIFragment, Library
from fastraml.parser.security import SecuritySchemeDefinition
from fastraml.parser.source_ir import METHODS
from fastraml.records import record
from fastraml.types.base import BaseShape
from fastraml.types.examples import Example, examples_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from markdown_it import MarkdownIt

    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.registry import Raml
    from fastraml.types.base import ScalarFacet
    from fastraml.views.walk import Addresses

__all__ = ['DocLink', 'DocLinks', 'Entity', 'Kind', 'Outcome', 'Owner', 'Target', 'prose_of']


class Kind(StrEnum):
    """What a link can name: everything the tree addresses with an `id`."""

    ENDPOINT = 'endpoint'
    METHOD = 'method'
    TYPE = 'type'
    ANNOTATION_TYPE = 'annotation-type'
    SECURITY_SCHEME = 'security-scheme'
    DOCUMENTATION = 'documentation'


class Outcome(StrEnum):
    RESOLVED = 'resolved'
    UNRESOLVED = 'unresolved'
    #: More than one target: a type and a scheme of one name, or two spellings
    #: of one CommonMark label that name different things.
    AMBIGUOUS = 'ambiguous'
    #: A resource, method or documentation item, named in prose a library
    #: wrote. A library is used by APIs it does not know.
    OUT_OF_SCOPE = 'out-of-scope'


#: What a link can resolve to.
type Entity = EndPoint | Operation | BaseShape | SecuritySchemeDefinition | DocumentationItem
#: What prose belongs to: where its names resolve, if the file that wrote it
#: has no namespace of its own.
type Owner = Entity | Response | APIFragment | Example


@record
class Target:
    kind: Kind
    entity: Entity
    #: The address the walk gave it. Only an addressed entity is a target.
    address: str


@record
class DocLink:
    """One label of one text, and what it names."""

    #: The label as CommonMark matches it: case-folded, whitespace collapsed.
    #: What a renderer's reference table is keyed by.
    label: str
    #: The first spelling of it in the text, between its brackets.
    written: str
    #: Written as a link and not merely in brackets: with backticks, or as the
    #: label of a full reference, `[text][label]`. Only these are reported.
    explicit: bool
    outcome: Outcome
    #: One when resolved, each candidate when ambiguous, otherwise none.
    targets: tuple[Target, ...] = ()

    @property
    def target(self) -> Target | None:
        return self.targets[0] if self.outcome is Outcome.RESOLVED else None


#: A `kind@` prefix, which picks one namespace where a name is in several.
_PREFIXES: Final = {
    'type': Kind.TYPE,
    'annotationType': Kind.ANNOTATION_TYPE,
    'securityScheme': Kind.SECURITY_SCHEME,
    'documentation': Kind.DOCUMENTATION,
}
_PREFIXED: Final = re.compile(r'([A-Za-z]+)@(.+)', re.DOTALL)
#: `(rateLimit)`, as an annotation type is written where it is applied.
_ANNOTATION: Final = re.compile(r'\((.+)\)', re.DOTALL)
_METHOD: Final = re.compile(r'([A-Za-z]+) (/\S*)')

#: Every bracketed run that holds no bracket: a superset of the labels
#: CommonMark finds, kept with the text it was written as. Its raw spelling is
#: what resolves, since the label CommonMark reports is case-folded.
_BRACKETED: Final = re.compile(r'(?<!\\)\[((?:[^\[\]\\]|\\.)+)\]', re.DOTALL)
#: A CommonMark backslash escape: `\` before ASCII punctuation.
_ESCAPE: Final = re.compile(r'\\([!-/:-@\[-`{-~])')

#: Prefixes the href the reference table answers an undefined label with. No
#: href an author defines can start with it: markdown-it percent-encodes a
#: control character in a destination.
_SENTINEL: Final = '\x00'

type _Spellings = dict[str, list[tuple[str, bool]]]


class _Undefined(dict[str, Any]):
    """The reference table: the author's definitions, and every other label.

    Every label markdown-it looks up resolves, so each one is a link token,
    carrying its label behind `_SENTINEL`. A definition the prose writes is
    stored by the parser and wins, as it does in a renderer.
    """

    __slots__ = ()

    def get(self, key: str, default: object = None) -> Any:  # noqa: ARG002 - dict's signature
        found = super().get(key)
        return found if found is not None else {'href': _SENTINEL + key, 'title': ''}


_PARSER: list[tuple[MarkdownIt, Callable[[str], str]]] = []


def _markdown() -> tuple[MarkdownIt, Callable[[str], str]]:
    """The parser and CommonMark's label normalisation, imported on first use.

    Imported here, not at the top: it costs tens of milliseconds, and a
    document whose prose holds no `[` never needs it. `js-default` without
    HTML is the viewer's configuration (`viewer/src/components/markdown.tsx`):
    the two must agree on what is a link.
    """
    if not _PARSER:
        from markdown_it import MarkdownIt  # noqa: PLC0415 - see the docstring
        from markdown_it.common.utils import normalizeReference  # noqa: PLC0415 - see the docstring

        _PARSER.append((MarkdownIt('js-default', {'html': False}), normalizeReference))
    return _PARSER[0]


def _clean(raw: str) -> str:
    """A spelling as a name: escapes undone, backticks dropped, whitespace collapsed."""
    return ' '.join(_ESCAPE.sub(r'\1', raw).replace('`', '').split())


def prose_of(entity: object) -> Iterator[tuple[ScalarFacet[str], Owner]]:
    """The Markdown `entity` carries, each text with the entity it belongs to.

    An example's description and a documentation item's content belong to
    the example and the item, which is how the tree reaches them.
    """
    if isinstance(entity, BaseShape):
        if entity.description is not None:
            yield entity.description, entity
        for example in examples_of(entity):
            if example.description is not None:
                yield example.description, example
    elif isinstance(entity, SecuritySchemeDefinition):
        declared = entity.resolved()
        if declared.description is not None:
            yield declared.description, entity
    elif isinstance(entity, (EndPoint, Operation, Response, APIFragment)):
        if entity.description is not None:
            yield entity.description, entity
    elif isinstance(entity, DocumentationItem) and entity.content is not None:
        yield entity.content, entity


class DocLinks:
    """The links of one parse, resolved against one address map.

    Built once per projection or lint run; it caches by scope, so one instance
    is what makes a description shared by many entities cost one parse.
    """

    __slots__ = ('_addresses', '_entry', '_names', '_raml', '_texts', '_titles')

    def __init__(self, raml: Raml, addresses: Addresses) -> None:
        self._raml = raml
        self._addresses = addresses.of
        self._entry = raml.resolver_at(raml.location)
        self._texts: dict[tuple[int | None, str], tuple[DocLink, ...]] = {}
        self._names: dict[tuple[int | None, str], tuple[tuple[Target, ...], bool]] = {}
        entry = raml.entry_point
        documentation = entry.documentation if isinstance(entry, APIFragment) else []
        self._titles: dict[str, list[DocumentationItem]] = {}
        for item in documentation:
            if item.title is not None:
                self._titles.setdefault(' '.join(item.title.value.split()), []).append(item)

    def __repr__(self) -> str:
        return f'<DocLinks texts={len(self._texts)} names={len(self._names)}>'

    def scope_of(self, facet: ScalarFacet[str], owner: Owner) -> ReferenceResolver | None:
        """The namespace `facet`'s names resolve in.

        The file that wrote the text, which an inherited or contributed
        description keeps. Failing that, which a headerless include or a
        JSON Schema's own description does, the owner's: a shape's anchor, or
        the file a resource, method or item resolves in. Then the entry's.
        """
        found = self._raml.resolver_at(facet.location)
        if found is None:
            found = owner.anchor if isinstance(owner, BaseShape) else self._raml.resolver_at(owner.location)
        return found if found is not None else self._entry

    def links(self, facet: ScalarFacet[str] | None, owner: Owner) -> tuple[DocLink, ...]:
        """Every label `facet`'s text links, each once, in the order written."""
        if facet is None or not isinstance(facet.value, str) or '[' not in facet.value:
            return ()
        text = facet.value
        scope = self.scope_of(facet, owner)
        key = (None if scope is None else scope.id, text)
        found = self._texts.get(key)
        if found is None:
            found = self._texts[key] = self._parse(text, scope)
        return found

    def _parse(self, text: str, scope: ReferenceResolver | None) -> tuple[DocLink, ...]:
        spellings = self._spellings(text)
        if not spellings:
            return ()
        markdown, _ = _markdown()
        labels: dict[str, None] = {}
        for token in markdown.parse(text, {'references': _Undefined()}):
            for child in token.children or ():
                if child.type == 'link_open':
                    href = child.attrs.get('href')
                    if isinstance(href, str) and href.startswith(_SENTINEL):
                        labels.setdefault(href[len(_SENTINEL) :])
        return tuple(self._link(label, spellings.get(label, []), scope) for label in labels)

    @staticmethod
    def _spellings(text: str) -> _Spellings:
        """Each bracketed run, by the label CommonMark would match it as.

        With whether it follows a `]`, which makes it a full reference's label.
        """
        _, normalize = _markdown()
        out: _Spellings = {}
        for match in _BRACKETED.finditer(text):
            start = match.start()
            out.setdefault(normalize(match[1]), []).append((match[1], start > 0 and text[start - 1] == ']'))
        return out

    def _link(self, label: str, spellings: list[tuple[str, bool]], scope: ReferenceResolver | None) -> DocLink:
        written = spellings[0][0] if spellings else label
        explicit = any('`' in raw or full for raw, full in spellings)
        targets: dict[str, Target] = {}
        ambiguous = outside = False
        for name in dict.fromkeys(_clean(raw) for raw, _ in spellings):
            if not name:
                continue
            found, beyond = self._resolve(name, scope)
            outside = outside or beyond
            ambiguous = ambiguous or len(found) > 1
            for target in found:
                targets.setdefault(target.address, target)
        if ambiguous or len(targets) > 1:
            return DocLink(label, written, explicit, Outcome.AMBIGUOUS, tuple(targets.values()))
        if targets:
            return DocLink(label, written, explicit, Outcome.RESOLVED, tuple(targets.values()))
        return DocLink(label, written, explicit, Outcome.OUT_OF_SCOPE if outside else Outcome.UNRESOLVED)

    def _resolve(self, name: str, scope: ReferenceResolver | None) -> tuple[tuple[Target, ...], bool]:
        """What `name` names in `scope`, and whether an API name was out of it."""
        key = (None if scope is None else scope.id, name)
        found = self._names.get(key)
        if found is None:
            found = self._names[key] = self._lookup(name, scope)
        return found

    def _lookup(self, name: str, scope: ReferenceResolver | None) -> tuple[tuple[Target, ...], bool]:
        kinds: frozenset[Kind] | None = None
        prefixed = _PREFIXED.fullmatch(name)
        annotation = _ANNOTATION.fullmatch(name)
        if prefixed is not None and prefixed[1] in _PREFIXES:
            kinds, name = frozenset({_PREFIXES[prefixed[1]]}), prefixed[2].strip()
        elif annotation is not None:
            kinds, name = frozenset({Kind.ANNOTATION_TYPE}), annotation[1].strip()
        declared = [] if scope is None else self._declared(name, kinds, scope)
        api = self._api_names(name, kinds)
        if isinstance(scope, Library):
            return tuple(declared), bool(api) and not declared
        return (*api, *declared), False

    def _api_names(self, name: str, kinds: frozenset[Kind] | None) -> list[Target]:
        """The resources, methods and documentation items `name` names."""
        found: list[Target] = []
        if kinds is None and name.startswith('/'):
            self._add(found, Kind.ENDPOINT, self._raml.endpoints.get(name))
        method = _METHOD.fullmatch(name) if kinds is None else None
        if method is not None and method[1].lower() in METHODS:
            endpoint = self._raml.endpoints.get(method[2])
            if endpoint is not None:
                self._add(found, Kind.METHOD, endpoint.operations.get(method[1].lower()))
        if kinds is None or Kind.DOCUMENTATION in kinds:
            for item in self._titles.get(name, ()):
                self._add(found, Kind.DOCUMENTATION, item)
        return found

    def _declared(self, name: str, kinds: frozenset[Kind] | None, scope: ReferenceResolver) -> list[Target]:
        """The declarations `name` names, as a `type:` or `securedBy:` written in `scope` would.

        An annotation type is looked up through the resolver that falls back
        to `types` (docs/04 § 3), so only what it finds that is one counts.
        """
        found: list[Target] = []
        if kinds is None or Kind.TYPE in kinds:
            self._add(found, Kind.TYPE, _found(scope.reference_type, name))
        if kinds is None or Kind.ANNOTATION_TYPE in kinds:
            shape = _found(scope.reference_annotation_type, name)
            if shape is not None and shape.is_annotation_type:
                self._add(found, Kind.ANNOTATION_TYPE, shape)
        if kinds is None or Kind.SECURITY_SCHEME in kinds:
            self._add(found, Kind.SECURITY_SCHEME, _found(scope.security_scheme_definition, name))
        return found

    def _add(self, found: list[Target], kind: Kind, entity: Entity | None) -> None:
        """`entity` as a target, if it is one: the walk addressed it."""
        if entity is None:
            return
        address = self._addresses.get(entity.id)
        if address is not None and all(target.address != address for target in found):
            found.append(Target(kind, entity, address))


def _found[T](lookup: Callable[[str], T], name: str) -> T | None:
    try:
        return lookup(name)
    except LookupError:
        return None
