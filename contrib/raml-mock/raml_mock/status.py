"""HTTP status ranges, defined here rather than in four modules."""

from __future__ import annotations

_SUCCESS_MIN, _SUCCESS_MAX = 200, 300
STATUS_MIN, STATUS_MAX = 100, 599
CLIENT_ERROR_MIN = 400
UNAUTHORIZED, FORBIDDEN = 401, 403


def is_success(status: int) -> bool:
    return _SUCCESS_MIN <= status < _SUCCESS_MAX


def is_failure(status: int) -> bool:
    return CLIENT_ERROR_MIN <= status <= STATUS_MAX
