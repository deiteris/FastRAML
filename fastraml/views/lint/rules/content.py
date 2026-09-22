"""Rules for routing and reader-facing text."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.nodes import EndPointNode
from fastraml.parser.uritemplates import extract_uri_template_params
from fastraml.positions import UNKNOWN
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fastraml.parser.endpoints import EndPoint
    from fastraml.types.base import BaseShape
    from fastraml.views.lint.engine import Context

__all__ = ['NoAmbiguousPaths']

_EXPRESSION: Final = re.compile(r'\{[^{}]*\}')
_BOOLEANS: Final = {'true': True, 'false': False}


def _admits(base: BaseShape, text: str) -> bool:
    """Whether a URI parameter's type accepts one path segment's text.

    A path carries text, and a typed parameter reads it as its own kind, so the
    literal is tried as a string, a number and a boolean before it is ruled out.
    Numbers go through `Decimal`, never `float` (AGENTS.md).
    """
    candidates: list[object] = [text]
    if text in _BOOLEANS:
        candidates.append(_BOOLEANS[text])
    try:
        number = Decimal(text)
    except InvalidOperation:
        pass
    else:
        if number.is_finite():
            candidates.append(int(number) if number == number.to_integral_value() else number)
    return any(base.validate(candidate) is None for candidate in candidates)


class _Segment:
    """One path segment: literal text, or simple expansions with literal text around them."""

    __slots__ = ('literal', 'names', 'params', 'pattern', 'prefix', 'suffix', 'text')

    def __init__(self, text: str, params: Mapping[str, BaseShape]) -> None:
        self.text = text
        self.literal = '{' not in text
        self.names = [expression.name for expression in extract_uri_template_params(text, '', UNKNOWN)]
        pieces = _EXPRESSION.split(text)
        #: RFC 6570 § 3.2.2: simple expansion percent-encodes `/`, so a value stays in its segment.
        self.pattern = re.compile('(.+)'.join(re.escape(piece) for piece in pieces))
        self.prefix, self.suffix = pieces[0], pieces[-1]
        self.params = {name: params[name] for name in self.names if name in params}

    def accepts(self, literal: str) -> bool:
        """Whether some expansion of this template is the literal segment."""
        if self.literal:
            return self.text == literal
        match = self.pattern.fullmatch(literal)
        if match is None:
            return False
        return all(
            name not in self.params or _admits(self.params[name], value)
            for name, value in zip(self.names, match.groups(), strict=True)
        )

    def meets(self, other: _Segment) -> bool:
        """Whether two templates can expand to one segment: their literal ends must agree."""
        return (self.prefix.startswith(other.prefix) or other.prefix.startswith(self.prefix)) and (
            self.suffix.endswith(other.suffix) or other.suffix.endswith(self.suffix)
        )


class _RouteTrie:
    __slots__ = ('endpoints', 'literals', 'templates')

    def __init__(self) -> None:
        self.literals: dict[str, _RouteTrie] = {}
        #: By template text; the first endpoint to add one supplies its parameter types,
        #: which its descendants inherit.
        self.templates: dict[str, tuple[_Segment, _RouteTrie]] = {}
        self.endpoints: list[EndPoint] = []

    def next_nodes(self, segment: _Segment) -> Iterable[_RouteTrie]:
        if segment.literal:
            exact = self.literals.get(segment.text)
            if exact is not None:
                yield exact
            yield from (child for template, child in self.templates.values() if template.accepts(segment.text))
            return
        yield from (child for text, child in self.literals.items() if segment.accepts(text))
        yield from (child for template, child in self.templates.values() if template.meets(segment))

    def overlap(self, segments: list[_Segment], methods: set[str]) -> EndPoint | None:
        stack: list[tuple[_RouteTrie, int]] = [(self, 0)]
        while stack:
            node, index = stack.pop()
            if index == len(segments):
                for endpoint in node.endpoints:
                    if methods & endpoint.operations.keys():
                        return endpoint
                continue
            stack.extend((child, index + 1) for child in node.next_nodes(segments[index]))
        return None

    def add(self, segments: list[_Segment], endpoint: EndPoint) -> None:
        node = self
        for segment in segments:
            if segment.literal:
                node = node.literals.setdefault(segment.text, _RouteTrie())
            else:
                node = node.templates.setdefault(segment.text, (segment, _RouteTrie()))[1]
        node.endpoints.append(endpoint)


def _route_segments(endpoint: EndPoint) -> list[_Segment] | None:
    """The path's segments, or `None` for a reserved or fragment expansion, which may span segments."""
    params = {name: parameter.base for name, parameter in endpoint.uri_parameters.items()}
    found = []
    for text in endpoint.full_uri.split('/'):
        if '{' in text and any(expression.operator for expression in extract_uri_template_params(text, '', UNKNOWN)):
            return None
        found.append(_Segment(text, params))
    return found


class NoAmbiguousPaths:
    meta: ClassVar = RuleMeta(
        'no-ambiguous-paths',
        Category.SPEC,
        'endpoint templates should not overlap',
        (
            'Two routes matching the same request path make dispatch depend on undocumented router precedence. '
            "A literal segment overlaps a parameter only if the parameter's type accepts it, so `/users/me` "
            'beside `/users/{id}` with `id: integer` is not reported. A segment such as `{name}.json` is '
            'compared by its literal text, since simple expansion cannot produce a `/`.'
        ),
        Severity.WARNING,
        references=('RFC 6570 § 3.2.2',),
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
