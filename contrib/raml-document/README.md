# raml-document — a typed model of a RAML 1.0 document

For anything that *emits* RAML. The spelling of every facet is stated here once
— the `camelCase` names, the order keys appear in, the shorthand that writes
`title: string` rather than `title: {type: string}` — so an emitter builds a
typed structure and never formats RAML itself.

```python
from raml_document import Document, Method, Response, Body, TypeDecl

document = Document(title='Library', version='v2')
document.types['Book'] = TypeDecl(
    type='object',
    properties={'isbn': TypeDecl(type='string', pattern=r'^\d{13}$')},
)
document.root.at('/books').methods['get'] = Method(
    responses={200: Response(body=Body({'application/json': TypeDecl(type='Book')}))},
)
print(document.to_raml())
```

## One dependency

`pyyaml`, and nothing else. Not a web framework, and **not `fastraml`**: this is
the authoring side of RAML and a parser is on the other one, so an emitter can
take this without taking either.

`fastapi-raml` and `aiohttp-raml` both build on it. A third integration needs no
more of it than they do — the framework-specific part is reading an application,
not writing RAML.

## What is in it

| Module | Needs | Holds |
|--------|-------|-------|
| `raml_document.model` | `pyyaml` | the document model, and the RAML spelling of each node; `METHODS` |
| `raml_document.report` | — | `Report`: a rendered document, and everything left out of it |
| `raml_document.annotations` | — | the annotations for what RAML has no node for: `deprecated`, `tags`, `operationId` |
| `raml_document.from_pydantic` | the `pydantic` extra | `Walk`: pydantic models, dataclasses and `TypedDict`s as RAML types |
| `raml_document.serve` | the `serve` extra (`fastraml`) | `build`: a report parsed back, explained where it fails, and projected |

`from_pydantic` is a package of its own, split by what each part decides:
`tables` (which RAML built-in each Python type is), `values` (Python values as
JSON), `introspect` (what a class or annotation says, and `Shape`, how a
response writes a model), `facets` (constraints as facets), `unions`, and
`walk`, the traversal. Only `walk` and `unions` hold state.

`Walk`'s surface is small: `model`, `annotation`, `field` and `parameter` read
something; `subset` declares a model with fields left out; `output(Shape(...))`
walks what models *write*; `annotate` applies an annotation; `types`,
`annotation_types` and `dropped` are the result. A renderer copies the first
two onto its `Document` and returns the third in its `Report`.

A subclass that redeclares an inherited field differently is declared whole,
without supertypes: RAML reads a redeclared property as a narrowing, and
which retypings it accepts is the parser's rule, not this package's.

## Not the tree view

`fastraml.views.tree` describes the **effective** document: it requires
`ParseOptions(unwrap=True)`, so inheritance is already flattened, and it refers
to types by address rather than by name. Right for reading a parsed document,
unusable for writing a declared one — an address is not a name a document can
declare, and an emitter wants to write `type: Pet` and let a parser flatten it.

## Scope

What emitters emit. RAML has more nodes than these; a node nobody writes is a
node nothing keeps honest, so the model grows when an emitter needs it to.

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy raml_document/
```

`fastraml` is a **dev** dependency: every construct in the tests is built,
rendered, and parsed back with `validate=True`. A facet spelled wrongly fails
here rather than in whichever emitter first used it.

The fastRAML JSON Schema exporter also writes RAML declarations. The conformance
tests compare its DataType and Library output with `TypeDecl.render()` for the
fields both support, without making the parser import this authoring package.
