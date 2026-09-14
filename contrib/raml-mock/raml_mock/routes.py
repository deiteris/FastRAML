from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from raml_mock.errors import MockGenerationError

if TYPE_CHECKING:
    from pyraml import EndPoint, Operation, Raml

__all__ = ['MockRoute', 'RouteTable']

_EXPRESSION = re.compile(r'\{([+#]?)([^{}]+)\}')


@dataclass(slots=True, frozen=True)
class MockRoute:
    """One RAML operation with a compiled request-path matcher."""

    endpoint: EndPoint
    operation: Operation
    pattern: re.Pattern[str]
    parameter_names: tuple[str, ...]
    literal_length: int

    @property
    def method(self) -> str:
        return self.operation.method.upper()

    @property
    def path(self) -> str:
        return self.endpoint.full_uri

    def match(self, path: str) -> dict[str, str] | None:
        matched = self.pattern.fullmatch(path)
        if matched is None:
            return None
        return dict(zip(self.parameter_names, matched.groups(), strict=True))


class RouteTable:
    """RAML operations ordered from most specific path to least specific."""

    __slots__ = ('routes',)

    def __init__(self, raml: Raml) -> None:
        assert raml.unwrapped, 'RouteTable needs ParseOptions(unwrap=True)'  # noqa: S101
        routes: list[MockRoute] = []
        signatures: dict[tuple[str, str], str] = {}
        for endpoint in raml.endpoints.values():
            pattern, names, literal_length = _compile(endpoint.full_uri)
            for operation in endpoint.operations.values():
                signature = (operation.method.upper(), pattern.pattern)
                previous = signatures.get(signature)
                if previous is not None:
                    msg = f'ambiguous mock routes: {operation.method.upper()} {previous} and {endpoint.full_uri}'
                    raise MockGenerationError(msg)
                signatures[signature] = endpoint.full_uri
                routes.append(MockRoute(endpoint, operation, pattern, names, literal_length))
        routes.sort(key=lambda route: (-route.literal_length, len(route.parameter_names)))
        self.routes = routes

    def paths(self, path: str) -> list[tuple[MockRoute, dict[str, str]]]:
        found: list[tuple[MockRoute, dict[str, str]]] = []
        for route in self.routes:
            values = route.match(path)
            if values is not None:
                found.append((route, values))
        return found


def _compile(template: str) -> tuple[re.Pattern[str], tuple[str, ...], int]:
    matches = list(_EXPRESSION.finditer(template))
    if any(match.group(1) == '#' for match in matches):
        msg = f'fragment expansion cannot be routed over HTTP: {template}'
        raise MockGenerationError(msg)

    parts: list[str] = []
    names: list[str] = []
    literal_length = 0
    offset = 0
    for match in matches:
        literal = template[offset : match.start()]
        parts.append(re.escape(literal))
        literal_length += len(literal)
        operator, name = match.groups()
        parts.append('(.+)' if operator == '+' else '([^/]+)')
        names.append(name)
        offset = match.end()
    literal = template[offset:]
    parts.append(re.escape(literal))
    literal_length += len(literal)
    return re.compile(''.join(parts)), tuple(names), literal_length
