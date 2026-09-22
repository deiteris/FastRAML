# fastapi-raml — render a FastAPI application as RAML 1.0

**Its own distribution**, living in the fastRAML repo but built and gated
separately — the arrangement `viewer/` already uses, which has its own
`package.json` and its own `npm run check`. It depends on `fastapi` and
`pydantic`, which the parser must not, so it cannot be part of `fastraml`; and it
depends on `fastraml`, so it cannot be independent of it either.

```
contrib/fastapi-raml/
  pyproject.toml      name = "fastapi-raml", and fastraml from ../.. in place
  fastapi_raml/       the package, and the only thing in the wheel
  examples/           runnable apps, not shipped
  tests/              the gate, not shipped
```

```bash
cd contrib/fastapi-raml
uv sync                       # resolves fastraml from the working tree
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
| `/raml.json` | the same document as `fastraml tree` output |
| `/raml-viewer` | the `fastraml-viewer` bundle, reading this app's tree |

## Use it in your own app

Three lines, after the routes are registered:

```python
from fastapi_raml.serve import add_raml_routes

# /raml, /raml.json, and /raml-viewer when fastraml-viewer is installed
add_raml_routes(app)

# just the two documents
add_raml_routes(app, mount_viewer=None)
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
| `raml-document` | nothing but `yaml` | the typed model of a RAML document, and its serialisation |
| `render.py` | `fastapi`, `pydantic` | reading an app and building one |
| `serve.py` | `starlette`, `fastraml` | the routes, the cache, and the parse back |

`raml-document` is a separate distribution, not a module here. It states the
RAML spelling of every facet once — the `camelCase` names, the order keys appear
in, and the shorthand that writes `title: string` rather than
`title: {type: string}`. Nothing in `render.py` formats RAML.

`aiohttp-raml` builds on the same model, so a change to it moves both. That is
the point: two integrations that disagree about what a RAML document is would be
two bugs waiting.

## What the renderer reads

The models, through `raml_document.from_pydantic.Walk`, which reads
`model_fields` and the annotations directly.

**Not through JSON Schema.** `model_json_schema()` is a projection built for a
different target and drops things RAML can carry: `Decimal(max_digits=8,
decimal_places=2)` becomes an `anyOf` of a number and a 60-character regex where
RAML wants `multipleOf: 0.01`; `dict[int, str]` loses its key type; a
discriminated union's `mapping` keys are stringified, so an integer tag arrives
as `'1'` and the RAML no longer parses against an `integer` property.

Each model is declared once under `types:` and referred to by name, so a model
used in three places appears once and a self-recursive model keeps its body —
the name is registered before the body is read.

**Python subclassing becomes RAML subtyping.** A subclass names its base and
declares only what it adds:

```yaml
Resource:   {type: object, additionalProperties: false, properties: {id: string}}
Book:       {type: Resource, properties: {title: string}}
Audiobook:  {type: Book, properties: {minutes: integer}}
Tracked:    {type: [Audiobook, Timestamped]}
```

Several bases become RAML's multiple inheritance. Two kinds of base are left
out, because neither is a name a document can declare: `RootModel`, which *is*
its single field, and a parametrised generic such as `Page[Book]`. A model
deriving from either keeps its properties inline.

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
| `/raml-viewer/api.json` | this app's tree, where the bundle looks for it | `application/json` |

**Nothing runs at import time.** The first request to `/raml` or `/raml.json`
triggers `build()`; the result is held and reused. The cache key is the router's
`_get_routes_version()`, a counter that changes whenever a route is added, so a
route registered after `add_raml_routes` is still picked up — the next request
rebuilds.

Measured on `examples/server.py`: **6 ms** to render, parse and project; **1.7 ms**
to serve from the cache. The 6 ms is paid once per route change, not per request.

### The viewer

`fastraml-viewer` ships the built SPA, and `add_raml_routes` mounts it at
`/raml-viewer` when the package is installed:

```bash
pip install fastapi-raml[viewer]
```

The bundle reads `api.json` beside itself, so the mount serves *this app's*
tree at `/raml-viewer/api.json` — registered before the static files, which
shadows the worked bookstore the package ships to demo itself. Without that a
mounted viewer would render someone else's API convincingly and say nothing.

Pass `mount_viewer=None` to leave it off, or `mount_viewer='/ui'` to move it.
Hosting your own copy of the bundle needs no argument here: serve `/raml.json`
as `api.json` next to it.

**This package renders no HTML.** There used to be a stub at `/raml-docs` whose
only job was to compose `?src=/raml.json` for a viewer that read its document
from a query string. That parameter is gone — it let a crafted link render any
document under the app's origin — and the stub went with it.

## Two models, and why the tree is not one of them

The same words cover both directions, so they are worth separating.

| Direction | Model | Because |
|-----------|-------|---------|
| app → RAML | `raml-document` | writes `type: Pet` and refers to types by the names it declared |
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
