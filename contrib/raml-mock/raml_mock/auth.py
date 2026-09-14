from __future__ import annotations

import base64
import binascii
import hmac
import inspect
from typing import TYPE_CHECKING

from raml_mock.config import AuthDecision
from raml_mock.status import FORBIDDEN, UNAUTHORIZED

if TYPE_CHECKING:
    from aiohttp import web

    from raml_mock.config import Authentication, BasicCredentials, BearerToken
    from raml_mock.routes import MockRoute

__all__ = ['authenticate']

_BASIC = 'Basic Authentication'
_OAUTH2 = 'OAuth 2.0'


async def authenticate(  # noqa: PLR0911, PLR0912 - each RAML security alternative has a distinct outcome
    request: web.Request,
    route: MockRoute,
    config: Authentication | None,
) -> AuthDecision:
    schemes = route.operation.secured_by
    if config is None or not schemes or any(scheme.is_null for scheme in schemes):
        return AuthDecision(allowed=True)

    forbidden_failure: AuthDecision | None = None
    custom_failure: AuthDecision | None = None
    for scheme in schemes:
        definition = None if scheme.definition is None else scheme.definition.resolved()
        if definition is None:
            continue
        if definition.type == _BASIC:
            if _basic(request, config.basic.get(scheme.name, ())):
                return AuthDecision(allowed=True)
            continue
        if definition.type == _OAUTH2:
            token = _bearer(request, config.bearer.get(scheme.name, ()))
            if token is None:
                continue
            required = frozenset(scheme.compiled_params or ())
            if required <= token.scopes:
                return AuthDecision(allowed=True)
            forbidden_failure = AuthDecision(allowed=False, status=FORBIDDEN)
            continue
        validator = config.custom.get(scheme.name) or config.custom.get(definition.type)
        if validator is None:
            continue
        decision = validator(request, route)
        if inspect.isawaitable(decision):
            decision = await decision
        if decision.allowed:
            return decision
        if decision.status == FORBIDDEN:
            forbidden_failure = decision
        custom_failure = decision

    if forbidden_failure is not None:
        return forbidden_failure
    if custom_failure is not None:
        return custom_failure
    return AuthDecision(
        allowed=False,
        status=UNAUTHORIZED,
        headers={'WWW-Authenticate': 'Bearer, Basic realm="raml-mock"'},
    )


def _basic(request: web.Request, configured: tuple[BasicCredentials, ...]) -> bool:
    authorization = request.headers.get('Authorization', '')
    scheme, separator, encoded = authorization.partition(' ')
    if not separator or scheme.lower() != 'basic':
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(':')
    return bool(separator) and any(
        hmac.compare_digest(username, item.username) and hmac.compare_digest(password, item.password)
        for item in configured
    )


def _bearer(request: web.Request, configured: tuple[BearerToken, ...]) -> BearerToken | None:
    authorization = request.headers.get('Authorization', '')
    scheme, separator, value = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer':
        return None
    return next((item for item in configured if hmac.compare_digest(value, item.token)), None)
