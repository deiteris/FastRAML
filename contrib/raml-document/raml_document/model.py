"""A typed model of the RAML 1.0 document an emitter writes.

This is the *authoring* side of RAML, and deliberately not `fastraml.views.tree`.
That view is a projection of a parsed model for reading: it requires
`ParseOptions(unwrap=True)` so it describes the **effective** document with
inheritance already flattened, its cross-references are addresses rather than
names, and its `Json` type is untyped by construction. An emitter needs the
opposite of all three -- it writes `type: Pet` and lets a parser flatten it, it
refers to types by the names it declared them under, and its whole purpose here
is to be checked.

Scope is what an emitter emits. RAML has more nodes than these; a node nobody
writes is a node nothing keeps honest, so the model grows when an emitter needs
it to. Every class renders itself, so the RAML spelling of a facet -- `camelCase`
names, the bare-string shorthand, the order keys appear in -- is stated once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import yaml

__all__ = [
    'UNSET',
    'Body',
    'Document',
    'Method',
    'Parameters',
    'Resource',
    'Response',
    'SecuredBy',
    'SecurityScheme',
    'TypeDecl',
    'Unset',
    'Yaml',
]

#: Anything `yaml.safe_dump` accepts, which is also anything a RAML facet holds.
type Yaml = str | int | float | bool | list[Yaml] | dict[str, Yaml] | None

#: A RAML type declaration keyed by name: `properties:`, `queryParameters:`,
#: `headers:`, `uriParameters:`, `baseUriParameters:` are all this shape.
type Parameters = dict[str, 'TypeDecl']


class Unset:
    """The absence of a facet, where `None` is a value the facet can take.

    `default: ~` and `discriminatorValue: ~` are both legal RAML meaning *null*,
    so neither field can use `None` to mean "not written".
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return 'UNSET'


UNSET: Final = Unset()

#: Facet fields on `TypeDecl` in the order they are rendered, as
#: `(attribute, RAML spelling)`. One list so the spelling and the order are
#: stated once rather than at each `if` that emits one.
_FACETS: Final[tuple[tuple[str, str], ...]] = (
    ('display_name', 'displayName'),
    ('description', 'description'),
    ('pattern', 'pattern'),
    ('min_length', 'minLength'),
    ('max_length', 'maxLength'),
    ('minimum', 'minimum'),
    ('maximum', 'maximum'),
    ('multiple_of', 'multipleOf'),
    ('min_items', 'minItems'),
    ('max_items', 'maxItems'),
    ('unique_items', 'uniqueItems'),
    ('min_properties', 'minProperties'),
    ('max_properties', 'maxProperties'),
    ('discriminator', 'discriminator'),
    ('additional_properties', 'additionalProperties'),
)


@dataclass(slots=True)
class TypeDecl:
    """One RAML type declaration, wherever a declaration may stand.

    A property, an array's `items`, a body's schema, a query parameter and a
    named entry in `types:` are the same node in RAML, so they are one class
    here. `type` holds a *type expression* -- `string`, `Pet`, `Cat | Dog`,
    `string[]` -- because that is what RAML accepts in the position, and
    resolving it is the parser's job rather than the emitter's.
    """

    type: str | None = None
    display_name: str | None = None
    description: str | None = None
    required: bool | None = None
    default: Yaml | Unset = UNSET
    enum: list[Yaml] | None = None
    examples: dict[str, Yaml] | None = None

    # object
    properties: Parameters = field(default_factory=dict)
    additional_properties: bool | None = None
    min_properties: int | None = None
    max_properties: int | None = None
    discriminator: str | None = None
    discriminator_value: Yaml | Unset = UNSET

    # array
    items: TypeDecl | None = None
    min_items: int | None = None
    max_items: int | None = None
    unique_items: bool | None = None

    # string and numbers
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    multiple_of: float | int | None = None

    def render(self) -> Yaml:
        """The declaration as RAML, collapsed to a bare type expression if it can be.

        `TypeDecl(type='string')` is `string`, not `{type: string}`. RAML accepts
        both and the short form is what an author writes, so collapsing here is
        what keeps it out of every call site.
        """
        out: dict[str, Yaml] = {}
        if self.type is not None:
            out['type'] = self.type
        for attribute, spelling in _FACETS:
            value = getattr(self, attribute)
            if value is not None:
                out[spelling] = value
        if self.enum is not None:
            out['enum'] = list(self.enum)
        if self.items is not None:
            out['items'] = self.items.render()
        if self.properties:
            out['properties'] = {name: decl.render() for name, decl in self.properties.items()}
        if not isinstance(self.discriminator_value, Unset):
            out['discriminatorValue'] = self.discriminator_value
        # Last, and in this order: `required: false` and `default:` read as
        # qualifications of everything above them.
        if self.required is not None:
            out['required'] = self.required
        if not isinstance(self.default, Unset):
            out['default'] = self.default
        if self.examples is not None:
            out['examples'] = dict(self.examples)
        if len(out) == 1 and self.type is not None:
            return self.type
        return out


@dataclass(slots=True)
class Body:
    """A request or response body: one declaration per media type."""

    content: dict[str, TypeDecl] = field(default_factory=dict)

    def render(self) -> Yaml:
        return {media: decl.render() for media, decl in self.content.items()}


