# bookstore-api

A worked example exercising every construct the tree carries — including **Markdown** in a description, which is what the RAML spec says every `description:` is. See [Getting started](#) for the `{tenant}` you will need.

Generated from a RAML 1.0 definition by
[`raml-codegen`](https://github.com/deiteris/fastraml). Do not edit by hand —
regenerate.

## Use

```python
from bookstore_api import Client
from bookstore_api.api.books import post_books

client = Client(base_url='https://{tenant}.books.example.com/{version}')
result = post_books.sync(client=client)
```

Each module under `api/` exposes `sync`, `sync_detailed`, `asyncio` and
`asyncio_detailed`. The detailed pair returns a `Response[T]` with the status,
the raw content and the parsed body; the other two return the parsed body alone.

`sync()` returns the **lowest documented 2xx** response. That is this generator's
convention, not something the RAML says; every documented status is reachable
through the detailed variants, and an undocumented one raises
`errors.UnexpectedStatus` when the client is built with
`raise_on_unexpected_status=True`.

## What is in here

| | |
|---|---|
| `client.py` | `Client` and `AuthenticatedClient`, over `httpx` |
| `models/` | 28 declared and nested types, as dataclasses and aliases |
| `api/` | 8 operations, grouped by the first path segment |
| `types.py` | `Unset`/`UNSET`, `Response[T]`, `File` |
| `errors.py` | `UnexpectedStatus` |

## What it does not do

It does not validate. A `pattern:`, a `minimum:` or a `maxItems:` is in the
docstring and not in a check — the document's constraints are recorded, and
whether the server agrees with them is not something a client can answer.
