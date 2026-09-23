"""Code-first RAML 1.0 for aiohttp.

Declare a handler with pydantic models and annotations; the same declarations
validate every request and produce the RAML document that describes them.

    class BookView(RamlView):
        async def get(self, isbn: str, /) -> Annotated[
            web.Response,
            Responds(200, Book, 'the book'),
            Responds(404, Error, 'no such book'),
        ]:
            \"\"\"One book.\"\"\"
            ...

    add_raml_routes(build_app(), title='Library', version='v2')

A consumer of `fastraml`, not part of it. `docs/01-scope-and-coverage.md` § 3
keeps this out of the parser, and it depends on `aiohttp` and `pydantic`, which
the parser must not.

The RAML model itself lives in `raml-document`, which knows about neither
aiohttp nor fastRAML; `fastapi-raml` builds on the same one.
"""

from __future__ import annotations

from raml_document import Documentation

from aiohttp_raml.decorator import Described, exclude, secured, validate
from aiohttp_raml.errors import RequestError
from aiohttp_raml.injectors import ResponseMismatch
from aiohttp_raml.multipart import File, PartRejected, UploadedFile
from aiohttp_raml.params import Body, Header, QueryParam, UriParam
from aiohttp_raml.render import Report, render
from aiohttp_raml.responses import Responds
from aiohttp_raml.security import (
    AuthenticationError,
    AuthorizationError,
    BasicAuth,
    CustomScheme,
    DigestAuth,
    OAuth1,
    OAuth2,
    PassThrough,
    SecurityScheme,
)
from aiohttp_raml.serve import Served, add_raml_routes, build
from aiohttp_raml.view import RamlView

__all__ = [
    'AuthenticationError',
    'AuthorizationError',
    'BasicAuth',
    'Body',
    'CustomScheme',
    'Described',
    'DigestAuth',
    'Documentation',
    'File',
    'Header',
    'OAuth1',
    'OAuth2',
    'PartRejected',
    'PassThrough',
    'QueryParam',
    'RamlView',
    'Report',
    'RequestError',
    'Responds',
    'ResponseMismatch',
    'SecurityScheme',
    'Served',
    'UploadedFile',
    'UriParam',
    'add_raml_routes',
    'build',
    'exclude',
    'render',
    'secured',
    'validate',
]
