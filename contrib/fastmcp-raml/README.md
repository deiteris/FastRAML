# fastmcp-raml — serve a RAML-described API as an MCP server

Reads a RAML 1.0 document with fastRAML and builds MCP components from it, so an
existing HTTP API becomes MCP tools without your writing any.

```bash
pip install fastmcp-raml      # or: uv add fastmcp-raml
```

```python
from fastmcp_raml import raml_mcp

mcp = raml_mcp('api.raml')
mcp.run()
```

That short form needs the document to declare a `baseUri` with every parameter
already bound, because it is where requests are sent. If the `baseUri` is
templated or absent, pass a client and `raml_mcp` will use its `base_url`:

```python
import httpx2

mcp = raml_mcp('api.raml', client=httpx2.AsyncClient(base_url='https://api.example/v1'))
```

Pass a client anyway when the API needs credentials: `securedBy` names the
scheme, but nothing here sends one.

`raml_mcp` otherwise takes `FastMCP.from_openapi`'s arguments, with a RAML path
in place of the spec — `route_maps`, `route_map_fn`, `mcp_component_fn`,
`mcp_names`, `tags` and `validate_output` all mean what they mean there. The
provider form takes the same arguments and an already-parsed document:

```python
from fastmcp import FastMCP
from fastmcp_raml import RAMLProvider
from fastraml import ParseOptions, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True, validate=True))
provider = RAMLProvider(raml, client=httpx2.AsyncClient(base_url='https://api.example/v1'))

mcp = FastMCP('Bookstore')
mcp.add_provider(provider)
```

Parse with `unwrap=True`: a schema built from an un-flattened shape is missing
every inherited facet, and `RAMLProvider` refuses one.

## No OpenAPI in the middle

FastMCP's OpenAPI provider does two things with a spec: it builds
`list[HTTPRoute]`, and it constructs a `RequestDirector` from an `openapi-core`
`SchemaPath`. **Only the first affects anything.** `RequestDirector.__init__`
stores the spec and never reads it again; `build()` works entirely off the
route. So this package produces routes directly from the fastRAML model and
converts no document — not even an empty one, because `RAMLProvider` sits on
`Provider` rather than on `OpenAPIProvider` and so meets no constructor that
demands a spec.

RAML's endpoint tree already separates path, query and header parameters, which
is exactly the split `ParameterInfo.location` wants. Schemas come from
`fastraml.views.jsonschema`.

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

## What does not survive the trip to MCP

Each of these is reported once per document in `provider.dropped`, so a caller
can see what was lost and decide whether it mattered:

- **Credentials.** `securedBy` names the scheme; FastMCP sends nothing. Put
  authentication on the `httpx2.AsyncClient` you pass in.
- **Streaming.** `OpenAPITool.run` and `OpenAPIResource.read` both read the whole
  response before returning, so a `text/event-stream` body is buffered to its
  end. Upstream lists response streaming as unbuilt.
- **Non-JSON request bodies.** `RequestDirector` encodes JSON,
  `multipart/form-data` and `application/x-www-form-urlencoded`. Anything else
  declared as a mapping is sent as JSON. Where one body declares several media
  types, this package moves a JSON one to the front, because both the director
  and the flattening read only the first key — and a RAML author listing
  `application/json` and `application/xml` means "either", not "prefer the one
  I wrote first".
- **`protocols:` narrower than the base URI.** The request goes to the client's
  base URL whatever the method said.

A `file` type in a multipart body is carried as a string, which is all an MCP
argument can be.

## Which FastMCP

`fastmcp>=4.0,<5`. Nothing private is imported: every name the package uses is
in its module's `__all__`, including `_extract_mime_type_from_route`, which
carries an underscore but is exported deliberately.

One undocumented fact is depended on, and named rather than hidden:
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

## The example

`examples/bookstore.py` serves `fixtures/sample` as a working MCP server. The
API that document describes does not exist, so `raml-mock` serves the same RAML
document on a local port for the MCP server's lifetime. The book resource starts
from `Book.example` and keeps creates and deletes in memory. Calling a tool
therefore builds a real HTTP request from the RAML, sends it over a socket, and
validates the reply against the schema the document declared.

```bash
uv run python examples/bookstore.py              # HTTP, prints a URL
uv run python examples/bookstore.py --stdio      # for an MCP client config
uv run python examples/bookstore.py --describe   # what the document became, then exit
```

`tests/test_example.py` runs it, so it cannot quietly stop working. It is also
the only place in the suite where a request leaves for something that is not an
`httpx2` mock transport. The HTTP mock and MCP schema both come from the same
RAML document, so the example carries no second route table or response model.

## Running the checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastmcp_raml/
uv run pytest -q
```

The suite runs against `fixtures/sample`, the repo's worked example. It exists
to exercise every construct the model carries, and `viewer/` and
`tests/unit/test_bindings.py` measure themselves against the same file. Every
row of the two tables under *What RAML carries* and *What does not survive* is
something that document caught.
