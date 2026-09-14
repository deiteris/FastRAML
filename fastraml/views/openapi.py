"""A finished RAML API model as a typed OpenAPI 3.0.3 document.

The model and mapping follow go-raml's ``converter/oas3doc.go`` and
``converter/oas3conv.go``. This is a projection, not a parser pass: RAML has
already resolved traits, resource types, security, and inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from fractions import Fraction
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from fastraml.parser.security import (
    TYPE_BASIC,
    TYPE_DIGEST,
    TYPE_NULL,
    TYPE_OAUTH1,
    TYPE_OAUTH2,
    TYPE_PASS_THROUGH,
)
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import (
    AnyShape,
    BooleanShape,
    DateOnlyShape,
    DateTimeOnlyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    StringShape,
    TimeOnlyShape,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.directives import SecurityScheme
    from fastraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from fastraml.parser.fragments import APIFragment
    from fastraml.parser.security import SecuritySchemeDefinition, SecuritySchemeSettings
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Parameter

__all__ = [
    'OAS3XML',
    'OPENAPI_VERSION',
    'OAS3Components',
    'OAS3Discriminator',
    'OAS3Document',
    'OAS3Header',
    'OAS3Info',
    'OAS3MediaType',
    'OAS3OAuthFlow',
    'OAS3OAuthFlows',
    'OAS3Operation',
    'OAS3Parameter',
    'OAS3PathItem',
    'OAS3RequestBody',
    'OAS3Response',
    'OAS3Schema',
    'OAS3SecurityRequirement',
    'OAS3SecurityScheme',
    'OAS3Server',
    'OAS3ServerVariable',
    'OAS3Tag',
    'OpenAPIConversion',
    'to_openapi',
]

OPENAPI_VERSION: Final = '3.0.3'
_COMPONENT_REF: Final = '#/components/schemas/'
_MISSING: Final = object()
_RFC2616: Final = (
    r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-3][0-9]) '
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) ([0-9]{4})'
    r' ([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9] GMT$'
)
_DATETIME_ONLY: Final = r'^[0-9]{4}-(?:0[0-9]|1[0-2])-(?:[0-2][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$'


@dataclass(slots=True, eq=False)
class _OAS3Object:
    """A typed object with one ordered, non-copying wire projection."""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        extensions: dict[str, Any] = {}
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            if descriptor.name == 'extensions':
                extensions = value
                continue
            required = bool(descriptor.metadata.get('required'))
            keep_empty = bool(descriptor.metadata.get('keep_empty')) and value is not None
            keep_null = bool(descriptor.metadata.get('keep_null')) and value is not _MISSING
            if not required and not keep_empty and not keep_null and _empty(value):
                continue
            name = str(descriptor.metadata.get('name', descriptor.name))
            result[name] = _plain(value)
        result.update(extensions)
        return result


def _wire(
    *, name: str | None = None, required: bool = False, keep_empty: bool = False, keep_null: bool = False
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if name is not None:
        metadata['name'] = name
    if required:
        metadata['required'] = True
    if keep_empty:
        metadata['keep_empty'] = True
    if keep_null:
        metadata['keep_null'] = True
    return metadata


def _empty(value: Any) -> bool:
    if value is _MISSING or value is None or value is False:
        return True
    return isinstance(value, (str, list, dict)) and not value


def _plain(value: Any) -> Any:
    if isinstance(value, _OAS3Object):
        return value.to_dict()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


type OAS3SecurityRequirement = dict[str, list[str]]


@dataclass(slots=True, eq=False)
class OAS3Info(_OAS3Object):
    title: str = field(default='', metadata=_wire(required=True))
    description: str = ''
    version: str = field(default='', metadata=_wire(required=True))


@dataclass(slots=True, eq=False)
class OAS3ServerVariable(_OAS3Object):
    default: str = field(default='', metadata=_wire(required=True))
    description: str = ''
    enum: list[str] = field(default_factory=list)


@dataclass(slots=True, eq=False)
class OAS3Server(_OAS3Object):
    url: str = field(default='', metadata=_wire(required=True))
    description: str = ''
    variables: dict[str, OAS3ServerVariable] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3XML(_OAS3Object):
    name: str = ''
    namespace: str = ''
    prefix: str = ''
    attribute: bool = False
    wrapped: bool = False


@dataclass(slots=True, eq=False)
class OAS3Schema(_OAS3Object):
    ref: str = field(default='', metadata=_wire(name='$ref'))
    all_of: list[OAS3Schema] = field(default_factory=list, metadata=_wire(name='allOf'))
    any_of: list[OAS3Schema] = field(default_factory=list, metadata=_wire(name='anyOf'))
    one_of: list[OAS3Schema] = field(default_factory=list, metadata=_wire(name='oneOf'))
    not_: OAS3Schema | None = field(default=None, metadata=_wire(name='not'))
    type: str = ''
    nullable: bool = False
    format: str = ''
    enum: list[Any] = field(default_factory=list)
    properties: dict[str, OAS3Schema] = field(default_factory=dict)
    additional_properties: bool | None = field(default=None, metadata=_wire(name='additionalProperties'))
    required: list[str] = field(default_factory=list)
    min_properties: int | None = field(default=None, metadata=_wire(name='minProperties'))
    max_properties: int | None = field(default=None, metadata=_wire(name='maxProperties'))
    discriminator: OAS3Discriminator | None = None
    pattern_properties: dict[str, OAS3Schema] = field(default_factory=dict, metadata=_wire(name='patternProperties'))
    items: OAS3Schema | None = None
    min_items: int | None = field(default=None, metadata=_wire(name='minItems'))
    max_items: int | None = field(default=None, metadata=_wire(name='maxItems'))
    unique_items: bool | None = field(default=None, metadata=_wire(name='uniqueItems'))
    minimum: int | float | None = None
    maximum: int | float | None = None
    multiple_of: int | float | None = field(default=None, metadata=_wire(name='multipleOf'))
    min_length: int | None = field(default=None, metadata=_wire(name='minLength'))
    max_length: int | None = field(default=None, metadata=_wire(name='maxLength'))
    pattern: str = ''
    title: str = ''
    description: str = ''
    default: Any = field(default=_MISSING, metadata=_wire(keep_null=True))
    example: Any = field(default=_MISSING, metadata=_wire(keep_null=True))
    read_only: bool = field(default=False, metadata=_wire(name='readOnly'))
    write_only: bool = field(default=False, metadata=_wire(name='writeOnly'))
    xml: OAS3XML | None = None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3Discriminator(_OAS3Object):
    property_name: str = field(default='', metadata=_wire(name='propertyName', required=True))
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3Parameter(_OAS3Object):
    name: str = field(default='', metadata=_wire(required=True))
    in_: str = field(default='', metadata=_wire(name='in', required=True))
    description: str = ''
    required: bool = False
    deprecated: bool = False
    allow_empty_value: bool = field(default=False, metadata=_wire(name='allowEmptyValue'))
    schema: OAS3Schema | None = None
    example: Any = field(default=_MISSING, metadata=_wire(keep_null=True))


@dataclass(slots=True, eq=False)
class OAS3MediaType(_OAS3Object):
    schema: OAS3Schema | None = None
    example: Any = field(default=_MISSING, metadata=_wire(keep_null=True))


@dataclass(slots=True, eq=False)
class OAS3RequestBody(_OAS3Object):
    description: str = ''
    required: bool = False
    content: dict[str, OAS3MediaType] = field(default_factory=dict, metadata=_wire(required=True))


@dataclass(slots=True, eq=False)
class OAS3Header(_OAS3Object):
    description: str = ''
    required: bool = False
    schema: OAS3Schema | None = None


@dataclass(slots=True, eq=False)
class OAS3Response(_OAS3Object):
    description: str = field(default='', metadata=_wire(required=True))
    headers: dict[str, OAS3Header] = field(default_factory=dict)
    content: dict[str, OAS3MediaType] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3Operation(_OAS3Object):
    tags: list[str] = field(default_factory=list)
    summary: str = ''
    description: str = ''
    operation_id: str = field(default='', metadata=_wire(name='operationId'))
    parameters: list[OAS3Parameter] = field(default_factory=list)
    request_body: OAS3RequestBody | None = field(default=None, metadata=_wire(name='requestBody'))
    responses: dict[str, OAS3Response] = field(default_factory=dict, metadata=_wire(required=True))
    security: list[OAS3SecurityRequirement] | None = field(default=None, metadata=_wire(keep_empty=True))
    deprecated: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3PathItem(_OAS3Object):
    summary: str = ''
    description: str = ''
    get: OAS3Operation | None = None
    put: OAS3Operation | None = None
    post: OAS3Operation | None = None
    delete: OAS3Operation | None = None
    options: OAS3Operation | None = None
    head: OAS3Operation | None = None
    patch: OAS3Operation | None = None
    trace: OAS3Operation | None = None
    parameters: list[OAS3Parameter] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3OAuthFlow(_OAS3Object):
    authorization_url: str = field(default='', metadata=_wire(name='authorizationUrl'))
    token_url: str = field(default='', metadata=_wire(name='tokenUrl'))
    refresh_url: str = field(default='', metadata=_wire(name='refreshUrl'))
    scopes: dict[str, str] = field(default_factory=dict, metadata=_wire(required=True))


@dataclass(slots=True, eq=False)
class OAS3OAuthFlows(_OAS3Object):
    implicit: OAS3OAuthFlow | None = None
    password: OAS3OAuthFlow | None = None
    client_credentials: OAS3OAuthFlow | None = field(default=None, metadata=_wire(name='clientCredentials'))
    authorization_code: OAS3OAuthFlow | None = field(default=None, metadata=_wire(name='authorizationCode'))


@dataclass(slots=True, eq=False)
class OAS3SecurityScheme(_OAS3Object):
    type: str = field(default='', metadata=_wire(required=True))
    description: str = ''
    name: str = ''
    in_: str = field(default='', metadata=_wire(name='in'))
    scheme: str = ''
    bearer_format: str = field(default='', metadata=_wire(name='bearerFormat'))
    flows: OAS3OAuthFlows | None = None
    open_id_connect_url: str = field(default='', metadata=_wire(name='openIdConnectUrl'))
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class OAS3Components(_OAS3Object):
    schemas: dict[str, OAS3Schema] = field(default_factory=dict)
    security_schemes: dict[str, OAS3SecurityScheme] = field(
        default_factory=dict, metadata=_wire(name='securitySchemes')
    )


@dataclass(slots=True, eq=False)
class OAS3Tag(_OAS3Object):
    name: str = field(default='', metadata=_wire(required=True))
    description: str = ''


@dataclass(slots=True, eq=False)
class OAS3Document(_OAS3Object):
    openapi: str = field(default=OPENAPI_VERSION, metadata=_wire(required=True))
    info: OAS3Info = field(default_factory=OAS3Info, metadata=_wire(required=True))
    servers: list[OAS3Server] = field(default_factory=list)
    paths: dict[str, OAS3PathItem] = field(default_factory=dict, metadata=_wire(required=True))
    components: OAS3Components = field(default_factory=OAS3Components)
    security: list[OAS3SecurityRequirement] = field(default_factory=list)
    tags: list[OAS3Tag] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)


class _SchemaConversion:
    """RAML shapes as OAS 3.0 Schema Objects, sharing one component table."""

    __slots__ = ('components', 'dropped', 'named')

    def __init__(self, named: Mapping[int, tuple[str, BaseShape]], dropped: list[str]) -> None:
        self.named = named
        self.dropped = dropped
        self.components: dict[str, OAS3Schema] = {}

    def inline(self, base: BaseShape, at: str) -> OAS3Schema:
        base._assert_unwrapped()  # noqa: SLF001 - the view requires the finished model
        referent = base.alias or (base.inherits[0] if len(base.inherits) == 1 else None)
        declared = self.named.get(referent.id) if referent is not None else None
        if declared is not None:
            name, shape = declared
            self._component(name, shape)
            return OAS3Schema(ref=f'{_COMPONENT_REF}{name}')
        return self._body(base, at)

    def _component(self, name: str, base: BaseShape) -> None:
        if name in self.components:
            return
        self.components[name] = OAS3Schema()
        self.components[name] = self._body(base, f'components.schemas.{name}')

    def _body(self, base: BaseShape, at: str) -> OAS3Schema:
        if isinstance(base.shape, JsonShape):
            projected = base.shape.as_shape()
            if projected is None:
                self.dropped.append(f'{at}: JSON Schema could not be projected; emitted without a type constraint')
                schema = self._common(base)
            else:
                schema = self._direct(projected, at)
                self._overlay_common(schema, base)
        else:
            schema = self._direct(base, at)
        self._decorate(schema, base, at)
        return schema

    def _direct(self, base: BaseShape, at: str) -> OAS3Schema:  # noqa: PLR0911, PLR0912, PLR0915
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            head = shape.head
            if head.name and head.name not in self.components:
                self.components[head.name] = OAS3Schema()
                self.components[head.name] = self._body(head, at)
            return OAS3Schema(ref=f'{_COMPONENT_REF}{head.name}') if head.name else OAS3Schema()
        schema = self._common(base)
        if isinstance(shape, ObjectShape):
            schema.type = 'object'
            schema.min_properties = _value(shape.min_properties)
            schema.max_properties = _value(shape.max_properties)
            schema.additional_properties = _value(shape.additional_properties)
            schema.properties = {
                name: self.inline(prop.base, f'{at}.{name}') for name, prop in (shape.properties or {}).items()
            }
            schema.required = [name for name, prop in (shape.properties or {}).items() if prop.required]
            schema.pattern_properties = {
                name: self.inline(prop.base, f'{at}.{name}') for name, prop in (shape.pattern_properties or {}).items()
            }
            if shape.discriminator is not None:
                schema.discriminator = OAS3Discriminator(property_name=shape.discriminator.value)
            return schema
        if isinstance(shape, ArrayShape):
            schema.type = 'array'
            schema.min_items = _value(shape.min_items)
            schema.max_items = _value(shape.max_items)
            schema.unique_items = _value(shape.unique_items)
            schema.items = self.inline(shape.items, f'{at}[]') if shape.items is not None else None
            return schema
        if isinstance(shape, UnionShape):
            return self._union(schema, shape, at)
        if isinstance(shape, StringShape):
            schema.type = 'string'
            schema.min_length = _value(shape.min_length)
            schema.max_length = _value(shape.max_length)
            schema.pattern = shape.pattern.value.pattern if shape.pattern is not None else ''
            return schema
        if isinstance(shape, IntegerShape):
            self._numeric(schema, shape, 'integer')
            if shape.format is not None:
                self._integer_format(schema, shape.format.value)
            return schema
        if isinstance(shape, NumberShape):
            self._numeric(schema, shape, 'number')
            schema.format = shape.format.value if shape.format is not None else ''
            return schema
        if isinstance(shape, BooleanShape):
            schema.type = 'boolean'
            return schema
        if isinstance(shape, NilShape):
            schema.nullable = True
            return schema
        if isinstance(shape, FileShape):
            schema.type = 'string'
            schema.format = 'binary'
            schema.min_length = _value(shape.min_length)
            schema.max_length = _value(shape.max_length)
            if shape.file_types:
                self.dropped.append(f'{at}: fileTypes has no OpenAPI 3.0 Schema Object equivalent')
            return schema
        if isinstance(shape, DateTimeShape):
            schema.type = 'string'
            if shape.format is not None and shape.format.value == 'rfc2616':
                schema.pattern = _RFC2616
            else:
                schema.format = 'date-time'
            return schema
        if isinstance(shape, DateTimeOnlyShape):
            schema.type = 'string'
            schema.pattern = _DATETIME_ONLY
            return schema
        if isinstance(shape, DateOnlyShape):
            schema.type = 'string'
            schema.format = 'date'
            return schema
        if isinstance(shape, TimeOnlyShape):
            schema.type = 'string'
            schema.format = 'time'
            return schema
        if shape is not None and not isinstance(shape, AnyShape):
            self.dropped.append(f'{at}: {type(shape).__name__} has no OpenAPI 3.0 form')
        return schema

    def _union(self, schema: OAS3Schema, shape: UnionShape, at: str) -> OAS3Schema:
        non_nil = [member for member in shape.any_of or () if not isinstance(member.shape, NilShape)]
        schema.nullable = len(non_nil) != len(shape.any_of or ())
        if len(non_nil) == 1:
            inner = self.inline(non_nil[0], f'{at}|')
            self._overlay_common(inner, schema)
            inner.nullable = schema.nullable
            return inner
        schema.any_of = [self.inline(member, f'{at}|') for member in non_nil]
        return schema

    def _common(self, base: BaseShape) -> OAS3Schema:
        schema = OAS3Schema()
        schema.title = base.display_name.value if base.display_name is not None else ''
        schema.description = base.description.value if base.description is not None else ''
        schema.default = base.default.raw if base.default is not None else _MISSING
        examples = list(_examples(base))
        schema.example = examples[0] if examples else _MISSING
        schema.enum = [member.raw for member in base.enum or ()]
        return schema

    @staticmethod
    def _overlay_common(target: OAS3Schema, source: BaseShape | OAS3Schema) -> None:
        if isinstance(source, OAS3Schema):
            title, description = source.title, source.description
            default, example, enum = source.default, source.example, source.enum
            extensions, xml = source.extensions, source.xml
        else:
            title = source.display_name.value if source.display_name is not None else ''
            description = source.description.value if source.description is not None else ''
            default = source.default.raw if source.default is not None else _MISSING
            examples = list(_examples(source))
            example = examples[0] if examples else _MISSING
            enum = [member.raw for member in source.enum or ()]
            extensions, xml = {}, None
        target.title = title or target.title
        target.description = description or target.description
        target.default = default if default is not _MISSING else target.default
        target.example = example if example is not _MISSING else target.example
        target.enum = enum or target.enum
        target.extensions.update(extensions)
        target.xml = xml or target.xml

    def _decorate(self, schema: OAS3Schema, base: BaseShape, at: str) -> None:
        if base.xml is not None:
            schema.xml = OAS3XML(
                name=_facet(base.xml.name),
                namespace=_facet(base.xml.namespace),
                prefix=_facet(base.xml.prefix),
                attribute=_value(base.xml.attribute) or False,
                wrapped=_value(base.xml.wrapped) or False,
            )
        _extensions(schema.extensions, base.annotations)
        for name in base.custom_facets:
            self.dropped.append(f'{at}: custom facet {name!r} has no OpenAPI 3.0 equivalent')

    @staticmethod
    def _numeric(schema: OAS3Schema, shape: IntegerShape | NumberShape, kind: str) -> None:
        schema.type = kind
        schema.minimum = _number(_value(shape.minimum))
        schema.maximum = _number(_value(shape.maximum))
        schema.multiple_of = _number(_value(shape.multiple_of))

    @staticmethod
    def _integer_format(schema: OAS3Schema, spelling: str) -> None:
        if spelling == 'int8':
            schema.minimum = -128 if schema.minimum is None else schema.minimum
            schema.maximum = 127 if schema.maximum is None else schema.maximum
        elif spelling == 'int16':
            schema.minimum = -32768 if schema.minimum is None else schema.minimum
            schema.maximum = 32767 if schema.maximum is None else schema.maximum
        elif spelling in {'int', 'int32'}:
            schema.format = 'int32'
        elif spelling in {'long', 'int64'}:
            schema.format = 'int64'


class OpenAPIConversion:
    """One RAML API to OpenAPI conversion and the information it dropped."""

    __slots__ = ('dropped', 'schema')

    def __init__(self) -> None:
        self.dropped: list[str] = []
        self.schema: _SchemaConversion | None = None

    def convert(self, raml: Raml) -> OAS3Document:
        """Convert an unwrapped API parse to a typed OpenAPI 3.0.3 document."""
        from fastraml.parser.fragments import APIFragment  # noqa: PLC0415 - keeps import-time cost low

        if not raml.is_unwrapped:
            msg = 'OpenAPI export needs an unwrapped model: parse with ParseOptions(unwrap=True)'
            raise AssertionError(msg)
        api = raml.entry_point
        if not isinstance(api, APIFragment):
            msg = 'OpenAPI export needs a RAML API fragment'
            raise TypeError(msg)

        named = {base.id: (name, base) for name, base in api.types.items()}
        self.schema = _SchemaConversion(named, self.dropped)
        document = OAS3Document(
            info=OAS3Info(
                title=_facet(api.title),
                version=_facet(api.version),
                description=_facet(api.description),
            )
        )
        self._servers(api, document)
        document.tags = [
            OAS3Tag(name=_facet(item.title), description=_facet(item.content)) for item in api.documentation
        ]
        document.security = [_security_requirement(item) for item in raml.global_secured_by]
        _extensions(document.extensions, api.annotations)

        document.paths = {
            endpoint.full_uri: self._endpoint(endpoint) for endpoint in raml.endpoints.values() if endpoint.operations
        }
        document.components.security_schemes = {
            name: self._security_scheme(definition.resolved(), f'components.securitySchemes.{name}')
            for name, definition in api.security_schemes.items()
        }
        document.components.schemas = self.schema.components
        return document

    def _servers(self, api: APIFragment, document: OAS3Document) -> None:
        if api.base_uri is None:
            return
        variables = {
            name: self._server_variable(name, parameter, api) for name, parameter in api.base_uri_parameters.items()
        }
        if '{version}' in api.base_uri.value and 'version' not in variables:
            variables['version'] = OAS3ServerVariable(default=_facet(api.version))
        document.servers = [OAS3Server(url=api.base_uri.value, variables=variables)]

    def _server_variable(self, name: str, parameter: Parameter, api: APIFragment) -> OAS3ServerVariable:
        base = parameter.base
        default = api.version.value if name == 'version' and api.version is not None else ''
        if not default and base.default is not None and isinstance(base.default.raw, str):
            default = base.default.raw
        enum = [item.raw for item in base.enum or () if isinstance(item.raw, str)]
        if not default:
            default = enum[0] if enum else name
            self.dropped.append(
                f'servers.variables.{name}: OpenAPI requires a default; used {default!r} because RAML declared none'
            )
        return OAS3ServerVariable(default=default, description=_facet(base.description), enum=enum)

    def _endpoint(self, endpoint: EndPoint) -> OAS3PathItem:
        item = OAS3PathItem(summary=_facet(endpoint.display_name), description=_facet(endpoint.description))
        item.parameters = [
            self._parameter(parameter, 'path', required=True) for parameter in endpoint.uri_parameters.values()
        ]
        for method, operation in endpoint.operations.items():
            setattr(item, method.lower(), self._operation(operation))
        _extensions(item.extensions, endpoint.annotations)
        return item

    def _operation(self, operation: Operation) -> OAS3Operation:
        node = OAS3Operation(summary=_facet(operation.display_name), description=_facet(operation.description))
        request = operation.request
        if request is not None:
            node.parameters = [
                *(self._parameter(item, 'query') for item in request.query_parameters.values()),
                *(self._parameter(item, 'header') for item in request.headers.values()),
            ]
            if request.query_string is not None:
                self._query_string(request.query_string, node)
            node.request_body = self._request_body(request)
        node.responses = {code: self._response(response) for code, response in operation.responses.items()}
        if not node.responses:
            node.responses['default'] = OAS3Response(description='Success')
        if operation.explicit_secured_by or operation.secured_by:
            node.security = [_security_requirement(item) for item in operation.secured_by]
        _extensions(node.extensions, operation.annotations)
        return node

    def _query_string(self, base: BaseShape, operation: OAS3Operation) -> None:
        if isinstance(base.shape, ObjectShape) and base.shape.properties:
            operation.parameters.extend(
                self._parameter_from_shape(name, prop.base, 'query', required=prop.required)
                for name, prop in base.shape.properties.items()
            )
            return
        operation.extensions['x-query-string'] = self._shape(base, 'paths.queryString').to_dict()

    def _parameter(self, parameter: Parameter, binding: str, *, required: bool | None = None) -> OAS3Parameter:
        return self._parameter_from_shape(
            parameter.name,
            parameter.base,
            binding,
            required=parameter.required if required is None else required,
        )

    def _parameter_from_shape(self, name: str, base: BaseShape, binding: str, *, required: bool) -> OAS3Parameter:
        return OAS3Parameter(
            name=name,
            in_=binding,
            required=required,
            schema=self._shape(base, f'parameters.{binding}.{name}'),
            description=_facet(base.description),
        )

    def _request_body(self, request: Request) -> OAS3RequestBody | None:
        if not request.bodies:
            return None
        return OAS3RequestBody(content={media: self._media_type(body) for media, body in request.bodies.items()})

    def _response(self, response: Response) -> OAS3Response:
        description = _facet(response.description) or _facet(response.display_name) or _status_text(response.code)
        node = OAS3Response(description=description)
        node.headers = {
            name: self._header(parameter, f'responses.{response.code}.headers.{name}')
            for name, parameter in response.headers.items()
        }
        node.content = {media: self._media_type(body) for media, body in response.bodies.items()}
        _extensions(node.extensions, response.annotations)
        return node

    def _header(self, parameter: Parameter, at: str) -> OAS3Header:
        return OAS3Header(
            description=_facet(parameter.base.description),
            required=parameter.required,
            schema=self._shape(parameter.base, at),
        )

    def _media_type(self, body: Body) -> OAS3MediaType:
        schema = None if body.shape is None else self._shape(body.shape, f'bodies.{body.media_type}')
        return OAS3MediaType(schema=schema)

    def _shape(self, base: BaseShape, at: str) -> OAS3Schema:
        if self.schema is None:  # pragma: no cover - construction invariant
            raise AssertionError('schema converter is not initialized')
        return self.schema.inline(base, at)

    def _security_scheme(self, definition: SecuritySchemeDefinition, at: str) -> OAS3SecurityScheme:
        node = OAS3SecurityScheme(description=_facet(definition.description))
        scheme_type = definition.type
        if scheme_type == TYPE_BASIC:
            node.type, node.scheme = 'http', 'basic'
        elif scheme_type == TYPE_DIGEST:
            node.type, node.scheme = 'http', 'digest'
        elif scheme_type == TYPE_OAUTH2:
            node.type, node.flows = 'oauth2', self._oauth2_flows(definition.settings, at)
        elif scheme_type == TYPE_OAUTH1:
            node.type, node.scheme = 'http', 'bearer'
            node.extensions['x-raml-oauth1'] = _oauth1_settings(definition.settings)
            self.dropped.append(f'{at}: OAuth 1.0 has no OpenAPI 3.0 security scheme equivalent')
        else:
            node.type, node.scheme = 'http', 'bearer'
            node.extensions['x-raml-type'] = scheme_type
            if scheme_type == TYPE_PASS_THROUGH:
                self.dropped.append(f'{at}: Pass Through has no OpenAPI 3.0 security scheme equivalent')
            elif scheme_type != TYPE_NULL:
                self.dropped.append(f'{at}: custom security scheme has no OpenAPI 3.0 equivalent')
        _extensions(node.extensions, definition.annotations)
        return node

    def _oauth2_flows(self, settings: SecuritySchemeSettings | None, at: str) -> OAS3OAuthFlows:
        flows = OAS3OAuthFlows()
        if settings is None:
            self.dropped.append(f'{at}: OAuth 2.0 settings are absent')
            return flows
        auth = _setting(settings, 'authorizationUri')
        token = _setting(settings, 'accessTokenUri')
        scopes = dict.fromkeys(settings.scopes, '')
        for grant in settings.lists.get('authorizationGrants', ()):
            if grant == 'implicit':
                flows.implicit = OAS3OAuthFlow(authorization_url=auth, scopes=scopes)
            elif grant == 'password':
                flows.password = OAS3OAuthFlow(token_url=token, scopes=scopes)
            elif grant == 'client_credentials':
                flows.client_credentials = OAS3OAuthFlow(token_url=token, scopes=scopes)
            elif grant == 'authorization_code':
                flows.authorization_code = OAS3OAuthFlow(authorization_url=auth, token_url=token, scopes=scopes)
            else:
                self.dropped.append(f'{at}: OAuth 2.0 grant {grant!r} has no OpenAPI flow equivalent')
        return flows


def _examples(base: BaseShape) -> list[Any]:
    result = []
    if base.example is not None and base.example.data is not None:
        result.append(base.example.data.raw)
    if base.examples is not None:
        result.extend(item.data.raw for item in base.examples.entries().values() if item.data is not None)
    return result


def _value(facet: Any) -> Any:
    return None if facet is None else facet.value


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Fraction):
        return int(value) if value.denominator == 1 else float(value)
    if isinstance(value, int | float):
        return value
    return float(value)


def _facet(value: Any) -> str:
    return '' if value is None else str(value.value)


def _setting(settings: SecuritySchemeSettings, name: str) -> str:
    value = settings.values.get(name)
    return '' if value is None else value.value


def _oauth1_settings(settings: SecuritySchemeSettings | None) -> dict[str, Any]:
    if settings is None:
        return {'type': TYPE_OAUTH1}
    node: dict[str, Any] = {'type': TYPE_OAUTH1}
    node.update((name, value.value) for name, value in settings.values.items())
    if settings.lists.get('signatures'):
        node['signatures'] = settings.lists['signatures']
    return node


def _security_requirement(scheme: SecurityScheme) -> OAS3SecurityRequirement:
    if scheme.is_null or scheme.name == TYPE_NULL:
        return {}
    return {scheme.name: list(scheme.compiled_params or ())}


def _extensions(target: dict[str, Any], annotations: Mapping[str, DomainExtension]) -> None:
    for name, extension in annotations.items():
        target.setdefault(f'x-{name}', extension.value.raw)


def _status_text(code: str) -> str:
    try:
        return HTTPStatus(int(code)).phrase
    except ValueError:
        return 'Response'


def to_openapi(raml: Raml) -> tuple[OAS3Document, list[str]]:
    """Return a typed OpenAPI 3.0.3 document and the RAML information it dropped."""
    conversion = OpenAPIConversion()
    return conversion.convert(raml), conversion.dropped
