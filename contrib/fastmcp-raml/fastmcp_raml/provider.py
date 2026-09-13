"""A FastMCP provider whose components come from RAML.

It sits on `Provider`, not on `OpenAPIProvider`, so no OpenAPI document exists
anywhere in the path -- not even the empty skeleton that a subclass needs to get
past `SchemaPath.from_dict`. What is reused is what is genuinely format-neutral
and public: the three component classes, `RequestDirector`, and the output
schema extractor. What is owned is the loop between them, which is small.

**`RequestDirector` is given no spec, because it reads none.** Its `__init__`
stores the argument and `build()` never touches it -- every field it uses comes
off the route. A subclass has to manufacture a document to satisfy the type; a
provider does not, and `tests/test_provider.py` builds a real request through a
spec-less director so that the day upstream starts reading it, a test fails
rather than a server.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import httpx2
from fastmcp.resources import Resource, ResourceTemplate, TextResource
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    _extract_mime_type_from_route,
)
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap
from fastmcp.utilities.openapi import extract_output_schema_from_responses
from fastmcp.utilities.openapi.director import RequestDirector
from pyraml import ParseOptions, parse_from_path

from fastmcp_raml.routes import (
    base_url_of,
    description_of,
    documents_of,
    slug,
    title_of,
    to_http_routes,
    unresolved_in,
)

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Iterable, Mapping, Sequence

    from fastmcp import FastMCP
    from fastmcp.server.providers.openapi.routing import ComponentFn, RouteMapFn
    from fastmcp.tools.base import Tool
    from fastmcp.utilities.openapi import HTTPRoute
    from jsonschema_path import SchemaPath
    from pyraml.registry import Raml

    from fastmcp_raml.routes import Document

__all__ = ['DEFAULT_TIMEOUT', 'DOCUMENT_SCHEME', 'RAMLProvider', 'raml_mcp']

#: The URI scheme documentation resources are published under. Distinct from
#: `resource://`, so a `documentation:` entry can never collide with a resource
#: built from an endpoint.
DOCUMENT_SCHEME = 'docs'

#: Upstream's default for the client it builds when the caller supplies none.
DEFAULT_TIMEOUT = 30.0

#: `RequestDirector.build()` reads nothing off the spec it was constructed with.
#: Naming the hole is better than filling it with a document that lies.
NO_SPEC = cast('SchemaPath', None)

#: Everything becomes a tool unless a `RouteMap` says otherwise, as upstream.
DEFAULT_ROUTE_MAPS = (RouteMap(mcp_type=MCPType.TOOL),)

_MAX_NAME = 56


def _matches(route: HTTPRoute, route_map: RouteMap) -> bool:
    if route_map.methods != '*' and route.method not in route_map.methods:
        return False
    if not re.search(route_map.pattern, route.path):
        return False
    return route_map.tags <= set(route.tags or [])


def _route_type(route: HTTPRoute, route_maps: Iterable[RouteMap]) -> RouteMap:
    """The first map that matches, and a tool if none does."""
    for route_map in route_maps:
        if _matches(route, route_map):
            return route_map
    return RouteMap(mcp_type=MCPType.TOOL)


class RAMLProvider(Provider):
    """MCP components built from a parsed RAML document.

    The keyword arguments are `OpenAPIProvider`'s, and mean what they mean
    there, plus the two RAML has that OpenAPI does not: values for the base
    URI's parameters, and whether `documentation:` becomes resources.
    """

    def __init__(  # noqa: PLR0913 - `OpenAPIProvider.__init__`'s keywords, deliberately unchanged
        self,
        raml: Raml,
        client: httpx2.AsyncClient | None = None,
        *,
        base_uri_parameters: Mapping[str, str] | None = None,
        include_documentation: bool = True,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: RouteMapFn | None = None,
        mcp_component_fn: ComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        validate_output: bool = True,
    ) -> None:
        super().__init__()
        base_url = self._base_url(raml, base_uri_parameters, client)

        self._owns_client = client is None
        self._client = client or httpx2.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT)
        self._director = RequestDirector(NO_SPEC)
        self._validate_output = validate_output
        self._component_fn = mcp_component_fn
        self._names = mcp_names or {}
        self._used: Counter[str] = Counter()

        self._tools: dict[str, Tool] = {}
        self._resources: dict[str, Resource] = {}
        self._templates: dict[str, ResourceTemplate] = {}

        built = to_http_routes(raml)
        #: Everything the RAML said that an `HTTPRoute` could not carry.
        self.dropped: list[str] = built.dropped
        self._add_routes(built.routes, route_maps, route_map_fn, tags or set())
        if include_documentation:
            self._add_documents(documents_of(raml), tags or set())

    @staticmethod
    def _base_url(
        raml: Raml,
        base_uri_parameters: Mapping[str, str] | None,
        client: httpx2.AsyncClient | None,
    ) -> str:
        base_url = base_url_of(raml, base_uri_parameters)
        if base_url is None:
            if client is None:
                msg = 'the document declares no baseUri, so a client must be supplied'
                raise ValueError(msg)
            return ''
        missing = unresolved_in(base_url)
        if missing:
            msg = (
                f'the baseUri leaves {", ".join(missing)} unbound: supply base_uri_parameters '
                f'for them, or pass a client whose base_url is already resolved'
            )
            raise ValueError(msg)
        return base_url

    # -------------------------------------------------------------------------
    # Components
    # -------------------------------------------------------------------------

    def _add_routes(
        self,
        routes: Iterable[HTTPRoute],
        route_maps: list[RouteMap] | None,
        route_map_fn: RouteMapFn | None,
        tags: set[str],
    ) -> None:
        ordered = [*(route_maps or []), *DEFAULT_ROUTE_MAPS]
        for route in routes:
            route_map = _route_type(route, ordered)
            kind = route_map.mcp_type
            if route_map_fn is not None:
                kind = route_map_fn(route, kind) or kind
            if kind is MCPType.EXCLUDE:
                continue
            name = self._name_for(route)
            together = set(route.tags or []) | route_map.mcp_tags | tags
            build = {
                MCPType.TOOL: self._add_tool,
                MCPType.RESOURCE: self._add_resource,
                MCPType.RESOURCE_TEMPLATE: self._add_template,
            }[kind]
            build(route, name, together)

    def _name_for(self, route: HTTPRoute) -> str:
        """A component name, unique within this provider.

        `mcp_names` is keyed on the operation id, which is what a caller writing
        one has in front of them.
        """
        given = route.operation_id or route.summary or f'{route.method}_{route.path}'
        name = slug(self._names.get(route.operation_id or '', given.split('__')[0]))[:_MAX_NAME]
        self._used[name] += 1
        return name if self._used[name] == 1 else f'{name}_{self._used[name]}'

    def _customise(self, route: HTTPRoute, component: Any) -> None:
        if self._component_fn is not None:
            self._component_fn(route, component)

    def _output_schema(self, route: HTTPRoute) -> dict[str, Any] | None:
        schema = extract_output_schema_from_responses(route.responses, route.response_schemas, route.openapi_version)
        if self._validate_output or schema is None:
            return schema
        # Anything goes, but a non-object response still has to be wrapped.
        permissive: dict[str, Any] = {'type': 'object', 'additionalProperties': True}
        if schema.get('x-fastmcp-wrap-result'):
            permissive['x-fastmcp-wrap-result'] = True
        return permissive

    def _describe(self, route: HTTPRoute, fallback: str) -> str:
        return route.description or route.summary or fallback

    def _add_tool(self, route: HTTPRoute, name: str, tags: set[str]) -> None:
        tool = OpenAPITool(
            client=self._client,
            route=route,
            director=self._director,
            name=name,
            description=self._describe(route, f'Executes {route.method} {route.path}'),
            parameters=route.flat_param_schema,
            output_schema=self._output_schema(route),
            tags=tags,
        )
        self._customise(route, tool)
        self._tools[tool.name] = tool

    def _add_resource(self, route: HTTPRoute, name: str, tags: set[str]) -> None:
        resource = OpenAPIResource(
            client=self._client,
            route=route,
            director=self._director,
            uri=f'resource://{name}',
            name=name,
            description=self._describe(route, f'Represents {route.path}'),
            mime_type=_extract_mime_type_from_route(route),
            tags=tags,
        )
        self._customise(route, resource)
        self._resources[str(resource.uri)] = resource

    def _add_template(self, route: HTTPRoute, name: str, tags: set[str]) -> None:
        path_parameters = [p for p in route.parameters if p.location == 'path']
        uri = f'resource://{name}'
        if path_parameters:
            uri += '/' + '/'.join(f'{{{p.name}}}' for p in sorted(path_parameters, key=lambda p: p.name))
        template = OpenAPIResourceTemplate(
            client=self._client,
            route=route,
            director=self._director,
            uri_template=uri,
            name=name,
            description=self._describe(route, f'Template for {route.path}'),
            parameters={
                'type': 'object',
                'properties': {p.name: _with_description(p.schema_, p.description) for p in path_parameters},
                'required': [p.name for p in path_parameters if p.required],
            },
            tags=tags,
            mime_type=_extract_mime_type_from_route(route),
        )
        self._customise(route, template)
        self._templates[template.uri_template] = template

    def _add_documents(self, documents: Sequence[Document], tags: set[str]) -> None:
        """Publish `documentation:` as resources.

        RAML carries titled prose about the API as a whole, which is what a
        caller needs and no schema states. It is static text, so it is served
        from the document rather than fetched from the API.
        """
        for index, document in enumerate(documents):
            # Lowered, unlike a component name: this one is a URI, and the title
            # it comes from is a sentence rather than an identifier.
            name = slug(document.title).lower() or f'document_{index + 1}'
            uri = f'{DOCUMENT_SCHEME}://{name}'
            self._resources[uri] = TextResource(
                uri=uri,  # type: ignore[arg-type]  # pydantic coerces to AnyUrl
                name=name,
                description=document.title,
                text=document.content,
                mime_type='text/markdown',
                tags={'documentation', *tags},
            )

    # -------------------------------------------------------------------------
    # Provider interface. The base class's `_get_*` defaults filter these by URI
    # and version, which is exactly what is wanted.
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        return list(self._tools.values())

    async def _list_resources(self) -> Sequence[Resource]:
        return list(self._resources.values())

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        return list(self._templates.values())

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Close the client, if this built it."""
        if self._owns_client:
            async with self._client:
                yield
        else:
            yield


