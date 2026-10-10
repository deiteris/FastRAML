"""Author-facing hover: source explanations and compact model summaries.

The service composes the parser's source grammar, occurrences and authorship
view. It binds no name and runs no pass (docs/21 § 4.2). Indices and source
keys are built once per snapshot, so successive hovers do not rescan the whole
model. Formatted subjects and declaration hints are cached.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from fastraml.errors import RamlError
from fastraml.parser.fragments import APIFragment, LibraryLink, every_declaration
from fastraml.parser.includes import is_json_ref
from fastraml.parser.security import SecuritySchemeDefinition
from fastraml.parser.syntax import METHODS, NAME_MAPS, Key, Site, child_site, fragment_site, keys
from fastraml.parser.templates import TemplateDefinition
from fastraml.service.datahover import DataHover, DataRoot, DataTarget
from fastraml.service.hoverdocs import BUILTINS, METHOD_DOCS, field_doc
from fastraml.service.inlays import (
    LABEL_LIMIT,
    Hint,
    Part,
    constraint_details,
    constraint_summary,
    type_label,
    underlying_type,
)
from fastraml.service.source import original_tree
from fastraml.service.text import Lines
from fastraml.types.base import BaseShape, Parameter, facets_of
from fastraml.types.complex_ import ObjectShape, UnknownShape
from fastraml.types.examples import examples_of
from fastraml.types.expressions import Array, Optional_, Primitive, Union, parse_expression
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import DATETIME_FORMATS, INTEGER_FORMATS, NUMBER_FORMATS
from fastraml.types.shape import TYPE_SPECIFIC_FACETS
from fastraml.uris import relative_to
from fastraml.views import authored
from fastraml.views.occurrences import DECLARATION_KINDS, Kind, Link, Occurrence, Occurrences, Role
from fastraml.views.render import type_name
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, backend_name, compose, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from weakref import ReferenceType

    from fastraml.parser.endpoints import Body, EndPoint, Operation, Response
    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.service.source import Sources
    from fastraml.types.base import ScalarFacet
    from fastraml.types.expressions import ExprCache, RdtNode

__all__ = ['Hover']

_SPEC: Final = 'https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md'
_INLINE_LIMIT: Final = 160
_TICKS: Final = re.compile(r'`+')
_STATUS: Final = re.compile(r'[1-5][0-9]{2}')
_OPERATORS: Final = re.compile(r'[\[\]|?]')
_AMBIGUOUS: Final = '*Several typed declarations describe this token; no single alternative is assumed.*'

type _Entity = BaseShape | LibraryLink | TemplateDefinition | SecuritySchemeDefinition


@dataclass(frozen=True, slots=True, eq=False)
class _Subject:
    name: str
    role: str
    entity: _Entity
    required: bool | None = None


def _code(text: str) -> str:
    """An inline code span that cannot be closed by the author's text."""
    if len(text) > _INLINE_LIMIT:
        text = text[: _INLINE_LIMIT - 1] + '…'
    fence = '`' * (max((len(run) for run in _TICKS.findall(text)), default=0) + 1)
    return f'{fence} {text} {fence}' if fence != '`' else f'`{text}`'


