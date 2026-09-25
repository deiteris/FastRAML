"""The effective model as Sphinx content: one entry per addressable item.

Entries are Sphinx's own object descriptions (`desc` nodes), so any theme
styles an endpoint or a type the way it styles a Python class, and the lists
inside them are the field lists a Python function's parameters use. Nothing
here is HTML: the same nodes build a PDF.

**The model is the effective document.** It is parsed with `unwrap=True`:
traits and resource types are applied, inheritance is flattened, URI
parameters are propagated down (docs/08), each method's `securedBy` is the
effective list (docs/09 § A4) and P9 has marked every cycle with a
`RecursiveShape`. So what an entry shows is what a caller must send, with no
rule of RAML's applied here, and a walk that stops at declarations and at
recursion markers ends.

**A declared type is linked, never repeated.** A body of `Book` links to
`Book`'s entry; only an anonymous type -- one declared inline -- is spelled
out where it is used. An anonymous subtype of a declared one shows what it
adds and links to the rest, because unwrapping has flattened the supertype's
properties into it and repeating them would bury the addition.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, cast

from docutils import nodes
from fastraml import ArrayShape, BaseShape, FileShape, JsonShape, ObjectShape, RecursiveShape, UnionShape, facets_of
from sphinx import addnodes
from sphinx.util.nodes import make_id

from .domain import LABELS, RamlDomain, Target
from .markdown import markdown, markdown_sections
from .model import plain, scalar, target, text, texts

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from docutils.nodes import Element, Node
    from fastraml import (
        Body,
        DomainExtension,
        Example,
        Operation,
        Parameter,
        Property,
        Response,
        SecurityScheme,
        SecuritySchemeDefinition,
    )
    from sphinx.util.docutils import SphinxDirective

    from .apis import Api
    from .catalogue import Declared, Kind

Detail = Literal['summary', 'request', 'full']
Register = Literal['indexed', 'target', 'none']
DETAILS: tuple[Detail, ...] = ('summary', 'request', 'full')


class Writer:
    """Renders one API's items for one directive, and registers their targets.

    With `index` false nothing is registered, so an item can be shown a second
    time -- in a tutorial, say -- without competing with its reference entry.
    """

    def __init__(self, directive: SphinxDirective, api: Api, *, index: bool) -> None:
        self.env = directive.env
        self.document = directive.state.document
        self.location = directive.get_location()
        self.api = api
        self.catalogue = api.catalogue
        self.index = index
        self.domain = cast('RamlDomain', self.env.get_domain('raml'))

    # -- the API ---------------------------------------------------------------

    def overview(self) -> list[Node]:
        entry = self.catalogue.entry
        signature: list[Node] = [addnodes.desc_name(self.catalogue.title, self.catalogue.title)]
        signature.extend(addnodes.desc_annotation('', f' {version}') for version in self.catalogue.value('version'))
        out, content = self.entry('api', '', signature, self.catalogue.title)
        content.extend(markdown(text(entry.description) if entry else None, self.document))
        content.extend(
            fields(
                [
                    ('Base URI', _literals(self.catalogue.value('base_uri'))),
                    ('Base URI parameters', self.parameters(self.catalogue.base_uri_parameters(), addressable=True)),
                    ('Protocols', _words([protocol.upper() for protocol in self.catalogue.value('protocols')])),
                    ('Media types', _literals(self.catalogue.value('media_types'))),
                    ('Security', self.security(self.catalogue.secured_by())),
                    ('Annotations', self.annotations(entry.annotations if entry else None)),
                ]
            )
        )
        return out

    def documentation(self, titles: Iterable[str] | None = None) -> list[Node]:
        """Documentation items as sections, so they are in the toctree and the search."""
        wanted = None if titles is None else set(titles)
        duplicates = self.catalogue.duplicate_titles()
        out: list[Node] = []
        for item in self.catalogue.documentation():
            title = text(item.title) or ''
            if wanted is not None and title not in wanted:
                continue
            section = nodes.section()
            section += nodes.title(title, title)
            if title not in duplicates:
                self.target('documentation-item', title, section, title)
            markdown_sections(text(item.content), self.document, section)
            out.append(section)
        return out

    # -- endpoints -------------------------------------------------------------

    def endpoints(self, paths: Iterable[str], detail: Detail, methods: set[str] | None) -> list[Node]:
        if detail == 'summary':
            return [_bullets([self.endpoint_summary(path, methods) for path in paths])]
        out: list[Node] = []
        for path in paths:
            out.extend(self.endpoint(path, detail, methods))
        return out

    def endpoint(self, path: str, detail: Detail, methods: set[str] | None) -> list[Node]:
        endpoint = self.catalogue.endpoints[path]
        out, content = self.entry('endpoint', path, [addnodes.desc_name(path, path)], path)
        content.extend(self.named(text(endpoint.display_name), path))
        content.extend(markdown(text(endpoint.description), self.document))
        content.extend(
            fields(
                [
                    ('URI parameters', self.parameters(endpoint.uri_parameters)),
                    ('Annotations', self.annotations(endpoint.annotations)),
                ]
            )
        )
        for method, operation in endpoint.operations.items():
            if methods is None or method in methods:
                content.extend(self.method(path, method, operation, detail))
        return out

    def endpoint_summary(self, path: str, methods: set[str] | None) -> list[Node]:
        endpoint = self.catalogue.endpoints[path]
        lead = nodes.paragraph('', '', self.xref('endpoint', path, path))
        lead.extend(_dash(text(endpoint.display_name), path))
        rows = []
        for method, operation in endpoint.operations.items():
            if methods is None or method in methods:
                key = f'{method.upper()} {path}'
                row = nodes.paragraph('', '', self.xref('method', key, key))
                row.extend(_dash(text(operation.display_name), method))
                rows.append([row])
        return [lead, _bullets(rows)] if rows else [lead]

    def method(self, path: str, method: str, operation: Operation, detail: Detail) -> list[Node]:
        key = f'{method.upper()} {path}'
        signature: list[Node] = [
            addnodes.desc_sig_keyword(method.upper(), method.upper()),
            addnodes.desc_sig_space(),
            addnodes.desc_name(path, path),
        ]
        out, content = self.entry('method', key, signature, key)
        content.extend(self.named(text(operation.display_name), method))
        content.extend(markdown(text(operation.description), self.document))
        endpoint = self.catalogue.endpoints[path]
        request = operation.request
        bodies = [self.body(media, body) for media, body in (request.bodies if request else {}).items()]
        query_string = request.query_string if request else None
        content.extend(
            fields(
                [
                    # Already the effective list: falling back to the resource's
                    # would re-show a requirement the method removed (docs/09 § A4).
                    ('Security', self.security(operation.secured_by)),
                    ('Protocols', _words([protocol.upper() for protocol in operation.protocols])),
                    ('Annotations', self.annotations(operation.annotations)),
                    ('Base URI parameters', self.base_parameters()),
                    ('URI parameters', self.parameters(endpoint.uri_parameters)),
                    ('Headers', self.parameters(request.headers if request else {})),
                    ('Query parameters', self.parameters(request.query_parameters if request else {})),
                    ('Query string', self.typed(query_string) if query_string is not None else []),
                    ('Request body', [_bullets(bodies)] if bodies else []),
                ]
            )
        )
        if detail == 'full' and operation.responses:
            content += nodes.rubric('Responses', 'Responses')
            for status, response in operation.responses.items():
                content.extend(self.response(key, str(status), response))
        return out

    def response(self, method: str | None, status: str, response: Response) -> list[Node]:
        """One response: a target under its method's key, or none when `method` is `None`."""
        signature: list[Node] = [addnodes.desc_name(status, status)]
        phrase = _phrase(status)
        if phrase:
            signature.append(addnodes.desc_annotation('', f' {phrase}'))
        key = f'{method} {status}'
        out, content = self.entry('response', key, signature, key, register='target' if method else 'none')
        content.extend(markdown(text(response.description), self.document))
        bodies = [self.body(media, body) for media, body in response.bodies.items()]
        content.extend(
            fields(
                [
                    ('Headers', self.parameters(response.headers)),
                    ('Body', [_bullets(bodies)] if bodies else []),
                    ('Annotations', self.annotations(response.annotations)),
                ]
            )
        )
        return out

    # -- declarations ----------------------------------------------------------

    def declarations(self, kind: Declared, keys: Iterable[str], detail: Detail) -> list[Node]:
        if detail == 'summary':
            rows = []
            for key in keys:
                row = nodes.paragraph('', '', self.xref(kind, key, self.catalogue.label(key)))
                declared = self.catalogue.declaration(kind, key)
                shown = declared if isinstance(declared, BaseShape) else _content(declared)
                row.extend(_dash(text(getattr(shown, 'display_name', None)), key.partition('#')[2]))
                rows.append([row])
            return [_bullets(rows)]
        out: list[Node] = []
        for key in keys:
            out.extend(self.declaration(kind, key))
        return out

    def declaration(self, kind: Declared, key: str) -> list[Node]:
        if kind == 'security-scheme':
            return self.security_scheme(key)
        return self.type(kind, key)

    def type(self, kind: Declared, key: str) -> list[Node]:
        declared = cast('BaseShape', self.catalogue.declaration(kind, key))
        label = self.catalogue.label(key)
        name = f'({label})' if kind == 'annotation-type' else label
        signature: list[Node] = [addnodes.desc_name(name, name)]
        if declared.alias is not None:
            # An alias: it shares its referent's everything (docs/07 § 3).
            signature.extend([addnodes.desc_sig_punctuation('', ' = '), *self.label(declared.alias)])
            return self.entry(kind, key, signature, label)[0]
        signature.extend([addnodes.desc_sig_punctuation('', ' : '), *self.shape_label(declared)])
        out, content = self.entry(kind, key, signature, label)
        content.extend(self.named(text(declared.display_name), key.partition('#')[2]))
        content.extend(markdown(text(declared.description), self.document))
        if kind == 'annotation-type':
            targets = [str(where) for where in declared.allowed_targets or []]
            content.extend(fields([('Applies to', _words(targets))]))
        # A declared object type's properties are entries of their own, so a
        # link can land on one. Anything else -- an array's items, a union's
        # members, an annotation's value -- is listed as an inline type is.
        shape = declared.shape
        if kind == 'type' and isinstance(shape, ObjectShape):
            content.extend(self.details(declared, properties=False))
            for field, prop in (shape.properties or {}).items():
                content.extend(self.property(key, field, prop))
        else:
            content.extend(self.details(declared))
        return out

    def property(self, type_key: str, field: str, prop: Property) -> list[Node]:
        signature: list[Node] = [addnodes.desc_name(field, field), addnodes.desc_sig_punctuation('', ' : ')]
        signature.extend(self.label(prop.base))
        if prop.required:
            signature.append(addnodes.desc_annotation('', ' required'))
        out, content = self.entry(
            'property', f'{type_key}.{field}', signature, f'{self.catalogue.label(type_key)}.{field}', register='target'
        )
        content.extend(self.about(prop.base))
        return out

    def security_scheme(self, key: str) -> list[Node]:
        declared = cast('SecuritySchemeDefinition', self.catalogue.declaration('security-scheme', key))
        # The name and identity are the declaration's; the content may be an
        # included SecurityScheme fragment's, which `resolved()` reaches.
        scheme = _content(declared)
        label = self.catalogue.label(key)
        signature: list[Node] = [addnodes.desc_name(label, label), addnodes.desc_annotation('', f' {scheme.type}')]
        out, content = self.entry('security-scheme', key, signature, label)
        content.extend(self.named(text(scheme.display_name), declared.name))
        content.extend(markdown(text(scheme.description), self.document))
        settings: list[list[Node]] = []
        if scheme.settings is not None:
            written: dict[str, list[str]] = {
                name: [scalar(facet.value)] for name, facet in scheme.settings.values.items()
            }
            written.update({name: list(items) for name, items in scheme.settings.lists.items()})
            settings = [
                [nodes.paragraph('', '', nodes.strong(name, name), nodes.Text(': '), *_literals(values))]
                for name, values in written.items()
            ]
        adds = scheme.described_by
        content.extend(
            fields(
                [
                    ('Settings', [_bullets(settings)] if settings else []),
                    ('Adds headers', self.parameters(adds.headers if adds else {})),
                    ('Adds query parameters', self.parameters(adds.query_parameters if adds else {})),
                    ('Adds a query string', self.typed(adds.query_string) if adds and adds.query_string else []),
                    ('Annotations', self.annotations(scheme.annotations)),
                ]
            )
        )
        if adds is not None and adds.responses:
            content += nodes.rubric('Adds responses', 'Adds responses')
            # Shown as a method's are, but no target: a scheme's response has
            # no method to be named under.
            for status, response in adds.responses.items():
                content.extend(self.response(None, str(status), response))
        return out

    # -- shapes ----------------------------------------------------------------

    def link(self, entity_id: int, text: str | None = None) -> Node | None:
        """A link to the declaration an entity is, when it is one."""
        declared = self.catalogue.declared_at(entity_id)
        if declared is None:
            return None
        kind, key = declared
        return self.xref(kind, key, text or self.catalogue.label(key))

    def label(self, base: BaseShape | None) -> list[Node]:
        """A type as one line: a link to a declaration, or what an anonymous one is."""
        if base is None:
            return [nodes.Text('any')]
        if base.alias is not None:
            return self.label(base.alias)
        linked = self.link(base.id)
        if linked is not None:
            return [linked]
        if isinstance(base.shape, RecursiveShape):
            head = base.shape.head
            return [self.link(head.id) or nodes.Text(head.name or 'recursive')]
        return self.shape_label(base)

    def shape_label(self, base: BaseShape) -> list[Node]:
        """What a shape is, never itself: `array of Money`, `string | number`, `Entity`.

        Apart from `label` so a declaration's own signature can say what it
        is without linking to itself.
        """
        shape = base.shape
        if isinstance(shape, ArrayShape):
            return [nodes.Text('array of '), *self.label(shape.items)] if shape.items else [nodes.Text('array')]
        if isinstance(shape, UnionShape):
            out: list[Node] = []
            for position, member in enumerate(shape.any_of or []):
                if position:
                    out.append(nodes.Text(' | '))
                out.extend(self.label(member))
            return out or [nodes.Text('union')]
        if isinstance(shape, JsonShape):
            return [nodes.Text('JSON schema')]
        return self.supertypes(base) or [nodes.Text(base.type)]

    def supertypes(self, base: BaseShape) -> list[Node]:
        """Links to the declared types a shape extends, if it extends any."""
        out: list[Node] = []
        for parent in self.declared_parents(base):
            if out:
                out.append(nodes.Text(', '))
            out.append(cast('Node', self.link(parent.id)))
        return out

    def declared_parents(self, base: BaseShape) -> list[BaseShape]:
        """The declared types a shape extends, through any alias."""
        parents = [target(parent) for parent in base.inherits]
        return [parent for parent in parents if self.catalogue.declared_at(parent.id) is not None]

    def about(self, base: BaseShape | None) -> list[Node]:
        """What a use of a type says beyond its label: nothing, for a declared one.

        An anonymous subtype of a declared type -- `type: Book` with a facet
        added -- arrives flattened, carrying `Book`'s description, facets and
        properties as its own. Only what differs from those is shown; the
        label already links to the rest.
        """
        if base is None:
            return []
        base = target(base)
        if self.catalogue.declared_at(base.id) is not None or isinstance(base.shape, RecursiveShape):
            return []
        beneath = self.declared_parents(base)
        description = text(base.description)
        if any(text(parent.description) == description for parent in beneath):
            description = None
        return [*markdown(description, self.document), *self.details(base, beneath=beneath)]

    def details(
        self, base: BaseShape, *, properties: bool = True, beneath: list[BaseShape] | None = None
    ) -> list[Node]:
        """Constraints, allowed values, structure and examples of one shape.

        `beneath` are the declared types it extends: what it has from them is
        left out, since their entries say it.
        """
        beneath = beneath or []
        out: list[Node] = []
        inherited = [_facets(parent) for parent in beneath]
        facets = [
            (name, value) for name, value in _facets(base).items() if all(p.get(name) != value for p in inherited)
        ]
        if facets:
            row = nodes.paragraph()
            for position, (name, value) in enumerate(facets):
                if position:
                    row += nodes.Text(', ')
                row += nodes.emphasis(name, name)
                row += nodes.Text(' ')
                row += nodes.literal(value, value)
            out.append(row)
        enum = [plain(member) for member in base.enum or []]
        if enum and all([plain(member) for member in parent.enum or []] != enum for parent in beneath):
            out.append(nodes.paragraph('', '', nodes.Text('One of: '), *_literals(enum)))
        default = plain(base.default)
        if base.default is not None and all(plain(parent.default) != default for parent in beneath):
            out.append(nodes.paragraph('', '', nodes.Text('Default: '), *_literals([default])))
        if all(set(parent.annotations) != set(base.annotations) for parent in beneath):
            out.extend(fields([('Annotations', self.annotations(base.annotations))]))
        if properties:
            out.extend(self.structure(base, beneath))
        out.extend(self.examples(base))
        return out

    def structure(self, base: BaseShape, beneath: list[BaseShape]) -> list[Node]:
        """What an anonymous type holds: its own properties, items or members."""
        shape = base.shape
        if isinstance(shape, ObjectShape):
            inherited = {
                name
                for parent in beneath
                if isinstance(parent.shape, ObjectShape)
                for name in parent.shape.properties or {}
            }
            rows = [
                self.property_row(name, prop.base, required=prop.required)
                for name, prop in (shape.properties or {}).items()
                if name not in inherited
            ]
            rows.extend(
                self.property_row(f'/{pattern.pattern.pattern}/', pattern.base, required=False)
                for pattern in (shape.pattern_properties or {}).values()
            )
            return [_bullets(rows)] if rows else []
        if isinstance(shape, ArrayShape):
            inner = self.about(shape.items)
            return [nodes.paragraph('', 'Each item:'), *inner] if inner else []
        if isinstance(shape, UnionShape):
            rows = [
                [nodes.paragraph('', '', *self.label(member)), *self.about(member)] for member in shape.any_of or []
            ]
            return [nodes.paragraph('', 'One of:'), _bullets(rows)] if rows else []
        if isinstance(shape, JsonShape):
            # The nearest RAML shape to the schema (docs/10 § 7): its structure
            # is what a reader of the schema needs, in the same form as the rest.
            view = shape.as_shape()
            return self.structure(view, []) if view is not None else []
        return []

    def property_row(self, name: str, base: BaseShape, *, required: bool) -> list[Node]:
        lead = nodes.paragraph('', '', nodes.strong(name, name), nodes.Text(' : '), *self.label(base))
        if required:
            lead += nodes.emphasis(' required', ' required')
        return [lead, *self.about(base)]

    def examples(self, base: BaseShape) -> list[Node]:
        found: list[tuple[str, Example]] = []
        if base.example is not None:
            found.append(('Example', base.example))
        if base.examples is not None:
            # `entries()`, never `values`: with `examples: !include` the entries
            # live on the fragment and `values` is empty (AGENTS.md).
            found.extend(
                (f'Example: {text(example.display_name) or name}', example)
                for name, example in base.examples.entries().items()
            )
        out: list[Node] = []
        for title, example in found:
            out.append(nodes.rubric(title, title))
            out.extend(markdown(text(example.description), self.document))
            out.append(_code(plain(example.data)))
        return out

    # -- parts of a request ----------------------------------------------------

    def parameters(self, parameters: dict[str, Parameter], *, addressable: bool = False) -> list[Node]:
        rows = []
        for name, parameter in parameters.items():
            row = self.property_row(name, parameter.base, required=parameter.required)
            if addressable and self.index:
                self.target('base-uri-parameter', name, cast('Element', row[0]), f'{{{name}}}')
            rows.append(row)
        return [_bullets(rows)] if rows else []

    def base_parameters(self) -> list[Node]:
        """The base URI's parameters, linked: `{tenant}` is required and is not in the path."""
        links: list[Node] = []
        for position, name in enumerate(self.catalogue.base_uri_parameters()):
            if position:
                links.append(nodes.Text(', '))
            links.append(self.xref('base-uri-parameter', name, f'{{{name}}}'))
        return [nodes.paragraph('', '', *links)] if links else []

    def typed(self, base: BaseShape) -> list[Node]:
        return [nodes.paragraph('', '', *self.label(base)), *self.about(base)]

    def body(self, media: str, body: Body) -> list[Node]:
        lead = nodes.paragraph('', '', nodes.literal(media, media), nodes.Text(' : '), *self.label(body.shape))
        return [lead, *self.about(body.shape)]

    def security(self, secured_by: list[SecurityScheme]) -> list[Node]:
        if not secured_by:
            return []
        row = nodes.paragraph()
        for position, requirement in enumerate(secured_by):
            if position:
                row += nodes.Text(' or ')
            if requirement.is_null:
                row += nodes.Text('no authentication')
                continue
            definition = requirement.definition
            linked = self.link(definition.id) if definition is not None else None
            row += linked or nodes.literal(requirement.name, requirement.name)
            if requirement.compiled_params:
                row += nodes.Text(' (scopes: ')
                row.extend(_literals(requirement.compiled_params))
                row += nodes.Text(')')
        return [row]

    def annotations(self, applied: dict[str, DomainExtension] | None) -> list[Node]:
        rows = []
        for name, extension in (applied or {}).items():
            shown = f'({name})'
            defined_by = extension.defined_by
            linked = self.link(defined_by.id, shown) if defined_by is not None else None
            row = nodes.paragraph('', '', linked or nodes.literal(shown, shown))
            value = plain(extension.value)
            if value is not None:
                row += nodes.Text(' ')
                row.extend(_literals([value]))
            rows.append([row])
        return [_bullets(rows)] if rows else []

    # -- entries and targets ---------------------------------------------------

    def entry(
        self,
        kind: Kind,
        key: str,
        signature: list[Node],
        display: str,
        *,
        register: Register = 'indexed',
    ) -> tuple[list[Node], addnodes.desc_content]:
        """One object description, and where its content goes.

        `register` is how far it is findable: a link target in the general
        index, a link target only (a property, a response), or neither (a
        scheme's response, which has no name of its own). `:no-index:` on the
        directive makes it neither.
        """
        registered = self.index and register != 'none'
        desc = addnodes.desc(domain='raml', objtype=kind, desctype=kind, no_index=not registered)
        desc['classes'].extend(['raml', kind])
        sig = addnodes.desc_signature('', '')
        sig.extend(signature)
        out: list[Node] = []
        if registered:
            anchor = self.target(kind, key, sig, display)
            if register == 'indexed':
                entry = f'{display} ({self.catalogue.title} {LABELS[kind]})'
                out.append(addnodes.index(entries=[('single', entry, anchor, '', None)]))
        content = addnodes.desc_content()
        desc += sig
        desc += content
        out.append(desc)
        return out, content

    def target(self, kind: Kind, key: str, node: Element, display: str) -> str:
        anchor = make_id(self.env, self.document, 'raml', f'{self.api.name} {kind} {key}')
        node['ids'].append(anchor)
        self.document.note_explicit_target(node)
        self.domain.note_object(kind, self.api.name, key, Target(self.env.docname, anchor, display), self.location)
        return anchor

    def xref(self, kind: Kind, key: str, text: str) -> Node:
        """A link the extension writes: plain text, unwarned, when nothing renders its target."""
        ref = addnodes.pending_xref(
            '', refdomain='raml', reftype=kind, reftarget=key, refexplicit=True, refwarn=False, refdoc=self.env.docname
        )
        ref['raml:api'] = self.api.name
        self.domain.note_link(kind, self.api.name, key, self.env.docname)
        ref += nodes.literal(text, text, classes=['xref', 'raml', f'raml-{kind}'])
        return ref

    def named(self, display_name: str | None, name: str) -> list[Node]:
        """A `displayName`, when it says more than the name it is displayed for."""
        if not display_name or display_name == name:
            return []
        return [nodes.paragraph('', '', nodes.strong(display_name, display_name))]


