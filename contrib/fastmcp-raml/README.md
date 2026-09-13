# fastmcp-raml — serve a RAML-described API as an MCP server

Reads a RAML 1.0 document with pyRAML and hands FastMCP the routes it builds
components from, so an existing HTTP API becomes MCP tools with no code.

```python
from fastmcp_raml import raml_mcp

mcp = raml_mcp('api.raml')
mcp.run()
```

`raml_mcp` is `FastMCP.from_openapi`'s signature with a RAML path in place of
the spec — `route_maps`, `route_map_fn`, `mcp_component_fn`, `mcp_names`, `tags`
and `validate_output` all mean what they mean there. The provider form works the
same way:

```python
from fastmcp import FastMCP
from fastmcp_raml import RAMLProvider
from pyraml import ParseOptions, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True, validate=True))
provider = RAMLProvider(raml, client=client)

mcp = FastMCP('Bookstore')
mcp.add_provider(provider)
```

## No OpenAPI in the middle

FastMCP's OpenAPI provider does two things with a spec: builds `list[HTTPRoute]`,
and constructs a `RequestDirector` from an `openapi-core` `SchemaPath`. **Only
the first is load-bearing** — `RequestDirector.__init__` stores the spec and
never reads it again; `build()` works entirely off the route. So this produces
routes directly from the pyRAML model and converts no documents. Not even an
empty one: `RAMLProvider` sits on `Provider`, so there is no constructor
demanding a spec to get past.

RAML's endpoint tree already separates path, query and header parameters, which
is exactly the split `ParameterInfo.location` wants. Schemas come from
`pyraml.views.jsonschema`.

What is reused is what is format-neutral and public — `OpenAPITool`,
`OpenAPIResource`, `OpenAPIResourceTemplate`, `RequestDirector`,
`extract_output_schema_from_responses`. What is owned is the loop between them:
route-map matching, component naming and collision resolution, and the flat
argument schema with the map that undoes it.

Owning that flattening is the part that could drift, so it is measured rather
than trusted: `tests/test_flatten.py` runs `flatten` and fastmcp's own
`_combine_schemas_and_map_params` over the same routes and requires they agree,
on every route the sample yields plus eight shapes it does not have. The private
import lives in that test, not in the package.

## What RAML carries that OpenAPI does not

| RAML | Becomes |
|------|---------|
| `documentation:` | MCP resources under `docs://`, `text/markdown` |
| `description:` on the API | the server's `instructions` |
| `baseUri` with parameters | `{version}` from the `version:` node, the rest from `baseUriParameters` defaults or `base_uri_parameters=` |
| `queryString:` typed as an object | one query parameter per property |
| the first path segment | a tag, so `RouteMap(tags=...)` has something to select on |

## What it does not carry

Reported per document in `provider.dropped`, rather than dropped quietly:

- **Credentials.** `securedBy` names the scheme; FastMCP sends nothing. Put
  authentication on the `httpx2.AsyncClient` you pass in.
- **Streaming.** `OpenAPITool.run` and `OpenAPIResource.read` both read the whole
  response before returning, so a `text/event-stream` body is buffered to its
  end. Upstream lists response streaming as unbuilt.
- **Non-JSON request bodies.** `RequestDirector` encodes JSON, `multipart/form-data`
  and `application/x-www-form-urlencoded`; anything else declared as a mapping is
  sent as JSON. Where a body declares several media types, a JSON one is moved to
  the front — both the director and `_combine_schemas_and_map_params` read the
  first, and RAML's order is the author's, who had no such rule in mind.
- **`protocols:` narrower than the base URI.** The request goes to the client's
  base URL whatever the method said.

A `file` type in a multipart body is carried as a string, which is all an MCP
argument can be.

## Which FastMCP

`fastmcp>=4.0,<5`. Nothing private is imported: every name the package uses is
in its module's `__all__`, including `_extract_mime_type_from_route`, which
carries an underscore but is exported deliberately.

One undocumented fact is still load-bearing, and is stated rather than hidden:
`RequestDirector` is constructed with `NO_SPEC`, because `build()` reads nothing
off it. `test_the_director_reads_no_spec` builds a real request through a
spec-less director, so if that ever changes a test fails rather than a server.

The bound stays narrow because the component classes and the route-mapping
semantics are upstream's, and a major bump is where those move.

Measured rather than assumed:

| Version | |
|---------|--|
| 2.11.3 | `fastmcp.server.providers.openapi` does not exist. Cannot import at all. |
| 3.0.0 | Every name present, `OpenAPIProvider.__init__` signature unchanged. Not tested green — the suite's mock transport does not pair with that release's `httpx2` — so support is not claimed. |
| 4.0.3 | The suite runs green end to end. |

## Upstream

Drafted in [UPSTREAM.md](UPSTREAM.md), ready to file.

`OpenAPIProvider` accepting `routes: list[HTTPRoute]`, and dropping the
`SchemaPath` that `RequestDirector` stores and never reads, would make any
description format pluggable. This package would then be the RAML front half
and nothing else: no owned component loop, no owned flattening, no `NO_SPEC`.

Two smaller things would help on their own: making
`_combine_schemas_and_map_params` public, since it derives everything from a
finished route and cares nothing for where the route came from; and the same for
`_determine_route_type`, which is pure `RouteMap` matching.

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy fastmcp_raml/
uv run python examples/bookstore.py
```

The suite runs against `fixtures/sample`, the repo's worked example, which
exists to exercise every construct the model carries — `viewer` and
`tests/unit/test_bindings.py` measure themselves against the same file. Each row
of the two tables above is a thing that document caught.