class Hover:
    """Hover information for one snapshot; all positions are source positions."""

    __slots__ = (
        '_ambiguous_sites',
        '_contexts',
        '_contexts_ready',
        '_data',
        '_data_descriptions',
        '_descriptions',
        '_includes',
        '_inlay_declarations',
        '_key_starts',
        '_keys',
        '_mappings',
        '_nodes',
        '_occurrences',
        '_operations',
        '_raml',
        '_resources',
        '_responses',
        '_root',
        '_sites',
        '_source_backend',
        '_source_builtins',
        '_source_generation',
        '_sources',
        '_subjects',
    )

    def __init__(  # noqa: PLR0913 - the snapshot's borrowed source owner and composition policy
        self,
        raml: Raml,
        root: str,
        occurrences: Occurrences,
        *,
        sources: ReferenceType[Sources] | None = None,
        source_generation: int = 0,
        source_backend: str = '',
    ) -> None:
        self._raml = raml
        self._root = root.rpartition('/')[0] + '/'
        self._occurrences = occurrences
        self._sources = sources
        self._source_generation = source_generation
        self._source_backend = source_backend
        self._subjects: dict[int, _Subject] = {}
        self._sites: dict[tuple[str, int, int], _Subject] = {}
        self._mappings: dict[tuple[str, int, int], _Subject] = {}
        self._keys: dict[str, list[Key]] = {}
        self._key_starts: dict[str, list[tuple[int, int]]] = {}
        self._resources: dict[tuple[str, int, int], EndPoint] = {}
        self._operations: dict[tuple[str, int, int], tuple[str, Operation]] = {}
        self._responses: dict[tuple[str, int, int], Response] = {}
        self._source_builtins: dict[str, Occurrences] = {}
        self._ambiguous_sites: set[tuple[str, int, int]] = set()
        self._nodes: dict[str, Node | None] = {}
        self._contexts: dict[str, set[tuple[Site, str]]] = {}
        for uri, fragment in raml.fragments.items():
            site = fragment_site(fragment.kind)
            if fragment.kind == 'DataType' and is_json_ref(uri):
                site = Site.DATA
            self._contexts.setdefault(uri.split('#', 1)[0], set()).add((site, ''))
        self._contexts_ready = False
        self._includes = {
            (ref.source_uri, ref.position.line, ref.position.column): ref.abs_uri.split('#', 1)[0]
            for refs in raml.include_refs.values()
            for ref in refs
        }
        self._descriptions: dict[_Subject, str] = {}
        self._data_descriptions: dict[DataTarget, str] = {}
        self._inlay_declarations: dict[str, tuple[list[Hint], list[tuple[int, int]]]] = {}
        self._index()
        self._data = DataHover(self._data_roots())

    def _put(self, subject: _Subject) -> None:
        entity = subject.entity
        self._subjects[entity.id] = subject
        key = entity.key_pos
        if key.is_known:
            site = (entity.location, key.line, key.column)
            previous = self._sites.setdefault(site, subject)
            if previous.entity.id != entity.id:
                self._ambiguous_sites.add(site)
        value = entity.value_pos
        if value.is_known:
            self._mappings.setdefault((entity.location, value.line, value.column), subject)

    def _index(self) -> None:
        raml = self._raml
        for key, name, entity in every_declaration(raml):
            kind = DECLARATION_KINDS[key]
            self._put(_Subject(name, _role(kind), entity))
        for fragment in raml.fragments.values():
            for name, link in fragment.uses.items():
                self._put(_Subject(name, 'library namespace', link))
        for base in raml.shapes:
            self._subjects.setdefault(base.id, _Subject(base.name or '<anonymous>', 'type declaration', base))
            if isinstance(base.shape, ObjectShape):
                for name, prop in authored.properties(base):
                    self._put(_Subject(name, 'object property', prop.base, prop.required))
                for name, pattern in authored.pattern_properties(base):
                    self._put(_Subject(f'/{name}/', 'pattern property', pattern.base))
            for name, prop in authored.facets(base):
                self._put(_Subject(name, 'custom facet declaration', prop.base, prop.required))
        self._index_endpoints()

    def _index_endpoints(self) -> None:
        api = self._raml.entry_point
        if isinstance(api, APIFragment):
            self._parameters(api.base_uri_parameters.values(), binding='base URI')
        for endpoint in self._raml.endpoints.values():
            self._resources.setdefault((endpoint.location, endpoint.key_pos.line, endpoint.key_pos.column), endpoint)
            self._parameters(endpoint.uri_parameters.values())
            for operation in endpoint.operations.values():
                self._operations.setdefault(
                    (operation.location, operation.key_pos.line, operation.key_pos.column),
                    (endpoint.full_uri, operation),
                )
                if operation.request is not None:
                    request = operation.request
                    self._parameters((*request.query_parameters.values(), *request.headers.values()))
                    self._bodies(request.bodies.values(), 'request body')
                for response in operation.responses.values():
                    self._responses.setdefault(
                        (response.location, response.key_pos.line, response.key_pos.column), response
                    )
                    self._parameters(response.headers.values())
                    self._bodies(response.bodies.values(), 'response body')

    def _parameters(self, parameters: Iterable[Parameter], *, binding: str | None = None) -> None:
        for param in parameters:
            if not param.synthesized:
                self._put(_Subject(param.name, f'{binding or param.binding} parameter', param.base, param.required))

    def _bodies(self, bodies: Iterable[Body], role: str) -> None:
        for body in bodies:
            if body.shape is not None:
                self._put(_Subject(body.media_type, role, body.shape))

    def at(self, uri: str, line: int, column: int) -> tuple[str, Position] | None:
        """Explain the precise token under the cursor, or return no answer."""
        key = self._key_at(uri, line, column)
        if key is not None:
            text = self._key_doc(uri, key)
            if text is not None:
                return text, key.node.position
        found = self._occurrences.at(uri, line, column)
        if found:
            occurrence = found[0]
            text = self._occurrence_doc(occurrence)
            if text is not None:
                if key is not None and key.annotation:
                    text = '**Annotation application**\n\nThe summary describes its value type.\n\n' + text
                if len(found) > 1:
                    text += '\n\n*This token has several materializations; the summary shows the first in this parse.*'
                return text, occurrence.span
        data = self._data.at(uri, line, column)
        if data:
            return self._data_docs(data), data[0].span
        primitive = self._source_builtins[uri].at(uri, line, column)
        if primitive:
            return self._builtin(primitive[0].written), primitive[0].span
        if key is not None:
            subject = self._sites.get((uri, key.node.line, key.node.column))
            text = self._describe(subject) if subject is not None else _unbound_doc(key)
            return None if text is None else (text, key.node.position)
        return None

    def _occurrence_doc(self, occurrence: Occurrence) -> str | None:
        if occurrence.role is Role.BUILTIN:
            return self._builtin(occurrence.written)
        if isinstance(occurrence, Link):
            return f'**Included or imported file**\n\n{_code(relative_to(occurrence.resolved, self._root))}'
        subject = self._subjects.get(occurrence.target) if occurrence.target is not None else None
        return None if subject is None else self._describe(subject)

    def data_at(self, uri: str, line: int, column: int) -> list[DataTarget]:
        """Bound data targets shared with go-to-definition."""
        return self._data.at(uri, line, column)

    def inlay_hints(self, uri: str, span: Position) -> list[Hint]:
        """Present hidden type/facet facts using the existing authored/data indices."""
        if uri not in self._keys:
            self._source_keys(uri)
        if uri not in self._inlay_declarations:
            declared = [hint for source in self._keys[uri] for hint in self._declaration_hints(uri, source)]
            declared.sort(key=lambda hint: (hint.position.line, hint.position.column))
            self._inlay_declarations[uri] = declared, [(hint.position.line, hint.position.column) for hint in declared]
        declared, starts = self._inlay_declarations[uri]
        begin = bisect_left(starts, (span.line, span.column))
        end = bisect_right(starts, (span.end_line, span.end_column))
        hints = declared[begin:end]
        candidates: dict[Position, dict[str, list[DataTarget]]] = {}
        for target in self._data.in_range(uri, span):
            if isinstance(target.base.shape, UnknownShape) or target.base.id in self._raml.broken:
                continue
            key = target.span
            position = key.shifted(key.end_column - key.column, 0)
            if not span.holds(position.line, position.column):
                continue
            candidates.setdefault(position, {}).setdefault(type_name(target.base), []).append(target)
        for position, types in candidates.items():
            parts = [Part('[')]
            targets = [target for alternatives in types.values() for target in alternatives]
            length = 0
            for index, (name, alternatives) in enumerate(types.items()):
                label = type_label(name)
                if length and length + len(label) + 3 > LABEL_LIMIT:
                    remaining = [target for group in list(types.values())[index:] for target in group]
                    parts.append(Part(' | …', tooltip=self._data_docs(remaining, warn=False)))
                    break
                if length:
                    parts.append(Part(' | '))
                    length += 3
                definitions = {
                    (target.base.location, target.base.key_pos.within(target.base.name or target.name))
                    for target in alternatives
                    if target.base.key_pos.is_known
                }
                definition = (
                    next(iter(definitions))
                    if len(definitions) == 1 and all(target.base.key_pos.is_known for target in alternatives)
                    else None
                )
                parts.append(
                    Part(
                        label,
                        definition[0] if definition is not None else None,
                        definition[1] if definition is not None else None,
                        tooltip=self._data_docs(alternatives, warn=False),
                    )
                )
                length += len(label)
            parts.append(Part(']'))
            hints.append(
                Hint(position, tuple(parts), self._data_docs(targets), note=_AMBIGUOUS if len(targets) > 1 else None)
            )
        return sorted(hints, key=lambda hint: (hint.position.line, hint.position.column, hint.label))

    def _declaration_hints(self, uri: str, source: Key) -> Iterator[Hint]:
        subject = self._sites.get((uri, source.node.line, source.node.column))
        if subject is None or not isinstance(subject.entity, BaseShape):
            return
        base = subject.entity
        if base.id in self._raml.broken or isinstance(base.shape, UnknownShape):
            return
        type_written = source.value.kind is NodeKind.SEQUENCE or (
            source.value.kind is NodeKind.MAPPING
            and any(key.value in ('type', 'schema') for key, _ in pairs(source.value))
        )
        if base.type_expr is None and base.type and not type_written:
            key = source.node.position
            yield Hint(
                key.shifted(key.end_column - key.column, 0),
                (Part('[' + type_label(underlying_type(base)) + ']'),),
                self._describe(subject),
            )
        inherited = {
            name: facet.value
            for name, facet in facets_of(base.shape)
            if self._raml.unwrapped
            and facet.key_pos.is_known
            and not authored.wrote(base, facet.location, facet.key_pos)
        }
        expression = base.type_expr
        if (
            expression is not None
            and expression.position.is_known
            and any(ref.resolved is not None for ref in base.type_expr_refs)
        ):
            key = expression.position
            name = underlying_type(base)
            if key.line == key.end_line and authored.wrote(base, uri, key) and name != expression.value:
                label = type_label(name)
                summary = constraint_summary(inherited, limit=LABEL_LIMIT - len(label) - 2)
                if summary is not None:
                    label += '; ' + summary
                tooltip = self._describe(subject)
                if inherited:
                    tooltip += '\n\n' + constraint_details(inherited)
                yield Hint(
                    key.shifted(key.end_column - key.column, 0),
                    (Part('[' + label + ']'),),
                    tooltip,
                )

    def _node(self, uri: str) -> Node | None:
        if uri in self._nodes:
            return self._nodes[uri]
        node = self._raml.source_nodes.get(uri) or self._raml.include_nodes.get(uri)
        text = self._raml.source_texts.get(uri)
        shared = None if self._sources is None else self._sources()
        if node is not None and original_tree(uri, node) is None:
            self._nodes[uri] = node
            return node
        if text is not None and shared is not None:
            node = shared.node(
                uri,
                text,
                max_depth=self._raml.max_depth,
                generation=self._source_generation,
                original=node if self._source_backend == backend_name() else None,
            )
        elif node is None and text is not None:
            with suppress(RamlError):
                node = compose(text, uri=uri, max_depth=self._raml.max_depth)
        self._nodes[uri] = node
        return node

    def _discover_contexts(self) -> None:
        """Literal includes inherit their receiving syntax; typed fragments do not."""
        self._contexts_ready = True
        typed = set(self._contexts)
        pending = [(uri, site, table) for uri, contexts in self._contexts.items() for site, table in contexts]
        while pending:
            uri, site, table = pending.pop()
            if site in (Site.DATA, Site.APPLICATION, Site.GENERIC):
                continue
            root = self._node(uri)
            if root is None:
                continue
            for key in keys(root, site, table=table):
                if key.value.tag != TAG_INCLUDE:
                    continue
                target = self._includes.get((uri, key.value.line, key.value.column))
                if target is None or target in typed:
                    continue
                child = child_site(key.site, key.node.value)
                child_table = key.node.value if child in NAME_MAPS else key.table
                context = (child, child_table)
                contexts = self._contexts.setdefault(target, set())
                if context not in contexts:
                    contexts.add(context)
                    pending.append((target, child, child_table))

    def _source_keys(self, uri: str) -> None:
        if uri not in self._contexts and not self._contexts_ready:
            self._discover_contexts()
        contexts = self._contexts.get(uri, {(Site.DATA, '')})
        context, table = next(iter(contexts)) if len(contexts) == 1 else (Site.DATA, '')
        if context in (Site.DATA, Site.APPLICATION, Site.GENERIC):
            self._keys[uri] = []
            self._key_starts[uri] = []
            self._source_builtins[uri] = Occurrences((), ())
            return
        node = self._node(uri)
        found = (
            []
            if node is None
            else sorted(keys(node, context, table=table), key=lambda key: (key.node.line, key.node.column))
        )
        self._keys[uri] = found
        self._key_starts[uri] = [(key.node.line, key.node.column) for key in found]
        lines = Lines(self._raml.source_texts.get(uri, ''))
        primitives = []
        cache: ExprCache = {}
        for key in found:
            value = key.type_value()
            if value is None:
                continue
            if value.tag != TAG_STR:
                continue
            tokens = []
            if value.value in BUILTINS:
                tokens.append((value.value, 0))
            elif _OPERATORS.search(value.value):
                with suppress(RamlError):
                    cached = self._raml.expr_cache.get(value.value)
                    if cached is not None:
                        cache[value.value] = cached
                    tree = parse_expression(value.value, cache)
                    tokens.extend((token.name, token.col) for token in _primitives(tree))
            for name, offset in tokens:
                span = value.position.within(value.value).shifted(offset, len(name))
                if lines.line(span.line)[span.column - 1 : span.end_column - 1] == name:
                    primitives.append(
                        Occurrence(uri, span.line, span.column, span.end_column, Role.BUILTIN, Kind.TYPE, None, name)
                    )
        self._source_builtins[uri] = Occurrences(primitives, ())

    def _key_at(self, uri: str, line: int, column: int) -> Key | None:
        if uri not in self._keys:
            self._source_keys(uri)
        index = bisect_right(self._key_starts[uri], (line, column))
        if index:
            found = self._keys[uri][index - 1]
            if found.node.position.holds(line, column):
                return found
        return None

    def _key_doc(self, uri: str, key: Key) -> str | None:
        return self._structural_doc(uri, key) or self._field_doc(uri, key)

    def _structural_doc(self, uri: str, key: Key) -> str | None:
        name = key.node.value
        if key.site is Site.RESPONSES:
            text = _status_doc(name)
            response = self._responses.get((uri, key.node.line, key.node.column))
            if text is not None and response is not None:
                text += self._description(response.description)
            return text
        if key.site is Site.RESOURCE and name.removesuffix('?') in METHODS:
            text = _method_doc(name)
            found = self._operations.get((uri, key.node.line, key.node.column))
            if found is not None:
                path, operation = found
                text += self._description(operation.description)
                text += f'\n\nResource: {_code(path)}'
            return text
        if key.site in (Site.RESOURCE, Site.ROOT) and name.startswith('/'):
            endpoint = self._resources.get((uri, key.node.line, key.node.column))
            path = endpoint.full_uri if endpoint is not None else name
            text = (
                f'**{_code(name)}** — resource\n\nDeclares a resource path relative to its parent and the API base URI.'
            )
            if endpoint is not None:
                text += self._description(endpoint.description)
            return text + f'\n\nResource path: {_code(path)}'
        return None

    def _field_doc(self, uri: str, key: Key) -> str | None:
        name = key.node.value
        owner = self._mappings.get((uri, key.owner.line, key.owner.column))
        if key.site is Site.TYPE and owner is not None and isinstance(owner.entity, BaseShape):
            custom = self._custom_facet_doc(owner.entity, name)
            if custom is not None:
                return custom
        explanation = field_doc(key.site, name)
        if explanation is None:
            return None
        root = key.owner is self._node(uri)
        if name == 'uses' and key.site is not Site.ROOT and not (root and uri in self._raml.fragments):
            return None  # only typed fragment roots establish imports
        if name == 'usage' and key.site in (Site.METHOD, Site.RESOURCE):
            template = owner is not None and isinstance(owner.entity, TemplateDefinition)
            if not (root or template):
                return None
        text = f'**{_code(name)}** — RAML field\n\n{explanation}'
        if owner is not None and isinstance(owner.entity, BaseShape):
            base = owner.entity
            if name == 'format':
                formats = _formats(base.type)
                if formats:
                    text += f'\n\nFor {_code(base.type)}: ' + ', '.join(map(_code, formats)) + '.'
        return text + f'\n\n[RAML reference]({_SPEC})'

    def _custom_facet_doc(self, base: BaseShape, name: str) -> str | None:
        value = base.custom_facets.get(name)
        declarations = self._raml.custom_facet_refs.get(value, ()) if value is not None else ()
        subjects = [self._subjects.get(prop.base.id) for prop in declarations]
        texts = [self._describe(subject) for subject in subjects if subject is not None]
        return '\n\n---\n\n'.join(texts) if texts else None

    def _data_roots(self) -> Iterator[DataRoot]:
        for extension in self._raml.domain_extensions:
            if extension.defined_by is not None:
                yield DataRoot(
                    extension.value,
                    extension.defined_by,
                    extension.name,
                    'annotation value',
                )
        for value, declarations in self._raml.custom_facet_refs.items():
            for prop in declarations:
                yield DataRoot(value, prop.base, prop.name, 'custom facet value')
        for base in self._raml.shapes:
            name = base.name or '<anonymous>'
            if base.alias is not None:
                continue  # an alias shares its referent's data; the referent indexes it
            if base.default is not None:
                yield DataRoot(base.default, base, name, 'default value')
            for value in base.enum or ():
                yield DataRoot(value, base, name, 'enum value')
            for example in examples_of(base):
                if example.data is not None:
                    yield DataRoot(
                        example.data,
                        base,
                        name,
                        'example value',
                    )

    def _data_doc(self, target: DataTarget) -> str:
        cached = self._data_descriptions.get(target)
        if cached is not None:
            return cached
        subject = _Subject(target.name, target.role, target.base, target.required)
        text = self._shape(subject, target.base)
        self._data_descriptions[target] = text
        return text

    def _data_docs(self, targets: list[DataTarget], *, warn: bool = True) -> str:
        text = '\n\n---\n\n'.join(dict.fromkeys(self._data_doc(target) for target in targets))
        if warn and len(targets) > 1:
            text = _AMBIGUOUS + '\n\n' + text
        return text

    def _builtin(self, name: str) -> str:
        text = f'**{_code(name)}** — built-in RAML type\n\n{BUILTINS.get(name, "A RAML data type.")}'
        facets = TYPE_SPECIFIC_FACETS.get(name)
        if facets:
            text += '\n\nType-specific facets: ' + ', '.join(map(_code, sorted(facets))) + '.'
        formats = _formats(name)
        if formats:
            text += '\n\nFormats: ' + ', '.join(map(_code, formats)) + '.'
        return text + f'\n\n[RAML reference]({_SPEC})'

    def _describe(self, subject: _Subject) -> str:
        text = self._descriptions.get(subject)
        if text is None:
            text = self._describe_subject(subject)
            self._descriptions[subject] = text
        return text

    def _describe_subject(self, subject: _Subject) -> str:
        entity = subject.entity
        if isinstance(entity, BaseShape):
            return self._shape(subject, entity)
        title = f'**{_code(subject.name)}** — {subject.role}'
        if isinstance(entity, LibraryLink):
            text = title + f'\n\nImports {_code(entity.value)}. Use this namespace prefix to name its declarations.'
            if entity.link is not None and entity.link.usage is not None:
                text += '\n\n' + entity.link.usage.value
            return text
        if isinstance(entity, TemplateDefinition):
            template = entity.resolved()
            noun = 'method' if subject.role == 'trait' else 'resource'
            text = title + f'\n\nA reusable {noun} template. Its fields are merged where it is applied.'
            if template.usage is not None:
                text += '\n\n' + template.usage.value
            if template.declared_variables:
                parameters = template.declared_variables - template.reserved
                reserved = template.declared_variables & template.reserved
                if parameters:
                    text += '\n\nTemplate parameters: ' + ', '.join(map(_code, sorted(parameters))) + '.'
                if reserved:
                    text += '\n\nProcessor-supplied parameters: ' + ', '.join(map(_code, sorted(reserved))) + '.'
            return text
        scheme = entity.resolved()
        text = title + '\n\nA named authentication mechanism, applied with `securedBy`.'
        if scheme.type:
            text += f'\n\nMechanism: {_code(scheme.type)}.'
        if scheme.display_name is not None:
            text += '\n\n' + scheme.display_name.value
        text += self._description(scheme.description)
        return text

    def _shape(self, subject: _Subject, base: BaseShape) -> str:
        kind = 'JSON Schema' if isinstance(base.shape, JsonShape) else base.type or 'unresolved'
        if base.type in ('array', 'union'):
            kind = type_name(base)
        if isinstance(base.shape, UnknownShape):
            kind = 'unresolved'
        text = f'**{_code(subject.name)}** — {subject.role} · {_code(kind)}'
        text += _relation(subject, base)
        if subject.role == 'pattern property':
            text += (
                '\n\nThis regular expression is searched against property names. Explicit properties take precedence.'
            )
        if subject.role == 'custom facet declaration':
            text += (
                '\n\nDefines a value that subtypes can supply alongside `type`. '
                'The declaration describes the facet value, not a property of an API payload.'
            )
            requirement = 'must' if subject.required else 'may'
            text += (
                f' Subtypes **{requirement} supply this facet**; the declaring type does not supply it itself. '
                'Supplied values are checked against this declaration.'
            )
        elif subject.required is not None:
            presence = 'required' if subject.required else 'optional'
            text += f'\n\nPresence: **{presence}**.'
            if subject.name.endswith('?') and base.required is not None:
                text += ' The trailing `?` is part of the name because `required` was supplied explicitly.'
        if base.display_name is not None and base.display_name.value != subject.name:
            text += f'\n\n{base.display_name.value}'
        text += self._description(base.description)
        if subject.role == 'annotation type' and base.allowed_targets is not None:
            text += '\n\nAllowed targets: ' + ', '.join(_code(str(target)) for target in base.allowed_targets) + '.'
        if (base.location, base.key_pos.line, base.key_pos.column) in self._ambiguous_sites:
            text += '\n\n*This declaration has several materializations; the type shown is the first in this parse.*'
        return text

    @staticmethod
    def _description(facet: ScalarFacet[str] | None) -> str:
        if facet is None or not facet.value:
            return ''
        return '\n\n' + facet.value


