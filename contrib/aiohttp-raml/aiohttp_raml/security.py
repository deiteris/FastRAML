"""RAML's security schemes, declared as themselves.

The spec names six, and each is a class here. A scheme says how it is *declared*
-- which is what reaches `securitySchemes:` -- and how it authenticates, which
is what runs per request. Nothing is translated on the way out, because nothing
was expressed in another vocabulary on the way in.

    setup(app, {'books': OAuth2(grants=['authorization_code'], ...)})

`securedBy:` is a list of alternatives, so `@secured` stacks as alternatives and
there is no conjunction to offer. Scopes are the authorisation unit RAML states,
so they are what `permits` is asked about -- a scheme that wants roles calls
them scopes or keeps them to itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Final

from aiohttp import web
from raml_document import SecurityScheme as Declaration
from raml_document import Yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    'AUTH_SCHEMES',
    'AuthenticationError',
    'AuthorizationError',
    'BasicAuth',
    'CustomScheme',
    'DigestAuth',
    'OAuth1',
    'OAuth2',
    'PassThrough',
    'SecurityScheme',
    'middleware',
    'setup',
]

#: Where `setup` puts the registry. A RAML document declares a scheme by name,
#: so the registry is keyed by the name the document will carry.
AUTH_SCHEMES: Final = web.AppKey('AUTH_SCHEMES', dict[str, 'SecurityScheme'])


class AuthenticationError(Exception):
    """The caller is not who they say they are, or did not say."""


class AuthorizationError(Exception):
    """The caller is known, and may not do this."""


class SecurityScheme(ABC):
    """One entry of `securitySchemes:`, and the check behind it."""

    #: The RAML `type:` of this scheme. `x-` prefixed for a custom one.
    raml_type: ClassVar[str]

    @abstractmethod
    async def authenticate(self, request: web.Request) -> Any:
        """Return the caller's identity, or raise `AuthenticationError`."""

    async def permits(self, request: web.Request, identity: Any, scopes: Sequence[str]) -> bool:  # noqa: ARG002 - a hook; the default accepts any authenticated caller
        """May this identity act under these scopes? Authenticated is enough by default."""
        return True

    def settings(self) -> dict[str, Yaml]:
        """The scheme's `settings:` node, where it has one."""
        return {}

    def described_by(self) -> dict[str, Yaml]:
        """The scheme's `describedBy:` node, where it has one."""
        return {}

    def declare(self) -> Declaration:
        """The scheme, as RAML declares it."""
        return Declaration(
            type=self.raml_type,
            description=(self.__doc__ or '').strip() or None,
            settings=self.settings(),
            described_by=self.described_by(),
        )

    async def startup(self, app: web.Application) -> None:  # noqa: B027 - a hook, optional by design
        """Run when the application starts. Override where a scheme needs it."""

    async def cleanup(self, app: web.Application) -> None:  # noqa: B027 - as above
        """Run when the application stops, if `startup` succeeded."""


@dataclass(slots=True)
class _Carried(SecurityScheme):
    """A scheme whose credential travels in named headers or query parameters."""

    headers: dict[str, Yaml] = field(default_factory=dict)
    query_parameters: dict[str, Yaml] = field(default_factory=dict)

    def described_by(self) -> dict[str, Yaml]:
        out: dict[str, Yaml] = {}
        if self.headers:
            out['headers'] = dict(self.headers)
        if self.query_parameters:
            out['queryParameters'] = dict(self.query_parameters)
        return out

    async def authenticate(self, request: web.Request) -> Any:
        raise NotImplementedError


class BasicAuth(SecurityScheme, ABC):
    """RAML's `Basic Authentication`."""

    raml_type: ClassVar[str] = 'Basic Authentication'


class DigestAuth(SecurityScheme, ABC):
    """RAML's `Digest Authentication`."""

    raml_type: ClassVar[str] = 'Digest Authentication'


class PassThrough(_Carried, ABC):
    """RAML's `Pass Through`: a credential in headers or query parameters.

    This is what a bearer token, an API key and a session token all are, and
    `describedBy` is where the document says which header or parameter carries
    it.
    """

    raml_type: ClassVar[str] = 'Pass Through'


class CustomScheme(_Carried, ABC):
    """RAML's `x-{other}`: a scheme the spec does not name.

    Subclasses set `raml_type` to something beginning `x-`.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = getattr(cls, 'raml_type', '')
        if declared and not declared.startswith('x-'):
            raise TypeError(f"{cls.__qualname__}.raml_type must begin with 'x-'")


@dataclass(slots=True)
class OAuth1(SecurityScheme, ABC):
    """RAML's `OAuth 1.0`."""

    raml_type: ClassVar[str] = 'OAuth 1.0'

    request_token_uri: str = ''
    authorization_uri: str = ''
    token_credentials_uri: str = ''
    signatures: Sequence[str] = ()

    def settings(self) -> dict[str, Yaml]:
        out: dict[str, Yaml] = {
            'requestTokenUri': self.request_token_uri,
            'authorizationUri': self.authorization_uri,
            'tokenCredentialsUri': self.token_credentials_uri,
        }
        if self.signatures:
            out['signatures'] = [*self.signatures]
        return out

    async def authenticate(self, request: web.Request) -> Any:
        raise NotImplementedError


@dataclass(slots=True)
class OAuth2(SecurityScheme, ABC):
    """RAML's `OAuth 2.0`.

    `grants` are RAML's own spellings -- `authorization_code`,
    `client_credentials`, `password`, `implicit` -- not another format's.
    """

    raml_type: ClassVar[str] = 'OAuth 2.0'

    access_token_uri: str = ''
    authorization_uri: str | None = None
    grants: Sequence[str] = ()
    scopes: Sequence[str] = ()

    def settings(self) -> dict[str, Yaml]:
        out: dict[str, Yaml] = {'accessTokenUri': self.access_token_uri}
        if self.authorization_uri:
            out['authorizationUri'] = self.authorization_uri
        if self.grants:
            out['authorizationGrants'] = [*self.grants]
        if self.scopes:
            out['scopes'] = [*self.scopes]
        return out

    async def authenticate(self, request: web.Request) -> Any:
        raise NotImplementedError


def setup(app: web.Application, schemes: dict[str, SecurityScheme]) -> None:
    """Register the schemes this app declares, keyed by the name RAML will use."""
    if AUTH_SCHEMES in app:
        raise RuntimeError('Security schemes are already configured; call setup() once')
    app[AUTH_SCHEMES] = schemes
    for scheme in schemes.values():
        app.cleanup_ctx.append(_lifecycle(scheme))
    app.middlewares.append(middleware)


def _lifecycle(scheme: SecurityScheme) -> Any:
    async def context(app: web.Application) -> Any:
        await scheme.startup(app)
        yield
        await scheme.cleanup(app)

    return context


@web.middleware
async def middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """Turn an authentication or authorisation failure into 401 or 403."""
    try:
        return await handler(request)  # type: ignore[no-any-return]
    except AuthenticationError as error:
        return web.json_response({'error': 'Authentication required', 'detail': str(error)}, status=401)
    except AuthorizationError as error:
        return web.json_response({'error': 'Permission denied', 'detail': str(error)}, status=403)
