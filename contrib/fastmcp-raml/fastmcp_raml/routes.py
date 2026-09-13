"""A parsed RAML document -> the `HTTPRoute` list FastMCP builds components from.

FastMCP's OpenAPI provider does two things with a spec: it builds
`list[HTTPRoute]`, and it constructs a `RequestDirector` from an `openapi-core`
`SchemaPath`. **Only the first is load-bearing.** `RequestDirector.__init__`
stores the spec and never reads it again -- `build()` works entirely off the
route (`route.method`, `route.path`, `route.parameters`, `route.parameter_map`,
`route.request_body`). So a description format other than OpenAPI needs to
produce routes and nothing else.

That is what this does, straight from the pyRAML model. No OpenAPI document is
constructed anywhere: RAML's own endpoint tree already separates path, query and
header parameters, which is the split `ParameterInfo.location` wants, and
`views/jsonschema.py` supplies the schemas.

`_combine_schemas_and_map_params` is reused rather than reimplemented -- it
derives `flat_param_schema` and `parameter_map` from a route that is already
built, so it is indifferent to where the route came from.

What RAML says and MCP has no place for is collected in `Routes.dropped` rather
than discarded: a caller that cannot see what was lost cannot decide whether it
mattered.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol
from urllib.parse import urlsplit

from fastmcp.utilities.openapi import (
    HTTPRoute,
    ParameterInfo,
    ParameterLocation,
    RequestBodyInfo,
    ResponseInfo,
)
from pyraml import ObjectShape
from pyraml.parser.fragments import APIFragment
from pyraml.views.jsonschema import Conversion

from fastmcp_raml.flatten import flatten

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pyraml.parser.endpoints import Body, EndPoint, Operation
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape, ScalarFacet

__all__ = [
    'DEFS',
    'FORM_MEDIA_TYPES',
    'JSON_MEDIA_TYPES',
    'STREAMING_MEDIA_TYPES',
    'Document',
    'Routes',
    'base_url_of',
    'description_of',
    'documents_of',
    'slug',
    'title_of',
    'to_http_routes',
    'unresolved_in',
    'version_of',
]

#: fastmcp reads `#/$defs/`, not draft-07's `definitions`.
DEFS = '$defs'

#: The media types `RequestDirector` encodes as JSON and
#: `extract_output_schema_from_responses` will take an output schema from. Kept
#: in step with `fastmcp.utilities.openapi.schemas`, which spells the same list.
JSON_MEDIA_TYPES = (
    'application/json',
    'application/vnd.api+json',
    'application/hal+json',
    'application/ld+json',
    'text/json',
)

#: The two media types `RequestDirector` builds a form body for.
FORM_MEDIA_TYPES = ('multipart/form-data', 'application/x-www-form-urlencoded')

#: Media types whose point is that the body arrives in pieces. `OpenAPITool.run`
#: and `OpenAPIResource.read` both `await client.send(...)` and then read the
#: whole response, so a stream is buffered to its end before the caller sees any
#: of it. Upstream lists response streaming as unbuilt in both of its READMEs.
STREAMING_MEDIA_TYPES = (
    'text/event-stream',
    'application/x-ndjson',
    'application/stream+json',
    'multipart/x-mixed-replace',
)

_NOT_NAME = re.compile(r'[^a-zA-Z0-9]+')
_URI_PARAMETER = re.compile(r'\{([^{}]+)\}')


class Document(NamedTuple):
    """One `documentation:` entry: a title and the Markdown under it."""

    title: str
    content: str


class Routes(NamedTuple):
    """What a document yields, and what of it MCP has no place for."""

    routes: list[HTTPRoute]
    dropped: list[str]


class _Declared(Protocol):
    """What a URI parameter, a query parameter, a header and a property share.

    RAML declares all four the same way -- a type with a required flag -- and
    the three parameter maps and `ObjectShape.properties` hold values of three
    different classes that agree on exactly this much.
    """

    @property
    def base(self) -> BaseShape: ...

    @property
    def required(self) -> bool: ...


def slug(text: str) -> str:
    """*text* with nothing in it but letters, digits and single underscores.

    Case is left alone, as `fastmcp`'s own `_slugify` leaves it: a component
    name comes from a path or an `operationId`, and lowering those would make
    two documents that differ only in case produce the same name.
    """
    return _NOT_NAME.sub('_', text).strip('_')


def _operation_id(method: str, path: str) -> str:
    """A stable name for the tool this becomes.

    From the path and method rather than `displayName`, because a display name
    is prose: two operations may share one, and MCP component names must not.
    """
    named = slug(path)
    return f'{method.lower()}_{named}' if named else method.lower()


def _tags_for(path: str) -> list[str]:
    """The first path segment, which is the resource the operation belongs to.

    RAML has no tags. `RouteMap` selects on them and every component carries
    them, so the grouping RAML does have -- the top-level resource -- is worth
    more there than an empty list is.
    """
    tag = slug(path.strip('/').split('/', 1)[0])
    return [tag] if tag else []


def _parameter(name: str, declared: _Declared, location: ParameterLocation, conv: Conversion, at: str) -> ParameterInfo:
    base = declared.base
    return ParameterInfo(
        name=name,
        location=location,
        # A URI parameter is required whatever it says: it is part of the path,
        # so a request without it addresses nothing.
        required=True if location == 'path' else declared.required,
        schema=conv.inline(base, f'{at}.{name}'),
        description=_text(base.description),
    )


def _query_string(shape: BaseShape, conv: Conversion, at: str, dropped: list[str]) -> list[ParameterInfo]:
    """`queryString:` names a type for the query string as a whole.

    RAML allows it instead of `queryParameters:`, never as well as it, so
    ignoring it leaves the operation with no arguments at all. An object type's
    properties are the parameters. Anything else -- a union, a scalar -- says
    something about the whole string that no per-parameter schema can say, and
    is reported rather than guessed at.
    """
    inner = shape.shape
    if not isinstance(inner, ObjectShape) or not inner.properties:
        dropped.append(f'{at}: queryString names no object type, so its parameters cannot be named individually')
        return []
    if inner.pattern_properties:
        dropped.append(f'{at}: queryString has pattern properties, which have no parameter name')
    return [_parameter(name, prop, 'query', conv, at) for name, prop in inner.properties.items()]


def _parameters(
    endpoint: EndPoint, operation: Operation, conv: Conversion, at: str, dropped: list[str]
) -> list[ParameterInfo]:
    """RAML's parameter places, which are already the split MCP wants."""
    request = operation.request
    found = [_parameter(name, p, 'path', conv, at) for name, p in endpoint.uri_parameters.items()]
    if request is None:
        return found
    found += [_parameter(name, p, 'query', conv, at) for name, p in request.query_parameters.items()]
    found += [_parameter(name, p, 'header', conv, at) for name, p in request.headers.items()]
    if request.query_string is not None:
        found += _query_string(request.query_string, conv, at, dropped)
    return found


