# raml-mock

Run an in-process mock HTTP server from a RAML 1.0 definition. The package parses
the document with fastRAML, validates incoming requests against the effective model,
and returns declared examples or deterministic generated values.

It is an aiohttp application, so it can be mounted into another aiohttp process,
used with `aiohttp.test_utils`, or run on an ephemeral loopback port.

## Install

```bash
uv add raml-mock
```

This checkout develops against the fastRAML working tree:

```bash
cd contrib/raml-mock
uv sync --all-extras --dev
```

## Use an application

```python
from aiohttp.test_utils import TestClient, TestServer
from raml_mock import create_app

app = create_app('api.raml')
client = TestClient(TestServer(app))
await client.start_server()
response = await client.get('/books/9780441013593')
print(await response.json())
await client.close()
```

`create_app` always parses with `ParseOptions(unwrap=True, validate=True)`. Pass an
options object to configure the workspace, loaders, regex engine, or depth limit;
the two required flags are still forced on.

An already parsed model can be supplied to `create_app_from_raml`. It must be
unwrapped.

## Run on a loopback port

```python
import aiohttp
from raml_mock import mock_server

async with mock_server('api.raml') as server:
    async with aiohttp.ClientSession(server.url) as client:
        response = await client.get('/books')
        print(await response.json())
```

The socket uses an ephemeral port and is closed when the context exits.

## Run the worked bookstore

The repository example serves the same `fixtures/sample/api.raml` document used
by the viewer and `fastmcp-raml`, including its sibling-library includes:

```bash
uv run python examples/server.py
```

It listens on `127.0.0.1:8080` by default. Pass `--host` or `--port` to change
the listener; for example, `GET /books` returns a collection containing the
`Book` type's Dune example.

## Behavior

Routes come from `Raml.endpoints`. Literal paths take precedence over URI-template
paths. Path, query, and header values are coerced from HTTP text and validated by
fastRAML. Defaults on omitted query parameters, headers, and structured body fields
are applied before an override receives the decoded request.

`HEAD` is answered from the `get` declaration and reports the headers `GET` would
have sent, including `Content-Length` and `Content-Type`, with no body.

JSON numbers are decoded and encoded as decimals, so a body keeps every digit the
client sent rather than the nearest binary float. Scalar bodies written as text
use RAML's spelling: `true`, not Python's `True`.

The default response is the first declared 2xx response, or the first response if
there is no 2xx declaration. Set `X-RAML-Mock-Status` to select another declared
status and `X-RAML-Mock-Example` to select a named example.

Every status is a 3-digit code, and so is every status you configure. RAML has no
`4xx` response class — the parser rejects such a `responses:` key outright
(`docs/08-templates-and-endpoints.md` § 6.1, measured against the reference
implementation) — so there is no wildcard to match against and a mock that
accepted one would be answering for a document that cannot exist.

Response values come from `fastraml.sample` (fastRAML's `docs/16-graph.md`
§ 8.1), in this order:

1. a valid `example` or `examples` entry of the response body shape itself,
   including an included NamedExample, and never one marked `strict: false`
   (a seed selects one deterministically)
2. its `default`
3. its first `enum` member
4. a value composed from its properties or items, each chosen by these same
   rules, so a property's own example is used
5. a deterministic scalar synthesized from the shape's facets

A supertype's example is never used. A body written `application/json: Book`
is `Book` itself, so it answers with `Book`'s examples. One written as a
mapping with `type: Book` is a subtype, which may narrow `Book`, so its value
is composed from `Book`'s properties. `X-RAML-Mock-Example` likewise names only
the body shape's own examples.

An unconstrained array normally synthesizes to `[]`. When its item type has
explicit examples, it contains the distinct examples that fit within `maxItems`.

Request validation returns `400` by default, while representation failures retain
their `415` or `406`. When an API assigns a more specific meaning to a declared
status, supply `validation_status=`. The mapper receives the matched `MockRoute`
and `RequestValidationError`, and must select a response the operation declares;
for example:

```python
def validation_status(route, error):
    return 422 if route.path == '/shelves' and error.issues[0].location == 'body' else 400


app = create_app('api.raml', validation_status=validation_status)
```

## Configure generated responses

`MockOptions` holds behavior that RAML does not define. `GenerationOptions` is
fastRAML's `SampleOptions` under this package's name. A seed produces the same
values for the same route regardless of request order. `collection_size` sets the
size of arrays that have no examples or `minItems`, and `optional_probability`
controls how often generated objects include optional properties.

```python
from raml_mock import GenerationOptions, MockOptions, RouteBehavior, create_app

options = MockOptions(
    generation=GenerationOptions(
        seed='acceptance-suite',
        collection_size=3,
        optional_probability=0.25,
    ),
    routes={('GET', '/reports'): RouteBehavior(status='200', delay=0.05)},
)
app = create_app('api.raml', mock_options=options)
```

Route behavior can select a declared status or named example without requiring
`X-RAML-Mock-Status` or `X-RAML-Mock-Example`. The headers remain available as
per-request overrides.

## Add state

State is opt-in and routes are explicit. `raml-mock` does not guess that a path is
a CRUD resource merely because it uses GET or POST.

