"""Errors the mock raises, and the request-validation issues it reports."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ['MockGenerationError', 'RequestIssue', 'RequestValidationError']


class MockGenerationError(ValueError):
    """A declared response cannot be represented by a valid mock value."""


@dataclass(slots=True, frozen=True)
class RequestIssue:
    """One stable, machine-readable request validation failure."""

    location: str
    message: str
    detail: object = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {'location': self.location, 'message': self.message}
        if self.detail is not None:
            result['detail'] = self.detail
        return result


class RequestValidationError(ValueError):
    """Request problems and the HTTP status appropriate for them."""

    def __init__(self, status: int, issues: list[RequestIssue]) -> None:
        super().__init__(issues[0].message if issues else 'invalid request')
        self.status = status
        self.issues = issues