def _role(kind: Kind) -> str:
    return {
        Kind.TYPE: 'data type',
        Kind.ANNOTATION_TYPE: 'annotation type',
        Kind.TRAIT: 'trait',
        Kind.RESOURCE_TYPE: 'resource type',
        Kind.SECURITY_SCHEME: 'security scheme',
        Kind.LIBRARY: 'library namespace',
        Kind.PROPERTY: 'object property',
        Kind.FACET: 'custom facet declaration',
        Kind.FILE: 'file',
    }[kind]


def _unbound_doc(key: Key) -> str | None:
    if key.site is Site.NAMED_TYPES:
        role = {
            'queryParameters': 'query parameter',
            'headers': 'header parameter',
            'uriParameters': 'URI parameter',
            'baseUriParameters': 'base URI parameter',
            'facets': 'custom facet',
        }.get(key.table, 'object property')
        return f'**{_code(key.node.value)}** — {role} declaration\n\nIts value declares a data type.'
    if key.annotation:
        return f'**{_code(key.node.value)}** — annotation application\n\nIts value is checked against the named annotation type.'
    return None


def _relation(subject: _Subject, base: BaseShape) -> str:
    if base.alias is not None:
        name = _code(base.alias.name or type_name(base.alias))
        if subject.role in ('data type', 'annotation type', 'type declaration'):
            return f'\n\nAlias of {name}: another name for the same type.'
        return f'\n\nUses {name} as its type.'
    if base.inherits:
        parents = ', '.join(_code(parent.name or type_name(parent)) for parent in base.inherits)
        return f'\n\nSpecializes {parents}.'
    return ''


