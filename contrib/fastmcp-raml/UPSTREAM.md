# Proposal for FastMCP

Draft, ready to file at `PrefectHQ/fastmcp`. Measured against **4.0.3**.

Before filing, replace `FASTMCP_RAML_URL` below with this project's public URL.

---

## Let `OpenAPIProvider` take pre-built routes

### What we found

`OpenAPIProvider` does two things with a spec, and only one of them matters.

1. `parse_openapi_to_http_routes(openapi_spec)` → `list[HTTPRoute]`
2. `SchemaPath.from_dict(openapi_spec)` → `RequestDirector(spec)`

**The second is dead.** In `fastmcp/utilities/openapi/director.py` the spec is
assigned once and never read:

```
director.py:34            self._spec = spec          # the only assignment
provider.py:146           self._director = RequestDirector(self._spec)
```

Those are the only two occurrences of `_spec` in `utilities/openapi/` and
`server/providers/openapi/` combined. `RequestDirector.build()` works entirely
off the route — `route.method`, `route.path`, `route.parameters`,
`route.parameter_map`, `route.request_body`. One write, zero reads.

Everything `OpenAPIProvider` does *after* parsing is already format-neutral:
route-map matching, the `route_map_fn` and `mcp_component_fn` callbacks,
component naming and collision resolution, `MCPType.EXCLUDE`, and ownership of
the client it created. So is `RequestDirector`, so are `OpenAPITool`,
`OpenAPIResource` and `OpenAPIResourceTemplate`. **`HTTPRoute` is the real
interface, and it is not an OpenAPI type** — path, method, parameters by
location, body by media type, responses by status code. That is HTTP.

### The ask

```python
def __init__(
    self,
    openapi_spec: dict[str, Any] | None = None,
    client: httpx2.AsyncClient | None = None,
    *,
    routes: list[HTTPRoute] | None = None,   # new; mutually exclusive with the spec
    ...
)
```

and drop the `SchemaPath` construction, since nothing reads what it produces.

Additive and backward compatible: existing callers pass `openapi_spec` and see
no change, except that an invalid-but-parseable spec no longer raises
`ValueError("Invalid OpenAPI specification")` from a validator whose output is
discarded.

### Why it is worth doing

Any description format becomes pluggable without reimplementing the MCP half.
We maintain [`fastmcp-raml`](FASTMCP_RAML_URL), which serves a RAML 1.0
document as an MCP server. RAML's endpoint tree already separates path, query
and header parameters — exactly the split `ParameterInfo.location` wants — so
the integration is genuinely a front half and nothing more. It carries things
OpenAPI has no place for, such as RAML's `documentation:` sections, which become
MCP resources.

Today that integration has to either subclass `OpenAPIProvider` and feed it a
skeleton spec it will never read, or sit on `Provider` and re-own the component
loop. We chose the second, so we now maintain a copy of route-map matching,
component naming, and argument flattening that has to follow yours. None of
those decisions are ours to make, and none are about RAML.

### Two smaller asks, useful on their own

Both take a finished `HTTPRoute` and care nothing about where it came from:

- **`_combine_schemas_and_map_params(route, convert_refs)`** — builds the flat
  tool schema and the parameter map. We reimplemented it and hold ours to yours
  with a differential test rather than importing a private name; the three
  OpenAPI-specific parts (`#/components/schemas/` rewriting, top-level `allOf`
  merging, `discriminator.mapping` flattening) are inert for non-OpenAPI input.
- **`_determine_route_type(route, mappings)`** — pure `RouteMap` matching, 24
  lines, no OpenAPI in it.

Making either public would remove most of the reason to copy anything.

### A naming note

`MCPType`, `RouteMap`, `RouteMapFn` and `ComponentFn` live in
`server/providers/openapi/routing.py` but contain nothing OpenAPI — they decide
which MCP component a route becomes. Likewise `utilities/openapi/models.py` is
an HTTP IR. Re-exporting them from a format-neutral location would make the
boundary visible without moving anything.

### Happy to send the PR

If the shape above is acceptable we will open it: the `routes=` argument, the
`SchemaPath` removal, and tests covering both entry paths.
