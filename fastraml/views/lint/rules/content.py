"""Rules for routing and reader-facing text."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.nodes import EndPointNode
from fastraml.parser.uritemplates import simple_parameter_segment
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import EndPoint
    from fastraml.views.lint.engine import Context

__all__ = ['NoAmbiguousPaths']

_WILDCARD = '{}'


class _RouteTrie:
    __slots__ = ('children', 'endpoints')

    def __init__(self) -> None:
        self.children: dict[str, _RouteTrie] = {}
        self.endpoints: list[EndPoint] = []

    def overlap(self, segments: list[str], methods: set[str]) -> EndPoint | None:
        stack: list[tuple[_RouteTrie, int]] = [(self, 0)]
        while stack:
            node, index = stack.pop()
            if index == len(segments):
                for endpoint in node.endpoints:
                    if methods & endpoint.operations.keys():
                        return endpoint
                continue
            segment = segments[index]
            if segment == _WILDCARD:
                stack.extend((child, index + 1) for child in node.children.values())
                continue
            exact = node.children.get(segment)
            wildcard = node.children.get(_WILDCARD)
            if exact is not None:
                stack.append((exact, index + 1))
            if wildcard is not None:
                stack.append((wildcard, index + 1))
        return None

    def add(self, segments: list[str], endpoint: EndPoint) -> None:
        node = self
        for segment in segments:
            node = node.children.setdefault(segment, _RouteTrie())
        node.endpoints.append(endpoint)


def _route_segments(endpoint: EndPoint) -> list[str] | None:
    """Literal/wildcard trie keys, or `None` for an expansion we cannot compare."""
    found = []
    for segment in endpoint.full_uri.split('/'):
        if '{' not in segment:
            found.append(segment)
        elif simple_parameter_segment(segment):
            found.append(_WILDCARD)
        else:
            return None
    return found


class NoAmbiguousPaths:
    meta: ClassVar = RuleMeta(
        'no-ambiguous-paths',
        Category.SPEC,
        'endpoint templates should not overlap',
        'Two routes matching the same request path make dispatch depend on undocumented router precedence.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/users/me:\n  get:\n/users/{id}/posts:\n  get:\n',
        bad='#%RAML 1.0\ntitle: t\n/users/me:\n  get:\n/users/{id}:\n  get:\n',
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        routes = _RouteTrie()
        for iri, node in ctx.graph.nodes.items():
            if not isinstance(node, EndPointNode):
                continue
            endpoint = node.entity
            segments = _route_segments(endpoint)
            if segments is None:
                continue
            methods = set(endpoint.operations)
            earlier = routes.overlap(segments, methods)
            if earlier is not None:
                yield ctx.on(
                    self.meta,
                    'endpoint path overlaps another template',
                    endpoint,
                    iri=iri,
                    path=endpoint.full_uri,
                    conflictsWith=earlier.full_uri,
                    methods=','.join(sorted(methods & earlier.operations.keys())),
                )
            routes.add(segments, endpoint)