```python
from raml_mock import MockOptions, StatefulResource, create_app, state_of

books = StatefulResource(
    name='books',
    key_field='isbn',
    key_parameter='isbn',
    seed_from_example=True,
    collection_get=('GET', '/books'),
    item_get=('GET', '/books/{isbn}'),
    create=('POST', '/books'),
    delete=('DELETE', '/books/{isbn}'),
    delete_missing_status=204,
)
app = create_app('api.raml', mock_options=MockOptions(resources=(books,)))

snapshot = state_of(app).snapshot()
state_of(app).reset('books')
```

Requests and state-backed responses still pass through the RAML shapes and
representation codecs. Each application owns an isolated store. Duplicate creates
return `conflict_status`; missing reads, updates, and deletes use their separate
`*_missing_status` settings; and a body key that differs from its path key returns
`key_mismatch_status`. Set
`seed_from_example=True` to initialize a resource from its item response type
without duplicating fixture data in Python.

`delete_missing_status` is the only one of those that may be a success. DELETE is
idempotent and its answer carries no representation, so deleting something already
gone can correctly be a 204 — which is what `fixtures/sample` declares and calls
idempotent. Every other missing-item case would have to synthesize a body for a
thing that is not there, so those stay between 400 and 599.

A *failure* status the document does not declare still answers, with the generic
problem body. A success has nothing to fall back on, so configuring one the
operation does not declare is refused when the application is built rather than
answered with a status the API never promised.

## Enforce authentication

Authentication is also opt-in. With no `Authentication` configuration, the mock
does not enforce `securedBy`. When configured, alternatives are tried independently,
`null` permits anonymous access, and narrowed OAuth scopes are required.

```python
from raml_mock import Authentication, BasicCredentials, BearerToken, MockOptions

authentication = Authentication(
    basic={'basic': (BasicCredentials('demo', 'secret'),)},
    bearer={
        'oauth': (
            BearerToken('reader-token', frozenset({'read'})),
            BearerToken('writer-token', frozenset({'read', 'write'})),
        )
    },
)
options = MockOptions(authentication=authentication)
```

Basic credentials and Bearer tokens are fixtures, not an authorization server.
OAuth token issuance, expiry, issuer, and audience checks remain the responsibility
of a custom validator. Register custom RAML schemes by scheme name or `x-` type in
`Authentication.custom`; a validator returns `AuthDecision` and may be async.

## Command line

Install the project and run any RAML file directly:

```bash
raml-mock api.raml --workspace . --port 8080
raml-mock api.raml --seed acceptance --collection-size 3
raml-mock api.raml --config mock.json
```

The JSON configuration uses `METHOD /path` route keys and camel-case field names:

```json
{
  "generation": {
    "seed": "acceptance",
    "collectionSize": 3,
    "optionalProbability": 0.25
  },
  "routes": {
    "GET /reports": {"delay": 0.05}
  },
  "authentication": {
    "basic": {
      "basic": [{"username": "demo", "password": "secret"}]
    },
    "bearer": {
      "oauth": [{"token": "reader-token", "scopes": ["read"]}]
    }
  },
  "resources": [{
    "name": "books",
    "keyField": "isbn",
    "keyParameter": "isbn",
    "seedFromExample": true,
    "collectionGet": "GET /books",
    "itemGet": "GET /books/{isbn}",
    "create": "POST /books",
    "delete": "DELETE /books/{isbn}",
    "deleteMissingStatus": 204
  }]
}
```

Every synthesized value is validated by the original fastRAML shape before it is
returned. An impossible required recursion or unsupported string pattern produces
a visible 500 response rather than an invalid mock value.

## Representations

The built-in codecs support:

- JSON and `+json`
- textual scalar bodies
- binary and RAML `file` bodies
- `application/x-www-form-urlencoded`
- `multipart/form-data`

XML strings and files can pass through. Structured object-to-XML conversion is not
implemented: fastRAML retains `xml:` hints but does not yet expose an XML projection,
and this consumer does not duplicate RAML serialization rules.

`text/event-stream` and `application/x-ndjson` are refused. RAML describes one
representation of one entity: it has no notion of a stream, so how many events
one carries and how they are paced are not things a document states, and a mock
answering with a single frame would be a stream in name only.

An operation with only an unsupported response representation returns `406`.

Custom representations implement `BodyCodec` and are passed by base media type:

```python
class CsvCodec:
    async def decode(self, request):
        return (await request.text()).split(',')

    def encode(self, value):
        return ','.join(value)


app = create_app('api.raml', codecs={'text/csv': CsvCodec()})
```

Decoded values and values passed to `encode` are validated against the RAML body
shape. A codec therefore owns representation syntax, not RAML validation.

Security declarations are available through `MockRequest.route.operation` even
when built-in enforcement is disabled. `securedBy` alternatives and OAuth scopes
have already been resolved by fastRAML and remain available to an override.

## Overrides

Pass handlers keyed by the HTTP method and RAML path:

```python
from aiohttp import web
from raml_mock import MockRequest, create_app


async def one_book(request: MockRequest) -> web.Response:
    return web.json_response({'id': request.values.path['id'], 'name': 'Dune'})


app = create_app('api.raml', overrides={('GET', '/books/{id}'): one_book})
```

Requests are decoded and validated before an override runs. A handler receives the
original aiohttp request, the matched RAML route, and typed path/query/header/body
values.

## Development gate

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy raml_mock/
uv run pytest -q
```

This is a downstream consumer. Nothing under `fastraml/` imports it, and language
rules remain in parser passes or reusable views as required by `docs/17-consumers.md`.