def _ordered(bodies: Mapping[str, Body]) -> list[tuple[str, Body]]:
    """Declaration order, except that a JSON media type moves to the front.

    Both `RequestDirector.build` and `_combine_schemas_and_map_params` read the
    *first* key of `content_schema`, so which media type comes first decides how
    the payload is encoded and what arguments the tool declares. RAML's order is
    the author's, and the author had no such rule in mind: `application/json`
    and `application/xml` under one body mean "either", not "prefer the one
    written first". `sorted` is stable, so declaration order survives within
    each group.
    """
    declared = [(media, body) for media, body in bodies.items() if body.shape is not None]
    return sorted(declared, key=lambda item: _base_media(item[0]) not in JSON_MEDIA_TYPES)


def _base_media(media_type: str) -> str:
    return media_type.split(';', maxsplit=1)[0].strip().lower()


def _content(ordered: Iterable[tuple[str, Body]], conv: Conversion, at: str) -> dict[str, Any]:
    return {media: conv.inline(body.shape, f'{at}.{media}') for media, body in ordered if body.shape is not None}


def _request_body(operation: Operation, conv: Conversion, at: str, dropped: list[str]) -> RequestBodyInfo | None:
    request = operation.request
    if request is None or not request.bodies:
        return None
    ordered = _ordered(request.bodies)
    content = _content(ordered, conv, f'{at}.body')
    if not content:
        return None

    chosen = _base_media(ordered[0][0])
    if chosen not in JSON_MEDIA_TYPES and chosen not in FORM_MEDIA_TYPES:
        # The director has no branch for it: a mapping body is sent as JSON
        # whatever the declaration said.
        dropped.append(f'{at}: body is {chosen}, which is sent as JSON')

    # RAML has no "optional body": a declared body is the payload.
    return RequestBodyInfo(required=True, content_schema=content)


