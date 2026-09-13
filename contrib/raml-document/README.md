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

`pyyaml`, and nothing else. Not a web framework, and **not `pyraml`**: this is
the authoring side of RAML and a parser is on the other one, so an emitter can
take this without taking either.

`fastapi-raml` and `aiohttp-raml` both build on it. A third integration needs no
more of it than they do — the framework-specific part is reading an application,
not writing RAML.

## Not the tree view

`pyraml.views.tree` describes the **effective** document: it requires
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

`pyraml` is a **dev** dependency, for one reason: every construct in the tests is
built, rendered, and parsed back with `validate=True`. A facet spelled wrongly
fails here rather than in whichever emitter first used it.