def fields(rows: list[tuple[str, list[Node]]]) -> list[Node]:
    """A field list of the rows that have anything in them."""
    present = [(name, body) for name, body in rows if body]
    if not present:
        return []
    out = nodes.field_list()
    for name, body in present:
        blocks = [node if isinstance(node, nodes.Body) else nodes.paragraph('', '', node) for node in body]
        out += nodes.field('', nodes.field_name(name, name), nodes.field_body('', *blocks))
    return [out]


def _content(declared: SecuritySchemeDefinition | BaseShape | None) -> SecuritySchemeDefinition:
    """A scheme declaration's content: its own, or the fragment it includes."""
    return cast('SecuritySchemeDefinition', declared).resolved()


def _facets(base: BaseShape) -> dict[str, str]:
    """A shape's constraints in RAML's spelling, each as the text a reader writes."""
    shape = base.shape
    out = {name: scalar(facet.value) for name, facet in facets_of(shape)}
    if isinstance(shape, FileShape) and shape.file_types:
        # A list of facets, which `facets_of` does not list.
        out['fileTypes'] = ', '.join(texts(shape.file_types))
    return out


def _bullets(rows: Iterable[Sequence[Node]]) -> nodes.bullet_list:
    out = nodes.bullet_list()
    for row in rows:
        out += nodes.list_item('', *row)
    return out


def _dash(text: str | None, name: str) -> list[Node]:
    return [nodes.Text(f' -- {text}')] if text and text != name else []


def _words(values: Sequence[str]) -> list[Node]:
    return [nodes.paragraph('', ', '.join(values))] if values else []


def _literals(values: Iterable[Any] | None) -> list[Node]:
    out: list[Node] = []
    for position, value in enumerate(values or []):
        if position:
            out.append(nodes.Text(', '))
        shown = value if isinstance(value, str) else json.dumps(value, default=str)
        out.append(nodes.literal(shown, shown))
    return out


def _code(value: Any) -> nodes.literal_block:
    """An example as a code block: JSON unless it is a string, which is shown as written."""
    if isinstance(value, str):
        return nodes.literal_block(value, value, language='text')
    shown = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return nodes.literal_block(shown, shown, language='json')


def _phrase(status: str) -> str:
    try:
        return HTTPStatus(int(status)).phrase
    except ValueError:
        return ''