def _responses(operation: Operation, conv: Conversion, at: str, dropped: list[str]) -> dict[str, ResponseInfo]:
    built: dict[str, ResponseInfo] = {}
    for code, response in operation.responses.items():
        ordered = _ordered(response.bodies)
        streamed = [media for media, _body in ordered if _base_media(media) in STREAMING_MEDIA_TYPES]
        if streamed:
            dropped.append(
                f'{at}: {code} is {", ".join(streamed)}, which is read to the end before the caller sees any of it'
            )
        built[code] = ResponseInfo(
            description=_text(response.description),
            content_schema=_content(ordered, conv, f'{at}.{code}'),
        )
    return built


def _route(endpoint: EndPoint, operation: Operation, scheme: str | None, dropped: list[str]) -> HTTPRoute:
    path = endpoint.full_uri
    method = operation.method.upper()
    at = f'{method} {path}'
    # One conversion per route, so a type reached twice lands in `$defs` once.
    conv = Conversion(DEFS)

    if scheme is not None and operation.protocols and scheme.upper() not in {p.upper() for p in operation.protocols}:
        allowed = ', '.join(operation.protocols)
        dropped.append(f'{at}: protocols narrow to {allowed}, but the request goes to the client base URL')

    parameters = _parameters(endpoint, operation, conv, at, dropped)
    body = _request_body(operation, conv, at, dropped)
    responses = _responses(operation, conv, at, dropped)
    dropped.extend(conv.dropped)
    # Read after every conversion, so no argument evaluation order decides it.
    definitions = dict(conv.definitions)

    route = HTTPRoute(
        path=path,
        method=method,  # type: ignore[arg-type]  # a HttpMethod literal
        operation_id=_operation_id(method, path),
        summary=_text(operation.display_name) or _text(endpoint.display_name),
        description=_text(operation.description) or _text(endpoint.description),
        tags=_tags_for(path),
        parameters=parameters,
        request_body=body,
        responses=responses,
        # Two dicts, not one shared object: `_combine_schemas_and_map_params`
        # assigns `request_schemas` straight into `flat_param_schema['$defs']`,
        # so sharing would let a caller reading one reach the other.
        request_schemas=dict(definitions),
        response_schemas=dict(definitions),
        # Left unset on purpose. It selects `convert_openapi_schema_to_json_schema`,
        # which translates the OpenAPI 3.x schema dialect -- `nullable`, an object
        # `discriminator` -- into JSON Schema. These schemas are already JSON
        # Schema draft-07, so that pass has nothing to do and something to break.
        openapi_version=None,
    )

    # Derived from the finished route, so it is indifferent to where the route
    # came from -- and `tests/test_flatten.py` holds it to what fastmcp's own
    # flattening produces for the same input.
    route.flat_param_schema, route.parameter_map = flatten(route)
    return route


def _text(facet: ScalarFacet[str] | None) -> str | None:
    return None if facet is None else facet.value