@dataclass(slots=True)
class Response:
    """One response, under its status code."""

    description: str | None = None
    body: Body | None = None

    def render(self) -> Yaml:
        out: dict[str, Yaml] = {}
        if self.description is not None:
            out['description'] = self.description
        if self.body is not None and self.body.content:
            out['body'] = self.body.render()
        return out


@dataclass(slots=True)
class SecuredBy:
    """One entry of a `securedBy:` list.

    Renders as a bare name where there are no scopes, and as
    `{name: {scopes: [...]}}` where there are -- the two spellings RAML uses.
    """

    scheme: str
    scopes: list[str] = field(default_factory=list)

    def render(self) -> Yaml:
        return {self.scheme: {'scopes': list(self.scopes)}} if self.scopes else self.scheme


@dataclass(slots=True)
class Method:
    """One HTTP method on a resource."""

    display_name: str | None = None
    description: str | None = None
    query_parameters: Parameters = field(default_factory=dict)
    headers: Parameters = field(default_factory=dict)
    body: Body | None = None
    #: Keyed by the status code as a number, so `sorted` orders 200, 404, 1000
    #: rather than '1000', '200', '404'. Rendered as a string -- both spellings
    #: parse and both reach the model as `'200'`.
    responses: dict[int, Response] = field(default_factory=dict)
    secured_by: list[SecuredBy] = field(default_factory=list)

    def render(self) -> Yaml:
        out: dict[str, Yaml] = {}
        if self.display_name is not None:
            out['displayName'] = self.display_name
        if self.description is not None:
            out['description'] = self.description
        if self.query_parameters:
            out['queryParameters'] = {name: decl.render() for name, decl in self.query_parameters.items()}
        if self.headers:
            out['headers'] = {name: decl.render() for name, decl in self.headers.items()}
        if self.body is not None and self.body.content:
            out['body'] = self.body.render()
        if self.responses:
            out['responses'] = {str(code): self.responses[code].render() for code in sorted(self.responses)}
        if self.secured_by:
            out['securedBy'] = [entry.render() for entry in self.secured_by]
        return out


@dataclass(slots=True)
class Resource:
    """One `/segment:` node, its methods, and the resources nested under it."""

    uri_parameters: Parameters = field(default_factory=dict)
    methods: dict[str, Method] = field(default_factory=dict)
    children: dict[str, Resource] = field(default_factory=dict)

    def at(self, path: str) -> Resource:
        """The resource at `path`, creating each segment that is missing.

        `/books/{isbn}` nests as RAML nests it, `/books:` then `/{isbn}:`, which
        is also how a reader finds it.

        **`/` is a resource and not this node.** An API answering on its own
        base URI writes `/:` in RAML, which is a relative URI like any other;
        returning the document root instead would put `get:` beside `title:`,
        where RAML has no method node at all and a parser rejects the document.
        """
        segments = [part for part in path.split('/') if part]
        node = self if segments else self.children.setdefault('/', Resource())
        for segment in segments:
            node = node.children.setdefault(f'/{segment}', Resource())
        return node

    def render(self) -> dict[str, Yaml]:
        out: dict[str, Yaml] = {}
        if self.uri_parameters:
            out['uriParameters'] = {name: decl.render() for name, decl in self.uri_parameters.items()}
        for verb in sorted(self.methods):
            out[verb] = self.methods[verb].render()
        for segment, child in self.children.items():
            out[segment] = child.render()
        return out


@dataclass(slots=True)
class SecurityScheme:
    """One entry of `securitySchemes:`.

    `settings` and `describedBy` are passed through rather than modelled: their
    shape depends on `type`, the six cases share nothing, and an emitter builds
    each from a source that already knows which one it has.
    """

    type: str
    description: str | None = None
    settings: dict[str, Yaml] = field(default_factory=dict)
    described_by: dict[str, Yaml] = field(default_factory=dict)

    def render(self) -> Yaml:
        out: dict[str, Yaml] = {'type': self.type}
        if self.description is not None:
            out['description'] = self.description
        if self.settings:
            out['settings'] = dict(self.settings)
        if self.described_by:
            out['describedBy'] = dict(self.described_by)
        return out


@dataclass(slots=True)
class Document:
    """A RAML 1.0 API definition: the root node and everything under it."""

    title: str
    version: str | None = None
    description: str | None = None
    base_uri: str | None = None
    base_uri_parameters: Parameters = field(default_factory=dict)
    types: dict[str, TypeDecl] = field(default_factory=dict)
    security_schemes: dict[str, SecurityScheme] = field(default_factory=dict)
    root: Resource = field(default_factory=Resource)

    def render(self) -> dict[str, Yaml]:
        """The document as nested mappings, ready for `yaml.safe_dump`."""
        out: dict[str, Yaml] = {'title': self.title}
        if self.version is not None:
            out['version'] = self.version
        if self.description is not None:
            out['description'] = self.description
        if self.base_uri is not None:
            out['baseUri'] = self.base_uri
        if self.base_uri_parameters:
            out['baseUriParameters'] = {name: decl.render() for name, decl in self.base_uri_parameters.items()}
        if self.types:
            out['types'] = {name: decl.render() for name, decl in self.types.items()}
        if self.security_schemes:
            out['securitySchemes'] = {name: scheme.render() for name, scheme in self.security_schemes.items()}
        out.update(self.root.render())
        return out

    def to_raml(self) -> str:
        """The document as RAML source, header and all."""
        body = yaml.safe_dump(self.render(), sort_keys=False, allow_unicode=True, default_flow_style=False)
        return f'#%RAML 1.0\n{body}'
