# bookstore-server

Bookstore API v2, as a FastAPI server to implement.

A worked example exercising every construct the tree carries — including **Markdown** in a description, which is what the RAML spec says every `description:` is. See [Getting started](#) for the `{tenant}` you will need.

Generated from a RAML 1.0 definition by `raml-codegen`. `bookstore_server/`
is regenerated; `impl.py` is not.

## Implement it

```python
from bookstore_server import Api, create_app

class Implementation(Api):
    ...

app = create_app(Implementation())
```

`impl.py` is that file with every method stubbed. Fill it in and run it:

```bash
uvicorn impl:app --reload
```

`Api` inherits one abstract class per path group (books, shelves, deliveries, publications, search).
`abc` refuses to construct a subclass with a method missing, so an operation the
document describes and the implementation does not is an error at startup.

## This directory is the service

Nothing here needs copying anywhere. Commit it as it stands, and regenerate in
place when the document changes:

```
bookstore-server/
  bookstore_server/    generated — rewritten whole, every time. Do not edit.
  impl.py          yours — the implementation
  pyproject.toml   yours after the first run — add what the implementation needs
  README.md        yours after the first run — this file
```

**Inside the package is generated; outside it is yours.** That is the whole
rule. A second run rewrites `bookstore_server/` around the other three and
says which ones it kept; `--force` overwrites them.

## When the document changes

```bash
fastraml tree api.raml > api.json
raml-codegen python-fastapi api.json -o . --package bookstore-server
```

**No new method appears in `impl.py`.** Nothing writes to that file again —
adding the method is yours. What you do not have to do is go looking for which
one:

| the document | you find out from |
|---|---|
| gained an operation | `TypeError` at startup, naming the method nobody implements |
| changed a signature | `mypy impl.py`, printing both signatures side by side |
| dropped an operation | `mypy impl.py` — a method marked `@override` with nothing to override |

Every stubbed method carries `typing.override`, which is what makes the third
row work: `abc` does not mind extra methods on a subclass, so without it a
dropped operation would leave a method here routing nowhere and checking out
fine.

Copy a new method from its abstract declaration under `bookstore_server/api/`,
which carries the signature and the documentation the document gave it.

## What the routes do before you see a request

Every facet the document states is a pydantic constraint on the route, so a
request that does not match is a **422 before your method is called**. A
`pattern:` is a search, which is what RAML's is. `multipleOf:` is the one
exception: pydantic compares it in binary floating point, so it is documented
and not enforced.

A secured operation depends on the schemes its `securedBy:` names, and your
method receives a `Credential` — the token, the scheme it arrived under, and the
scopes the operation requires. **Verifying it is yours.** The document says an
operation is secured, not what a valid token looks like.

| scheme | header | prefix |
|---|---|---|
| `oauth2` | `Authorization` | `Bearer` |
| `machineToken` | `Authorization` | `Bearer` |
| `basic` | `Authorization` | `Basic` |

A missing credential on a required operation is a 401 with `WWW-Authenticate`.
That is RFC 7235, not something RAML states.

## Answering with an error

The request side is enforced before your method runs. A response comes out of
your own code, so nothing can enforce it the same way — what there is instead is
somewhere to raise *from*, so the status and the words both come from the
document:

```python
from bookstore_server.api.books import POST_BOOKS

raise POST_BOOKS.fail(400)                       # the document's own description
raise POST_BOOKS.fail(400, 'isbn already here')  # your own
raise POST_BOOKS.fail(418)                       # LookupError, where you wrote it
```

A status the operation does not document raises `LookupError` at the raise site.
That is a mistake in this code rather than an answer to a caller, so it is not
an `HTTPException` — making it one would answer the request with a 500 and hide
what was wrong. `HTTPException` still works directly if you want a status the
document does not name; nothing checks that one.

Where a documented response carries **headers**, the method is handed a
`Response` to set them on, and only then:

```python
async def post_books(self, *, body, credential, response: Response) -> Book:
    book = await self.catalogue.add(body)
    response.headers['Location'] = f'/books/{book.isbn}'
    return book
```

Setting it is yours — nothing checks that a required header was set. The
document's headers reach `/openapi.json` either way.

## Models

Flat. `type: [A, B]` has no MRO and a narrowed property has no override, so a
subtype carries its supertype's properties and is a class of its own rather than
a subclass. Where the document states a `discriminatorValue:`, the module holds
it as `DISCRIMINATOR` and the property that carries it is a `Literal`.

An optional property is `| None = None`. Use `model_dump(exclude_unset=True)` to
tell "not sent" from "sent as null".

## Base URI

The document states `https://{tenant}.books.example.com/{version}`, kept in `app.BASE_URI` as written.
Nothing mounts it: what its tokens stand for is the deployment's to decide, and
a placeholder would be a value the document does not give. Pass `root_path=` to
`create_app` if you want one.
