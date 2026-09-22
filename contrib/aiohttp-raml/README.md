# aiohttp-raml — code-first RAML 1.0 for aiohttp

Write an aiohttp handler once, using pydantic models and type annotations.
`aiohttp-raml` uses those annotations for two jobs: it validates every incoming
request, and it generates the RAML 1.0 document that describes your API. Because
both come from the same declarations, the document cannot describe something the
code does not do.

- [Install](#install)
- [Quick start](#quick-start)
- [Declare parameters](#declare-parameters)
- [Declare responses](#declare-responses)
- [Accept file uploads](#accept-file-uploads)
- [Add authentication](#add-authentication)
- [Validate your own responses](#validate-your-own-responses)
- [The 400 response](#the-400-response)
- [Serve the document](#serve-the-document)
- [Limitations](#limitations)
- [Develop this package](#develop-this-package)

## Install

```bash
pip install aiohttp-raml
```

Requires Python 3.12 or later, `aiohttp` 3.10 or later, and pydantic 2.7 or
later. To also serve the browsable API viewer, install the extra:

```bash
pip install 'aiohttp-raml[viewer]'
```

## Quick start

This is a complete, runnable application.

```python
from typing import Annotated

from aiohttp import web
from pydantic import BaseModel, Field

from aiohttp_raml import RamlView, Responds, add_raml_routes


class Book(BaseModel):
    isbn: Annotated[str, Field(pattern=r'^\d{13}$')]
    title: str


class Error(BaseModel):
    code: int
    message: str


BOOKS = {'9780306406157': Book(isbn='9780306406157', title='The Hobbit')}


class BookView(RamlView):
    async def get(
        self, isbn: str, /
    ) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
    ]:
        """One book."""
        book = BOOKS.get(isbn)
        if book is None:
            return web.json_response(Error(code=404, message='no such book').model_dump(), status=404)
        return web.json_response(book.model_dump())


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_view('/books/{isbn}', BookView)
    # Call this last, and before the app starts.
    return add_raml_routes(app, title='Library', version='v2')


if __name__ == '__main__':
    web.run_app(build_app())
```

Start it, then fetch the generated document from `/raml`:

```yaml
/books:
  /{isbn}:
    uriParameters:
      isbn: string
    get:
      description: One book.
      responses:
        '200':
          description: the book
          body:
            application/json: Book
        '400':
          description: the request did not validate
          body:
            application/json: RequestError[]
        '404':
          description: no such book
          body:
            application/json: Error
```

You declared the 200 and the 404. `aiohttp-raml` added the 400, because the
handler takes a parameter and so can reject a request — see
[The 400 response](#the-400-response).

Subclass `RamlView` and `aiohttp-raml` wraps every HTTP method the class
defines. To describe a plain function handler instead, decorate it with
`@validate`, or with `@validate.and_request` if the handler needs the request
object as its first argument.

### Describe a method with its docstring

A one-line docstring becomes the method's `description`:

```python
async def get(self, isbn: str, /) -> ...:
    """Fetch one book by its ISBN."""
```

Add a body and the summary line becomes `displayName` instead, which is the
short label RAML shows in place of the method name:

```python
async def post(self, isbn: str, /, cover: UploadedFile) -> ...:
    """Attach a cover image.

    The upload is streamed, so a large file never lands in memory.
    """
```

```yaml
post:
  displayName: Attach a cover image.
  description: The upload is streamed, so a large file never lands in memory.
```

A one-liner stays a description because most one-liners are prose, not a name
the author chose for the method. Only a docstring that separates a summary from
a body gets a `displayName`.

### Add reference documentation

Pass `documentation` to write RAML's root `documentation:` node — free-form
markdown pages that sit beside the API rather than on any one method:

```python
from aiohttp_raml import Documentation

add_raml_routes(
    app,
    title='Library',
    documentation=[
        Documentation(title='Rate limits', content='Ten requests per second.'),
    ],
)
```

### How models become RAML types

Every pydantic model reached from a handler is declared once under `types:` and
referred to by name, so a model used in three places appears once. Recursive
models work, because the name is registered before the body is read.

**Python subclassing becomes RAML subtyping.** A subclass names its base and
declares only what it adds:

```python
class Vehicle(BaseModel):
    wheels: int


class Car(Vehicle):
    doors: int
```

```yaml
Vehicle:
  type: object
  properties:
    wheels: integer
Car:
  type: Vehicle
  properties:
    doors: integer
```

Several bases become RAML's multiple inheritance, `type: [Left, Right]`. Two
kinds of base are left out, because neither is a name a document can declare:
`RootModel`, which *is* its single field, and a parametrised generic such as
`Page[Book]`. A model deriving from either keeps its properties inline.

## Declare parameters

By default, the position of a parameter in the signature decides which part of
the request it comes from:

| Signature position | Comes from | RAML node |
|---|---|---|
| positional-only (before `/`) | the URL path | `uriParameters` |
| a `BaseModel` | the request body | `body` |
| positional-or-keyword | the query string | `queryParameters` |
| keyword-only (after `*`) | a request header | `headers` |

Add a marker to override the position. The four markers are `UriParam`,
`QueryParam`, `Header` and `Body`, named after the RAML nodes they fill.

```python
async def get(
    self,
    isbn: Annotated[str, Field(pattern=r'^\d{13}$')],  # path parameter, constrained
    /,
    title: str | None = None,  # query parameter, optional
    *,
    request_id: Annotated[str | None, Header('X-Request-Id')] = None,
) -> Annotated[web.Response, Responds(200, Book)]: ...
```

A marker says *where* a value comes from. A pydantic `Field` says what the value
must look like. Use both together when you need both.

### Name a header explicitly

A Python parameter cannot be named `X-Request-Id`, so `aiohttp-raml` converts
the parameter name to a header name: `x_request_id` becomes `X-Request-Id`.
**When that conversion produces the wrong name, pass the correct one to
`Header`**, as in the example above. Incoming headers are matched
case-insensitively either way.

### Repeated query parameters

If a parameter's annotation accepts a sequence, repeated query keys collect into
a list. With `tags: list[str] | None = None`, the request `?tags=a&tags=b`
arrives as `['a', 'b']`, and `?tags=a` arrives as `['a']`.

Query parameters and headers that you have not declared are ignored.

## Declare responses

Responses go on the return annotation as `Annotated` metadata. The return type
stays `web.Response`, because that is what the handler returns, so type checkers
see the truth:

```python
class BookView(RamlView):
    async def get(
        self, isbn: str, /
    ) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
        Responds(204, None, 'deleted'),  # no body
        Responds(503, Error, 'maintenance', media='application/problem+json'),
    ]: ...
```

`Responds` takes a status code, a body type, an optional description, and an
optional media type that defaults to `application/json`. The body type is a
normal annotation, so `list[Book]` and `Book | None` work as they do anywhere
else. Pass `None` for a response with no body.

A handler that declares no response gets no `responses:` node. `aiohttp-raml`
does not invent a 200 you did not ask for.

## Accept file uploads

Declare a parameter as `UploadedFile` and the handler accepts a
`multipart/form-data` body. **Uploads are streamed, never buffered.** The
`UploadedFile` arrives having read nothing, and bytes come off the wire only
when you ask for them:

```python
from aiohttp_raml import File, UploadedFile


async def post(
    self,
    isbn: str,
    /,
    cover: Annotated[UploadedFile, File(file_types=['image/png'], max_size=1_000_000)],
) -> Annotated[web.Response, Responds(201, Book, 'cover stored')]:
    while chunk := await cover.read_chunk():
        ...  # never more than one chunk is held in memory
    return web.json_response(BOOKS[isbn].model_dump(), status=201)
```

```yaml
body:
  multipart/form-data:
    type: object
    properties:
      cover:
        type: file
        maxLength: 1000000
        fileTypes: [image/png]
```

Use `await cover.read()` instead if you want the whole part at once.

### File constraints

`File` takes `file_types`, `max_size` and `min_size`. These become RAML's
`fileTypes`, `maxLength` and `minLength`, and `aiohttp-raml` enforces them as
well as documenting them. A request that breaks one gets a 400. `file_types` is
checked against the part's header before any bytes are read; the two sizes are
checked as a running count while the part streams.

### Inspect a part before reading it

`filename` and `content_type` come from the part's headers, so they are `None`
until the reader reaches that part. Call `await cover.open()` to advance to the
part and check `file_types` without reading a byte. `cover.opened` tells you
whether that has happened. Both `read()` and `read_chunk()` call `open()` for
you.

### Other fields in the form

A parameter beside an upload becomes a form field if it is a `BaseModel`, or if
you mark it with `Body()`. A plain scalar with no marker is still a query
parameter.

### Order your parameters

**A client must send the parts in the order your signature declares them, and
you must declare every non-file field before the files.** A streaming reader
cannot reach the second part without passing the first, and buffering a part to
allow that is what streaming avoids. If you declare a field after a file,
`aiohttp-raml` raises a `TypeError` when the handler is decorated, not at
request time.

RAML has no way to express a required part order, so this requirement is
documented here and not in the generated document.

## Add authentication

Subclass one of RAML's six security scheme types, register it by name, then
apply it to a handler with `@secured`:

```python
from aiohttp_raml import AuthenticationError, OAuth2, RamlView, secured
from aiohttp_raml.security import setup as setup_security


class Books(OAuth2):
    """Books, and who may read or write them."""

    async def authenticate(self, request: web.Request) -> str:
        token = request.headers.get('Authorization', '')
        if not token:
            raise AuthenticationError('Authorization header missing')
        return token

    async def permits(self, request, identity, scopes) -> bool:
        return 'read:books' in scopes


setup_security(
    app,
    {
        'books': Books(
            access_token_uri='https://auth.example/token',
            authorization_uri='https://auth.example/authorize',
            grants=['authorization_code'],
            scopes=['read:books', 'write:books'],
        )
    },
)


class BookView(RamlView):
    @secured('books', scopes=['read:books'])
    async def get(self, isbn: str, /) -> Annotated[web.Response, Responds(200, Book)]: ...
```

The six types are `BasicAuth`, `DigestAuth`, `PassThrough`, `OAuth1`, `OAuth2`
and `CustomScheme`. Each declares itself directly as the matching RAML scheme,
so nothing is converted from another format on the way out.

Implement `authenticate` to return the caller's identity or raise
`AuthenticationError`. Implement `permits` to decide whether that identity may
act under the requested scopes; the default accepts any authenticated caller.
A failed `authenticate` produces a 401 and a failed `permits` produces a 403.
`request['identity']` holds whatever `authenticate` returned.

`@secured` stacks, and **each application is an alternative**, because RAML's
`securedBy:` is a list of alternatives. The first scheme that authenticates
handles the request. There is no decorator for requiring two schemes together,
because RAML cannot express that.

## Validate your own responses

**No type checker can verify what your handler returns.** `web.json_response()`
accepts `Any`, so the payload's type is gone by the time the response is built.
That is true of every way of declaring a response, including phantom `Protocol`
types like `r200[Book]`, which type-check against anything at all.

`aiohttp-raml` can check it at runtime instead. Set `check_responses` on the
view:

```python
class BooksView(RamlView):
    check_responses = True
```

A mismatch raises `ResponseMismatch`, which aiohttp turns into a 500 and logs:

```
BooksView.get returned a 200 body it does not declare: 1 validation error for Book
BooksView.post returned 418, which it does not declare
```

This costs one JSON parse and one validation per response, so it is off by
default. Turn it on in development and in your test suite.

The check ignores the 400 that `aiohttp-raml` returns for an invalid request,
because your handler never ran and so never produced it.

## The 400 response

Any operation that takes a parameter can reject a request, so `aiohttp-raml`
adds that response to the document for you:

```yaml
responses:
  '400':
    description: the request did not validate
    body:
      application/json: RequestError[]
```

The body is a list of `RequestError`, which has four fields:

| Field | Meaning |
|---|---|
| `in` | which part of the request failed: `uriParameters`, `queryParameters`, `headers` or `body` |
| `loc` | the path to the value inside that part |
| `type` | the error code, such as `missing` or `string_pattern_mismatch` |
| `msg` | the message |

pydantic also reports `input` and `ctx`. Neither is included: `input` would echo
request data back to the caller, and `ctx` varies by constraint, so no single
type describes it. The detail they carry is already in `msg`.

An operation that takes no parameter cannot fail validation, so it gets no 400.
If you declare your own `Responds(400, ...)`, yours is used unchanged.

## Serve the document

`add_raml_routes(app)` adds three routes:

| URL | Response | Media type |
|-----|----------|------------|
| `/raml` | the RAML source | `application/raml+yaml` |
| `/raml.json` | the document as JSON, for the viewer | `application/json` |
| `/raml-viewer/api.json` | the same JSON, where the viewer looks for it | `application/json` |

Pass your API's metadata as keyword arguments — `title`, `version`,
`description`, `base_uri` and `documentation`. An aiohttp application carries no
metadata of its own, and RAML requires a title.

**Call `add_raml_routes` after you register your routes, and before the app
starts.** aiohttp freezes the router at startup and rejects any route added
after that. For the same reason, the document is built once, on the first
request, and then reused; no route can appear later to invalidate it.

Before serving anything, `aiohttp-raml` parses the document it generated and
validates it. If the result would not parse, or an example in it does not
validate, the request fails loudly instead of sending a broken document to a
client.

### Keep a route out of the document

Use `@exclude`. It works on a handler, a view or a router resource, so you can
also exclude a static mount, which has no handler to decorate:

```python
from aiohttp_raml import exclude


@exclude
async def health(request):
    return web.Response()
```

The routes added by `add_raml_routes` are excluded already, so `/raml` does not
describe itself.

### The browsable viewer

Install the `viewer` extra and `add_raml_routes` mounts a browsable UI at
`/raml-viewer`. Without the extra, nothing is mounted and nothing fails — the
two document routes do not need it.

```python
add_raml_routes(app, title='Library', mount_viewer='/ui')  # move it
add_raml_routes(app, title='Library', mount_viewer=None)  # turn it off
```

The viewer package ships a sample document named `api.json`. `add_raml_routes`
registers your app's document at that path first, and aiohttp matches routes in
registration order, so the viewer shows your API rather than the sample.

## Limitations

**`Report.dropped` lists everything the renderer could not express**, so nothing
is omitted silently. Call `render(app, title=...)` directly to read it:

```python
from aiohttp_raml import render

report = render(app, title='Library')
print(report.to_raml())
print(report.dropped)
```

It reports scopes given to a non-OAuth scheme, a `@secured` naming a scheme that
was never registered, a URI parameter that matches no segment of its path, a
static mount, a prefixed sub-application, and a handler with no `@validate`,
which is still described by its path and method.

### Custom metadata is not supported

RAML has two extension mechanisms, and `aiohttp-raml` reaches neither:

- **Annotations** — `annotationTypes:` declarations and `(name): value`
  applications on almost any node.
- **User-defined facets** — a `facets:` block declaring extra facets that
  subtypes of a type must supply.

Both are RAML 1.0 features that the fastRAML parser reads in full. The gap is in
the authoring model this package writes through, which has no node for either.

You can still add them by post-processing the document yourself, at the cost of
giving up `add_raml_routes`:

```python
import yaml
from aiohttp_raml import render

raw = render(app, title='Library').document.render()  # a plain dict
raw['annotationTypes'] = {'owner': 'string'}
raw['/books']['get']['(owner)'] = 'platform-team'
source = '#%RAML 1.0\n' + yaml.safe_dump(raw, sort_keys=False)
```

That document parses, and `fastraml` reads the annotation back.

Not supported:

- **`traits` and `resourceTypes`.** Out of scope; nothing is generated for them.
- **`protocols:`, `mediaType:` and `uses:`.** Authoring conveniences with no
  natural source in a running application.
- **Required part order for uploads.** RAML has no node for it, so the
  requirement is documented here only.
- **A function registered for every method.** `app.router.add_route('*', ...)`
  with a plain function names no HTTP methods, and RAML has no wildcard method.
  Use a `RamlView` subclass, which declares its methods.

## Develop this package

This package lives in the fastRAML repository and is built and tested
separately from it.

```
contrib/aiohttp-raml/
  aiohttp_raml/       the package; the only directory in the wheel
  examples/           runnable applications, not packaged
  tests/              the test suite, not packaged
```

```bash
cd contrib/aiohttp-raml
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy aiohttp_raml/ && uv run pytest -q
```

Run the worked example, which serves `/books`, `/books/{isbn}` and a streamed
`/books/{isbn}/cover` upload with `check_responses` turned on:

```bash
uv run python -m examples.server
```

### The test suites

| Suite | What it checks |
|---|---|
| `test_differential.py` | The generated RAML accepts and rejects exactly what the pydantic model it came from accepts and rejects. One test per payload, so a disagreement names itself. |
| `test_runtime.py` | The running app agrees with both: a request the document rejects is one the app rejects, and for the same reason. |
| `test_render.py` | Each rule about what reaches the document. |
| `test_serve.py` | The three routes over HTTP, and that they stay out of the document. |

`test_differential.py` deliberately shares its models with the copy in
`contrib/fastapi-raml`. Two integrations reading the same models must produce
the same `types:` declarations.