def _formats(kind: str) -> list[str]:
    if kind == 'integer':
        return list(INTEGER_FORMATS)
    if kind == 'number':
        return sorted(NUMBER_FORMATS)
    return sorted(DATETIME_FORMATS) if kind == 'datetime' else []


def _primitives(root: RdtNode) -> Iterator[Primitive]:
    pending = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, Primitive):
            yield node
        elif isinstance(node, Array):
            pending.append(node.item)
        elif isinstance(node, Optional_):
            pending.append(node.inner)
        elif isinstance(node, Union):
            pending.extend(node.members)


def _method_doc(name: str) -> str:
    method = name.removesuffix('?').upper()
    text = (
        f'**{method}** — HTTP method\n\n{METHOD_DOCS[name.removesuffix("?")]}\n\n'
        'This declaration describes the operation request parameters, headers, body and possible responses.'
    )
    if name.endswith('?'):
        text += ' In a resource type, this optional method is contributed only if the applying resource declares it.'
    return text + f'\n\n[HTTP reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Methods/{method})'


def _status_doc(name: str) -> str | None:
    if _STATUS.fullmatch(name) is None:
        return None
    try:
        status = HTTPStatus(int(name))
    except ValueError:
        return f'**{name}** — response status\n\nDeclares the response for HTTP status {name}.'
    return (
        f'**{name} {status.phrase}** — response status\n\n{status.description}\n\n'
        f'[HTTP reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/{name})'
    )
