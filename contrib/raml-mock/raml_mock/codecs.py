"""The extension point for a media type the mock does not handle natively."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from aiohttp import web

__all__ = ['BodyCodec']


class BodyCodec(Protocol):
    """Decode requests and encode responses for one custom media type."""

    def decode(self, request: web.Request) -> Awaitable[object]: ...

    def encode(self, value: object) -> str | bytes: ...