def _with_description(schema: dict[str, Any], description: str | None) -> dict[str, Any]:
    described = dict(schema)
    if description and 'description' not in described:
        described['description'] = description
    return described


def raml_mcp(  # noqa: PLR0913 - `FastMCP.from_openapi`'s keywords, deliberately unchanged
    source: str | os.PathLike[str],
    *,
    client: httpx2.AsyncClient | None = None,
    name: str | None = None,
    options: ParseOptions | None = None,
    base_uri_parameters: Mapping[str, str] | None = None,
    include_documentation: bool = True,
    route_maps: list[RouteMap] | None = None,
    route_map_fn: RouteMapFn | None = None,
    mcp_component_fn: ComponentFn | None = None,
    mcp_names: dict[str, str] | None = None,
    tags: set[str] | None = None,
    validate_output: bool = True,
    **settings: Any,
) -> FastMCP:
    """A FastMCP server for the API `source` describes.

    `FastMCP.from_openapi`'s signature, with `source` in place of the spec. Pass
    *options* to reach the rest of `ParseOptions` -- a `workspace_root` wider
    than the document's own directory, an `http_client` for remote includes --
    but `unwrap` and `validate` are set regardless: a schema built from an
    un-flattened shape omits every inherited facet, and a document whose own
    examples do not validate should fail here rather than at a tool call.

    The API's `description:` becomes the server's instructions, which is the
    same thing said to the same reader.
    """
    from fastmcp import FastMCP  # noqa: PLC0415 - keeps the import cost off `routes`

    raml = parse_from_path(source, dataclasses.replace(options or ParseOptions(), unwrap=True, validate=True))
    provider = RAMLProvider(
        raml,
        client=client,
        base_uri_parameters=base_uri_parameters,
        include_documentation=include_documentation,
        route_maps=route_maps,
        route_map_fn=route_map_fn,
        mcp_component_fn=mcp_component_fn,
        mcp_names=mcp_names,
        tags=tags,
        validate_output=validate_output,
    )
    settings.setdefault('instructions', description_of(raml))
    return FastMCP(name=name or title_of(raml) or 'RAML', providers=[provider], **settings)
