# fastapi-raml — render a FastAPI application as RAML 1.0

**Its own distribution**, living in the pyRAML repo but built and gated
separately — the arrangement `viewer/` already uses, which has its own
`package.json` and its own `npm run check`. It depends on `fastapi` and
`pydantic`, which the parser must not, so it cannot be part of `pyraml`; and it
depends on `pyraml`, so it cannot be independent of it either.

```
contrib/fastapi-raml/
  pyproject.toml      name = "fastapi-raml", and pyraml from ../.. in place
  fastapi_raml/       the package, and the only thing in the wheel
  examples/           runnable apps, not shipped
  tests/              the gate, not shipped
```

```bash
cd contrib/fastapi-raml
uv sync                       # resolves pyraml from the working tree
uv run pytest                 # 34 tests
uv run ruff check . && uv run ruff format --check . && uv run mypy fastapi_raml/
```

The root `ruff check .` excludes `contrib/`: a project that states its own rules
should not be linted against another's, and doing so would need these
dependencies installed in the parser's environment.

## Run the example server

```bash
cd contrib/fastapi-raml
uv run --extra examples uvicorn examples.server:app --reload
```

`examples/server.py` is a real API with handlers that answer — not a sketch of
one:

| URL | What it is |
|-----|------------|
| `/books`, `/books/{isbn}` | the API itself, over a real dict |
| `/raml` | the RAML source, as `application/raml+yaml` |
| `/raml.json` | the same document as `pyraml tree` output |
| `/raml-docs` | an HTML stub naming both |

## Use it in your own app

Three lines, after the routes are registered:

```python
from fastapi_raml.serve import add_raml_routes

# /raml, /raml.json, /raml-docs
add_raml_routes(app)

# just the two documents
add_raml_routes(app, docs_url=None)
```

Or render without serving:

```python
from fastapi_raml import render

report = render(app)
print(report.to_raml())
# everything RAML cannot express, named -- never omitted silently
print(report.dropped)
```

## The three parts

| Module | Depends on | Holds |
|--------|-----------|-------|
| `document.py` | nothing but `yaml` | the typed model of a RAML document, and its serialisation |
| `render.py` | `fastapi`, `pydantic` | reading an app and building one |
| `serve.py` | `starlette`, `pyraml` | the routes, the cache, and the parse back |

`document.py` states the RAML spelling of every facet once — the `camelCase`
names, the order keys appear in, and the shorthand that writes `title: string`
rather than `title: {type: string}`. Nothing in `render.py` formats RAML.

## What the renderer reads

The models, from FastAPI's own collection helpers:

```
get_fields_from_routes -> get_flat_models_from_fields -> get_model_name_map -> get_definitions
```

`get_definitions` returns one flat, name-mapped dict of JSON Schema, and that is
what `_declaration` turns into `TypeDecl`s. Calling `model_json_schema()` per
model instead does not work: it returns a bare `$ref` for a self-recursive model
and loses its body, and it has no answer for two models of the same name in
different modules.

Everything else is on the `APIRoute`: `path_format` already uses `{param}` as
RAML does, `get_flat_params` splits path from query from header, `body_field` and
`response_field` carry the payload models, and security comes off the dependency
tree.

## The pipeline

```
app ──render()──> RAML text ──parse_from_string()──> Raml ──build_tree()──> tree JSON
      │                        │                                             │
      └─ /raml                 └─ validate=True, unwrap=True                 └─ /raml.json
```

Three stages, and the middle one is not a formality. **The RAML is parsed back
before anything is served**, with `validate=True`, so a document that will not
parse — or whose examples do not validate — raises rather than reaching a client.
It is also the only way to reach the third stage: `build_tree` projects a parsed
`Raml`, so there is no route from the renderer to the tree that does not go
through RAML text.

`parse_from_string` needs a `base_dir` for relative `!include` to resolve
against. Nothing rendered here writes an include, so `build()` uses a throwaway
directory: a real path, nothing left in it.

