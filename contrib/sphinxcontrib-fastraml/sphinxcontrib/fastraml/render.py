"""The effective tree as Sphinx content: one entry per addressable item.

Entries are Sphinx's own object descriptions (`desc` nodes), so any theme
styles an endpoint or a type the way it styles a Python class, and the lists
inside them are the field lists a Python function's parameters use. Nothing
here is HTML: the same nodes build a PDF.

**The tree is the effective document** (docs/16 § 6). Traits and resource
types are already applied, inheritance is flattened, URI parameters are
propagated down (docs/08) and each method's `securedBy` is the effective list
(docs/09 § A4), so what an entry shows is what a caller must send, with no rule
of RAML's applied here.

**A declared type is linked, never repeated.** A body of `Book` links to
`Book`'s entry; only an anonymous type -- one declared inline -- is spelled
out where it is used. An anonymous subtype of a declared one shows what it
adds and links to the rest, because the tree has flattened the supertype's
properties into it and repeating them would bury the addition.

Facet names are the tree's keys in RAML's own spelling: the tree writes
`max_length` for RAML's `maxLength`, and the camel-case form is the one a
reader of the RAML file recognises.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, cast

from docutils import nodes
from sphinx import addnodes
from sphinx.util.nodes import make_id

from .domain import LABELS, RamlDomain, Target
from .markdown import markdown, markdown_sections
from .walk import is_recursion, is_ref, is_shape

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from docutils.nodes import Element, Node
    from sphinx.util.docutils import SphinxDirective

    from .apis import Api
    from .catalogue import Declared, Kind
    from .tree import (
        Applied,
        Example,
        Json,
        Operation,
        Parameter,
        Property,
        Response,
        SecuredBy,
        SecurityScheme,
        Shape,
        ShapeNode,
    )

Detail = Literal['summary', 'request', 'full']
Register = Literal['indexed', 'target', 'none']
DETAILS: tuple[Detail, ...] = ('summary', 'request', 'full')

#: The facets a reader is told about, in the order they read best.
FACETS: tuple[str, ...] = (
    'format',
    'pattern',
    'min_length',
    'max_length',
    'minimum',
    'maximum',
    'multiple_of',
    'min_items',
    'max_items',
    'unique_items',
    'min_properties',
    'max_properties',
    'additional_properties',
    'discriminator',
    'discriminator_value',
    'file_types',
)


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
        if entry.get('version'):
            signature.append(addnodes.desc_annotation('', f' {entry["version"]}'))
        out, content = self.entry('api', '', signature, self.catalogue.title)
        content.extend(markdown(entry.get('description'), self.document))
        base = entry.get('base_uri')
        content.extend(
            fields(
                [
                    ('Base URI', [nodes.literal(base, base)] if base else []),
                    ('Base URI parameters', self.parameters(self.catalogue.base_uri_parameters(), addressable=True)),
                    ('Protocols', _words(entry.get('protocols'))),
                    ('Media types', _literals(entry.get('media_types'))),
                    ('Security', self.security(entry.get('secured_by'))),
                    ('Annotations', self.annotations(entry.get('annotations'))),
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
            if wanted is not None and item['title'] not in wanted:
                continue
            section = nodes.section()
            section += nodes.title(item['title'], item['title'])
            if item['title'] not in duplicates:
                self.target('documentation-item', item['title'], section, item['title'])
            markdown_sections(item['content'], self.document, section)
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
        content.extend(self.named(endpoint.get('display_name'), path))
        content.extend(markdown(endpoint.get('description'), self.document))
        content.extend(
            fields(
                [
                    ('URI parameters', self.parameters(endpoint.get('uri_parameters', {}))),
                    ('Annotations', self.annotations(endpoint.get('annotations'))),
                ]
            )
        )
        for method, operation in endpoint['operations'].items():
            if methods is None or method in methods:
                content.extend(self.method(path, method, operation, detail))
        return out

    def endpoint_summary(self, path: str, methods: set[str] | None) -> list[Node]:
        endpoint = self.catalogue.endpoints[path]
        lead = nodes.paragraph('', '', self.xref('endpoint', path, path))
        lead.extend(_dash(endpoint.get('display_name'), path))
        rows = []
        for method, operation in endpoint['operations'].items():
            if methods is None or method in methods:
                key = f'{method.upper()} {path}'
                row = nodes.paragraph('', '', self.xref('method', key, key))
                row.extend(_dash(operation.get('display_name'), method))
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
        content.extend(self.named(operation.get('display_name'), method))
        content.extend(markdown(operation.get('description'), self.document))
        endpoint = self.catalogue.endpoints[path]
        bodies = [self.body(media, node) for media, node in operation.get('bodies', {}).items()]
        query_string = operation.get('query_string')
        content.extend(
            fields(
                [
                    # Already the effective list: falling back to the resource's
                    # would re-show a requirement the method removed (docs/09 § A4).
                    ('Security', self.security(operation.get('secured_by'))),
                    ('Protocols', _words(operation.get('protocols'))),
                    ('Annotations', self.annotations(operation.get('annotations'))),
                    ('Base URI parameters', self.base_parameters()),
                    ('URI parameters', self.parameters(endpoint.get('uri_parameters', {}))),
                    ('Headers', self.parameters(operation.get('headers', {}))),
                    ('Query parameters', self.parameters(operation.get('query_parameters', {}))),
                    ('Query string', self.typed(query_string) if query_string else []),
                    ('Request body', [_bullets(bodies)] if bodies else []),
                ]
            )
        )
        if detail == 'full' and operation['responses']:
            content += nodes.rubric('Responses', 'Responses')
            for status, response in operation['responses'].items():
                content.extend(self.response(key, status, response))
        return out

    def response(self, method: str | None, status: str, response: Response) -> list[Node]:
        """One response: a target under its method's key, or none when `method` is `None`."""
        signature: list[Node] = [addnodes.desc_name(status, status)]
        phrase = _phrase(status)
        if phrase:
            signature.append(addnodes.desc_annotation('', f' {phrase}'))
        key = f'{method} {status}'
        out, content = self.entry('response', key, signature, key, register='target' if method else 'none')
        content.extend(markdown(response.get('description'), self.document))
        bodies = [self.body(media, node) for media, node in response.get('bodies', {}).items()]
        content.extend(
            fields(
                [
                    ('Headers', self.parameters(response.get('headers', {}))),
                    ('Body', [_bullets(bodies)] if bodies else []),
                    ('Annotations', self.annotations(response.get('annotations'))),
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
                node = self.catalogue.declaration(kind, key)
                row.extend(_dash(cast('dict[str, str]', node).get('display_name'), key.partition('#')[2]))
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
        node = cast('ShapeNode', self.catalogue.declaration(kind, key))
        label = self.catalogue.label(key)
        name = f'({label})' if kind == 'annotation-type' else label
        signature: list[Node] = [addnodes.desc_name(name, name)]
        if is_ref(node):
            # An alias: it shares its referent's everything (docs/07 § 3).
            signature.extend([addnodes.desc_sig_punctuation('', ' = '), *self.label(node)])
            return self.entry(kind, key, signature, label)[0]
        shape = cast('Shape', node)
        signature.extend([addnodes.desc_sig_punctuation('', ' : '), *self.shape_label(shape)])
        out, content = self.entry(kind, key, signature, label)
        content.extend(self.named(shape.get('display_name'), key.partition('#')[2]))
        content.extend(markdown(shape.get('description'), self.document))
        if kind == 'annotation-type':
            content.extend(fields([('Applies to', _words(shape.get('allowed_targets')))]))
        # A declared object type's properties are entries of their own, so a
        # link can land on one. Anything else -- an array's items, a union's
        # members, an annotation's value -- is listed as an inline type is.
        if kind == 'type' and shape['type'] == 'object':
            content.extend(self.details(shape, properties=False))
            for field, prop in (shape.get('properties') or {}).items():
                content.extend(self.property(key, field, prop))
        else:
            content.extend(self.details(shape))
        return out

    def property(self, type_key: str, field: str, prop: Property) -> list[Node]:
        signature: list[Node] = [addnodes.desc_name(field, field), addnodes.desc_sig_punctuation('', ' : ')]
        signature.extend(self.label(prop['type']))
        if prop['required']:
            signature.append(addnodes.desc_annotation('', ' required'))
        out, content = self.entry(
            'property', f'{type_key}.{field}', signature, f'{self.catalogue.label(type_key)}.{field}', register='target'
        )
        content.extend(self.about(prop['type']))
        return out

    def security_scheme(self, key: str) -> list[Node]:
        scheme = cast('SecurityScheme', self.catalogue.declaration('security-scheme', key))
        label = self.catalogue.label(key)
        signature: list[Node] = [addnodes.desc_name(label, label), addnodes.desc_annotation('', f' {scheme["type"]}')]
        out, content = self.entry('security-scheme', key, signature, label)
        content.extend(self.named(scheme.get('display_name'), scheme['name']))
        content.extend(markdown(scheme.get('description'), self.document))
        settings = [
            ([nodes.strong(name, name), nodes.Text(': '), *_literals(value if isinstance(value, list) else [value])])
            for name, value in (scheme.get('settings') or {}).items()
        ]
        adds = scheme.get('described_by') or {}
        query_string = adds.get('query_string')
        content.extend(
            fields(
                [
                    ('Settings', [_bullets([[nodes.paragraph('', '', *row)] for row in settings])] if settings else []),
                    ('Adds headers', self.parameters(adds.get('headers', {}))),
                    ('Adds query parameters', self.parameters(adds.get('query_parameters', {}))),
                    ('Adds a query string', self.typed(query_string) if query_string else []),
                    ('Annotations', self.annotations(scheme.get('annotations'))),
                ]
            )
        )
        responses = adds.get('responses') or {}
        if responses:
            content += nodes.rubric('Adds responses', 'Adds responses')
            # Shown as a method's are, but no target: a scheme's response has
            # no method to be named under.
            for status, response in responses.items():
                content.extend(self.response(None, status, response))
        return out

    # -- shapes ----------------------------------------------------------------

    def link(self, address: str | None, text: str | None = None) -> Node | None:
        """A link to the declaration at `address`, when that address is one."""
        declared = self.catalogue.declared_at(address) if address else None
        if declared is None:
            return None
        kind, key = declared
        return self.xref(kind, key, text or self.catalogue.label(key))

    def label(self, node: ShapeNode | None) -> list[Node]:
        """A type as one line: a link to a declaration, or what an anonymous one is."""
        if node is None:
            return [nodes.Text('any')]
        address = _address(node)
        if address is None:
            shape = cast('Shape', node)
            linked = self.link(shape.get('id'))
            return [linked] if linked is not None else self.shape_label(shape)
        linked = self.link(address)
        if linked is not None:
            return [linked]
        found = self.catalogue.tree.at(address)
        return self.label(found) if found is not None else [nodes.Text('any')]

    def shape_label(self, shape: Shape) -> list[Node]:
        """What a shape is, never itself: `array of Money`, `string | number`, `Entity`.

        Apart from `label` so a declaration's own signature can say what it
        is without linking to itself.
        """
        if shape['type'] == 'array':
            items = shape.get('items')
            return [nodes.Text('array of '), *self.label(items)] if items is not None else [nodes.Text('array')]
        if shape['type'] == 'union':
            out: list[Node] = []
            for position, member in enumerate(shape.get('any_of') or []):
                if position:
                    out.append(nodes.Text(' | '))
                out.extend(self.label(member))
            return out or [nodes.Text('union')]
        if shape['type'] == 'json':
            return [nodes.Text('JSON schema')]
        return self.supertypes(shape) or [nodes.Text(shape['type'])]

    def supertypes(self, shape: Shape) -> list[Node]:
        """Links to the declared types a shape extends, if it extends any."""
        out: list[Node] = []
        for parent in shape.get('inherits') or []:
            linked = self.link(_address(parent))
            if linked is None:
                continue
            if out:
                out.append(nodes.Text(', '))
            out.append(linked)
        return out

    def about(self, node: ShapeNode | None) -> list[Node]:
        """What a use of a type says beyond its label: nothing, for a declared one.

        An anonymous subtype of a declared type -- `type: Book` with a facet
        added -- arrives flattened, carrying `Book`'s description, facets and
        properties as its own. Only what differs from those is shown; the
        label already links to the rest.
        """
        if node is None or _address(node) is not None or not is_shape(node):
            return []
        shape = cast('Shape', node)
        own = shape.get('id')
        if own and self.catalogue.declared_at(own):
            return []
        beneath = self.declared_parents(shape)
        description = None if _inherited(shape, beneath, 'description') else shape.get('description')
        return [*markdown(description, self.document), *self.details(shape, beneath=beneath)]

    def details(self, shape: Shape, *, properties: bool = True, beneath: list[Shape] | None = None) -> list[Node]:
        """Constraints, allowed values, structure and examples of one shape.

        `beneath` are the declared types it extends: what it has from them is
        left out, since their entries say it.
        """
        beneath = beneath or []
        out: list[Node] = []
        facets = [
            (name, shape.get(name))
            for name in FACETS
            if shape.get(name) is not None and not _inherited(shape, beneath, name)
        ]
        if facets:
            row = nodes.paragraph()
            for position, (name, value) in enumerate(facets):
                if position:
                    row += nodes.Text(', ')
                row += nodes.emphasis(_camel(name), _camel(name))
                row += nodes.Text(' ')
                row.extend(_literals(cast('list[Json]', value if isinstance(value, list) else [value])))
            out.append(row)
        enum = None if _inherited(shape, beneath, 'enum') else shape.get('enum')
        if enum:
            out.append(nodes.paragraph('', '', nodes.Text('One of: '), *_literals(enum)))
        if 'default' in shape and not _inherited(shape, beneath, 'default'):
            out.append(nodes.paragraph('', '', nodes.Text('Default: '), *_literals([cast('Json', shape['default'])])))
        if not _inherited(shape, beneath, 'annotations'):
            out.extend(fields([('Annotations', self.annotations(shape.get('annotations')))]))
        if properties:
            out.extend(self.structure(shape, beneath))
        out.extend(self.examples(shape))
        return out

    def structure(self, shape: Shape, beneath: list[Shape]) -> list[Node]:
        """What an anonymous type holds: its own properties, items or members."""
        if shape['type'] == 'object':
            inherited = {
                name for parent in beneath if parent['type'] == 'object' for name in parent.get('properties') or {}
            }
            rows = [
                self.property_row(name, prop)
                for name, prop in (shape.get('properties') or {}).items()
                if name not in inherited
            ]
            rows.extend(
                self.property_row(f'/{pattern["pattern"]}/', cast('Property', {**pattern, 'required': False}))
                for pattern in (shape.get('pattern_properties') or {}).values()
            )
            return [_bullets(rows)] if rows else []
        if shape['type'] == 'array':
            inner = self.about(shape.get('items'))
            return [nodes.paragraph('', 'Each item:'), *inner] if inner else []
        if shape['type'] == 'union':
            rows = [
                [nodes.paragraph('', '', *self.label(member)), *self.about(member)]
                for member in shape.get('any_of') or []
            ]
            return [nodes.paragraph('', 'One of:'), _bullets(rows)] if rows else []
        return []

    def declared_parents(self, shape: Shape) -> list[Shape]:
        """The declared types a shape extends, expanded."""
        out: list[Shape] = []
        for parent in shape.get('inherits') or []:
            address = _address(parent)
            if address is None or self.catalogue.declared_at(address) is None:
                continue
            found = self.catalogue.tree.at(address)
            if found is not None:
                out.append(found)
        return out

    def property_row(self, name: str, prop: Property) -> list[Node]:
        lead = nodes.paragraph('', '', nodes.strong(name, name), nodes.Text(' : '), *self.label(prop['type']))
        if prop['required']:
            lead += nodes.emphasis(' required', ' required')
        return [lead, *self.about(prop['type'])]

    def examples(self, shape: Shape) -> list[Node]:
        found: list[tuple[str, Example]] = []
        if 'example' in shape:
            found.append(('Example', shape['example']))
        found.extend(
            (f'Example: {example.get("display_name") or name}', example)
            for name, example in (shape.get('examples') or {}).items()
        )
        out: list[Node] = []
        for title, example in found:
            out.append(nodes.rubric(title, title))
            out.extend(markdown(example.get('description'), self.document))
            out.append(_code(example['value']))
        return out

    # -- parts of a request ----------------------------------------------------

    def parameters(self, parameters: dict[str, Parameter], *, addressable: bool = False) -> list[Node]:
        rows = []
        for name, parameter in parameters.items():
            row = self.property_row(name, cast('Property', parameter))
            if addressable and self.index:
                self.target('base-uri-parameter', name, cast('Element', row[0]), f'{{{name}}}')
            rows.append(row)
        return [_bullets(rows)] if rows else []

    def base_parameters(self) -> list[Node]:
        """The base URI's parameters, linked: `{tenant}` is required and is not in the path."""
        names = list(self.catalogue.base_uri_parameters())
        links: list[Node] = []
        for position, name in enumerate(names):
            if position:
                links.append(nodes.Text(', '))
            links.append(self.xref('base-uri-parameter', name, f'{{{name}}}'))
        return [nodes.paragraph('', '', *links)] if links else []

    def typed(self, node: ShapeNode) -> list[Node]:
        return [nodes.paragraph('', '', *self.label(node)), *self.about(node)]

    def body(self, media: str, node: ShapeNode | None) -> list[Node]:
        lead = nodes.paragraph('', '', nodes.literal(media, media), nodes.Text(' : '), *self.label(node))
        return [lead, *self.about(node)]

    def security(self, secured_by: list[SecuredBy] | None) -> list[Node]:
        if not secured_by:
            return []
        row = nodes.paragraph()
        for position, requirement in enumerate(secured_by):
            if position:
                row += nodes.Text(' or ')
            if requirement['is_null']:
                row += nodes.Text('no authentication')
                continue
            name = requirement['name']
            row += self.link(requirement['declaration']) or nodes.literal(name, name)
            if requirement['scopes']:
                row += nodes.Text(' (scopes: ')
                row.extend(_literals(requirement['scopes']))
                row += nodes.Text(')')
        return [row]

    def annotations(self, applied: list[Applied] | None) -> list[Node]:
        rows = []
        for annotation in applied or []:
            name = f'({annotation["name"]})'
            row = nodes.paragraph('', '', self.link(annotation['type'], name) or nodes.literal(name, name))
            if annotation['value'] is not None:
                row += nodes.Text(' ')
                row.extend(_literals([annotation['value']]))
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
                text = f'{display} ({self.catalogue.title} {LABELS[kind]})'
                out.append(addnodes.index(entries=[('single', text, anchor, '', None)]))
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


def _bullets(rows: Iterable[Sequence[Node]]) -> nodes.bullet_list:
    out = nodes.bullet_list()
    for row in rows:
        out += nodes.list_item('', *row)
    return out


def _dash(text: str | None, name: str) -> list[Node]:
    return [nodes.Text(f' -- {text}')] if text and text != name else []


def _words(values: Sequence[str] | None) -> list[Node]:
    return [nodes.paragraph('', ', '.join(values))] if values else []


def _literals(values: Iterable[Json] | None) -> list[Node]:
    out: list[Node] = []
    for position, value in enumerate(values or []):
        if position:
            out.append(nodes.Text(', '))
        text = value if isinstance(value, str) else json.dumps(value)
        out.append(nodes.literal(text, text))
    return out


def _code(value: Json) -> nodes.literal_block:
    """An example as a code block: JSON unless it is a string, which is shown as written."""
    if isinstance(value, str):
        return nodes.literal_block(value, value, language='text')
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return nodes.literal_block(text, text, language='json')


def _address(node: object) -> str | None:
    """What a link or a recursion marker points at; `None` for anything expanded."""
    if is_ref(node):
        return node['$ref']
    if is_recursion(node):
        return node['head']['$ref']
    return None


def _inherited(shape: Shape, beneath: list[Shape], key: str) -> bool:
    """Whether a value is one a declared supertype already has."""
    value = cast('dict[str, object]', shape).get(key)
    return any(cast('dict[str, object]', parent).get(key) == value for parent in beneath)


def _camel(name: str) -> str:
    head, *rest = name.split('_')
    return head + ''.join(part.title() for part in rest)


def _phrase(status: str) -> str:
    try:
        return HTTPStatus(int(status)).phrase
    except ValueError:
        return ''


__all__ = ['DETAILS', 'Detail', 'Writer', 'fields']
