# raml-mock

Run an in-process mock HTTP server from a RAML 1.0 definition. The package parses
the document with pyRAML, validates incoming requests against the effective model,
and returns declared examples or deterministic generated values.

It is an aiohttp application, so it can be mounted into another aiohttp process,
used with `aiohttp.test_utils`, or run on an ephemeral loopback port.

## Install

```bash
uv add raml-mock
```

This checkout develops against the pyRAML working tree:

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

The default response is the first declared 2xx response, or the first response if
there is no 2xx declaration. Set `X-RAML-Mock-Status` to select another declared
status and `X-RAML-Mock-Example` to select a named example.

Response values are selected in this order:

1. `example` on the response body shape
2. the first valid entry from its `examples` (including an included NamedExample)
3. its `default`
4. its first `enum` member
5. the nearest referenced parent type's first valid `example` or `examples` entry
6. a deterministic value synthesized from the shape

An unconstrained array normally synthesizes to `[]`. When its item type has an
explicit example, it contains one example item instead. Parent examples are mock
input candidates, not inherited RAML facets, and are used only when they validate
against the effective response shape.

Every synthesized value is validated by the original pyRAML shape before it is
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
implemented: pyRAML retains `xml:` hints but does not yet expose an XML projection,
and this consumer does not duplicate RAML serialization rules. An operation with
only an unsupported response representation returns `406`.

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

Security declarations are available through `MockRequest.route.operation`, but
the mock does not authenticate requests. `securedBy` alternatives and OAuth scopes
have already been resolved by pyRAML and remain available to an override.

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

This is a downstream consumer. Nothing under `pyraml/` imports it, and language
rules remain in parser passes or reusable views as required by `docs/17-consumers.md`.