## Serving

`add_raml_routes(app)` adds three routes, none of them in the OpenAPI schema:

| URL | Response | Media type |
|-----|----------|------------|
| `/raml` | the RAML source | `application/raml+yaml` |
| `/raml.json` | the tree projection | `application/json` |
| `/raml-docs` | an HTML stub naming both | `text/html` |

**Nothing runs at import time.** The first request to `/raml` or `/raml.json`
triggers `build()`; the result is held and reused. The cache key is the router's
`_get_routes_version()`, a counter that changes whenever a route is added, so a
route registered after `add_raml_routes` is still picked up — the next request
rebuilds.

Measured on `examples/server.py`: **6 ms** to render, parse and project; **1.7 ms**
to serve from the cache. The 6 ms is paid once per route change, not per request.

`root_path` is read per request when the stub builds its links, so mounting the
app under a prefix keeps them correct.

### Wiring the viewer

`viewer/` is a SPA over `pyraml tree` output, and its `load.ts` already accepts
the document as a URL: `?src=`, described there as "a document served alongside
it". So `/raml.json` is exactly what it wants, and nothing is parsed in the
browser.

```bash
cd viewer && npm run build          # produces viewer/dist
```

Serve `viewer/dist` from anywhere and name it:

```python
add_raml_routes(app, viewer_url='/viewer/index.html')
```

`/raml-docs` then links to `/viewer/index.html?src=/raml.json`. Without it the
stub says no viewer is configured rather than pretending there is one.

A URL rather than a `StaticFiles` mount, so this module does not depend on a
built frontend: the parser, the renderer and the viewer are released on separate
clocks, and a mount would couple them.

## Two models, and why the tree is not one of them

The same words cover both directions, so they are worth separating.

| Direction | Model | Because |
|-----------|-------|---------|
| app → RAML | `document.py` | writes `type: Pet` and refers to types by the names it declared |
| RAML → viewer | `views/tree.py` | reads the **effective** document: requires `unwrap=True`, refers to types by address |

The tree is the right input for the viewer and unusable as the renderer's output
model — inheritance is already flattened in it, and an address is not a name a
document can declare. RAML text is what joins the two.

## The gate

`tests/test_differential.py`. For each payload, the rendered RAML and the
pydantic model it came from must return the same verdict — one test per case, so
a disagreement names itself:

```
tests/test_differential.py::test_everything_agrees[tagged: unknown tag] PASSED
tests/test_differential.py::test_book_agrees[pages below minimum]       PASSED
```

`tests/test_serve.py` covers the other half: every route answers, the tree has
the four keys `viewer/src/load.ts` refuses a document without, the routes stay
out of the app's own schema, and a route added *after* `add_raml_routes` still
shows up — which is what the cache key exists for.

A diff against expected output would miss both directions the differential
catches: the
document accepting what the code rejects, and rejecting what the code accepts.
Both sides validate a JSON document, which is why the pydantic call is
`model_validate_json` — `model_validate(strict=True)` refuses a `datetime`
written as a string, which is a fact about Python objects and nothing to do with
the rendering.

Three renderer bugs came from it, none of which a golden file would have shown:
an optional discriminator tag, a base/member optionality clash that RAML refused
outright, and `list[str] | None` collapsing to `any` because a union member has
to be spellable as a type expression.

## What it cannot express

Named in `Report.dropped`, never omitted silently: a schema keyword with no RAML
facet (`exclusiveMinimum`, `contains`, `not`), a tuple, a second `servers` entry,
a cookie parameter, callbacks, and a union member that is an inline declaration.

One asymmetry the gate surfaces rather than fixes: **pydantic's lax mode coerces
where RAML does not** (`'42'` to `42`, `'yes'` to `True`). In JSON mode most of it
goes away, which is why the gate compares in JSON mode; in Python mode the
rendered document is stricter than the code it describes. That is a property of
the models, not of the rendering.