def to_http_routes(raml: Raml) -> Routes:
    """Every operation in `raml`, as the routes FastMCP builds components from.

    Requires `ParseOptions(unwrap=True)`. Asserted here rather than per shape:
    `Conversion.inline` is the hot path and does not check, so without this a
    declared model would produce schemas missing every inherited facet, and
    produce them silently (invariant I12).
    """
    assert raml.unwrapped, (  # noqa: S101 - docs/02 section 4 invariant, not input validation
        'to_http_routes needs an unwrapped shape: parse with ParseOptions(unwrap=True)'
    )
    dropped: list[str] = []
    routes: list[HTTPRoute] = []
    base = base_url_of(raml)
    scheme = urlsplit(base).scheme if base else None

    schemes: dict[str, None] = {}
    for endpoint in raml.endpoints.values():
        for operation in endpoint.operations.values():
            routes.append(_route(endpoint, operation, scheme, dropped))
            for secured in operation.secured_by or ():
                if not secured.is_null:
                    schemes[secured.name] = None
    if schemes:
        # Once for the document, not once per route: it is one instruction to
        # the caller, and every operation in a secured API would repeat it.
        dropped.append(
            f'securedBy {", ".join(schemes)}: FastMCP sends no credentials, '
            'so authentication belongs on the httpx client'
        )
    return Routes(routes=routes, dropped=dropped)


def _entry(raml: Raml) -> APIFragment | None:
    """The document's root, when it is an API rather than a fragment.

    A library has types but no endpoints, so there is nothing for a base URI or
    a title to be.
    """
    entry = raml.entry_point
    return entry if isinstance(entry, APIFragment) else None


def title_of(raml: Raml) -> str | None:
    """The document's `title`, which names the server built from it."""
    entry = _entry(raml)
    return None if entry is None else _text(entry.title)


def description_of(raml: Raml) -> str | None:
    """The document's `description`, which is prose addressed to a caller."""
    entry = _entry(raml)
    return None if entry is None else _text(entry.description)


def version_of(raml: Raml) -> str | None:
    """The document's `version`, which is also what `{version}` stands for."""
    entry = _entry(raml)
    return None if entry is None else _text(entry.version)


def base_url_of(raml: Raml, parameters: Mapping[str, str] | None = None) -> str | None:
    """The document's `baseUri`, with what is known of its parameters filled in.

    RAML's base URI is a template. `{version}` stands for the `version:` node;
    every other parameter is declared under `baseUriParameters:` and may carry a
    `default:`. *parameters* overrides both. Whatever is still unbound is left
    in place for `unresolved_in` to name -- a base URL with a literal `{tenant}`
    in it is a clearer failure than one with an empty segment where the tenant
    should be.
    """
    entry = _entry(raml)
    if entry is None or entry.base_uri is None:
        return None

    supplied = dict(parameters or {})
    bound: dict[str, str] = {}
    version = _text(entry.version)
    if version is not None:
        bound['version'] = version
    for name, declared in (entry.base_uri_parameters or {}).items():
        default = declared.base.default
        if default is not None:
            bound[name] = str(default)
    bound.update(supplied)

    def substitute(match: re.Match[str]) -> str:
        return bound.get(match.group(1), match.group(0))

    return _URI_PARAMETER.sub(substitute, entry.base_uri.value)


def unresolved_in(url: str) -> list[str]:
    """The parameters still standing in *url*, in the order they appear."""
    return _URI_PARAMETER.findall(url)


def documents_of(raml: Raml) -> list[Document]:
    """The document's `documentation:` entries.

    RAML carries the prose an OpenAPI document has nowhere to put: numbered,
    titled sections of Markdown about the API as a whole. They are what a reader
    needs and no schema states -- which tenant to address, which scopes to ask
    for, how a collection pages.
    """
    entry = _entry(raml)
    if entry is None:
        return []
    # Both are required of a documentation item, so neither fallback is
    # reachable in a document that parsed; the model types them optional
    # because every facet on it is.
    return [
        Document(title=_text(item.title) or '', content=_text(item.content) or '')
        for item in (entry.documentation or ())
    ]
