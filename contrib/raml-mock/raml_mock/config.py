"""Mock behaviour options: generation, per-route responses, authentication, state."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import TYPE_CHECKING, Protocol

from raml_mock.status import (
    CLIENT_ERROR_MIN,
    STATUS_MAX,
    STATUS_MIN,
    is_failure,
    is_success,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from aiohttp import web

    from raml_mock.routes import MockRoute

__all__ = [
    'AuthDecision',
    'Authentication',
    'BasicCredentials',
    'BearerToken',
    'CustomAuthValidator',
    'GenerationOptions',
    'MockOptions',
    'RouteBehavior',
    'RouteKey',
    'StatefulResource',
]

type RouteKey = tuple[str, str]


@dataclass(slots=True, frozen=True)
class GenerationOptions:
    seed: int | str | None = None
    collection_size: int | None = None
    optional_probability: float = 0.0

    def __post_init__(self) -> None:
        if self.collection_size is not None and self.collection_size < 0:
            raise ValueError('collection_size must be non-negative')
        if not isfinite(self.optional_probability) or not 0.0 <= self.optional_probability <= 1.0:
            raise ValueError('optional_probability must be between zero and one')


@dataclass(slots=True, frozen=True)
class RouteBehavior:
    status: str | None = None
    example: str | None = None
    delay: float = 0.0

    def __post_init__(self) -> None:
        if self.status is not None and not _valid_response_status(self.status):
            raise ValueError('status must be a 3-digit HTTP status code such as 404')
        if not isfinite(self.delay):
            raise ValueError('response delays must be finite')
        if self.delay < 0:
            raise ValueError('response delays must be non-negative')


@dataclass(slots=True, frozen=True)
class BasicCredentials:
    username: str
    password: str = field(repr=False)


@dataclass(slots=True, frozen=True)
class BearerToken:
    token: str = field(repr=False)
    scopes: frozenset[str] = frozenset()


@dataclass(slots=True, frozen=True)
class AuthDecision:
    allowed: bool
    status: int = 401
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.allowed and not CLIENT_ERROR_MIN <= self.status <= STATUS_MAX:
            raise ValueError('a denied authentication status must be between 400 and 599')


class CustomAuthValidator(Protocol):
    def __call__(
        self,
        request: web.Request,
        route: MockRoute,
    ) -> AuthDecision | Awaitable[AuthDecision]: ...


@dataclass(slots=True, frozen=True)
class Authentication:
    basic: Mapping[str, tuple[BasicCredentials, ...]] = field(default_factory=dict)
    bearer: Mapping[str, tuple[BearerToken, ...]] = field(default_factory=dict)
    custom: Mapping[str, CustomAuthValidator] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class StatefulResource:
    name: str
    key_field: str
    key_parameter: str
    initial: tuple[Mapping[str, object], ...] = ()
    seed_from_example: bool = False
    collection_get: RouteKey | None = None
    item_get: RouteKey | None = None
    create: RouteKey | None = None
    update: RouteKey | None = None
    delete: RouteKey | None = None
    item_missing_status: int = 404
    update_missing_status: int = 404
    delete_missing_status: int = 404
    conflict_status: int = 409
    key_mismatch_status: int = 400

    def __post_init__(self) -> None:
        statuses = (
            self.item_missing_status,
            self.update_missing_status,
            self.conflict_status,
            self.key_mismatch_status,
        )
        if any(not is_failure(status) for status in statuses):
            raise ValueError('stateful failure statuses must be between 400 and 599')
        # A DELETE of something that is not there is the one missing-item case
        # with a correct *success* reading, so it is the one excluded above.
        # DELETE is idempotent, and its answer carries no representation, so a
        # mock saying 204 is not inventing a body it does not have -- which is
        # what a 2xx would mean for `item_missing_status` or
        # `update_missing_status`. `fixtures/sample` declares `204:` on its own
        # delete and describes it as idempotent; without this the mock cannot
        # serve the document it is given.
        if not is_failure(self.delete_missing_status) and not is_success(self.delete_missing_status):
            raise ValueError('delete_missing_status must be a 2xx status or between 400 and 599')


@dataclass(slots=True, frozen=True)
class MockOptions:
    generation: GenerationOptions = GenerationOptions()
    routes: Mapping[RouteKey, RouteBehavior] = field(default_factory=dict)
    authentication: Authentication | None = None
    resources: tuple[StatefulResource, ...] = ()


def _valid_response_status(status: str) -> bool:
    # 3-digit only. A `4xx` class is OpenAPI, not RAML: the parser rejects such
    # a `responses:` key outright (docs/08 § 6.1), so no parsed operation
    # can declare one and a behavior naming one could never be selected.
    return status.isdigit() and STATUS_MIN <= int(status) <= STATUS_MAX
