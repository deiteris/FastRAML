"""Instructions, not reference: what to send for one method, and what comes back.

A reference entry lists everything and links outward, which is right for a
reader looking something up and wrong for one following a guide: they have to
leave the step to learn what `{tenant}` or `Book` means. A step is written for
the second reader. Everything it needs is spelled out in place -- each input's
meaning and constraints in words, the body's fields one level deep -- and it
ends with a single link to the full entry.

It shows only what the step needs: the required inputs, plus the optional ones
the author names. The concrete request or response is built only from values
fastraml validated for exactly that input (`values.py`); where there is none,
it shows a placeholder rather than a value that might be wrong.

A step is never a link target, so it cannot compete with the reference.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, urlsplit

from docutils import nodes
from fastraml import ArrayShape, JsonShape, ObjectShape, RecursiveShape, UnionShape
from sphinx.util import logging

from .markdown import markdown
from .model import plain, target, text
from .render import Writer, bullets, constraint_line, constraints, one_of, status_phrase
from .values import Chosen, choose, supplied, supplied_text

if TYPE_CHECKING:
    from docutils.nodes import Node
    from fastraml import BaseShape, Body, Operation, Parameter, Response, SecurityScheme

logger = logging.getLogger(__name__)

#: What a directive passes when the author supplied no body.
NO_BODY: Any = object()

Where = Literal['path', 'header', 'query parameter']


@dataclass(frozen=True, slots=True)
class Ask:
    """What the author asked a step to show, beyond what is required."""

    #: A scheme to authenticate with, by name; `none` for no authentication.
    security: str | None = None
    media: str | None = None
    #: Optional headers and query parameters to include, by name.
    optional: frozenset[str] = frozenset()
    all_fields: bool = False
    #: Values for inputs, by name, as written in the directive.
    values: dict[str, str] = field(default_factory=dict)
    body: Any = NO_BODY


@dataclass(frozen=True, slots=True)
class Payload:
    """The body a step shows: its media type, its shape, and the value chosen for it."""

    media: str
    shape: BaseShape
    chosen: Chosen | None
    #: The shape in words, for the placeholder when there is no value.
    words: str

    @property
    def shown(self) -> str | None:
        """The body as it goes on the wire, or `None` where there is no value to show.

        Never a placeholder: under a JSON content type it would not be JSON,
        and a highlighter that reads it as JSON rejects it -- which is a
        warning, and under `-W` a failed build, for a spec that merely has no
        example.
        """
        if self.chosen is None:
            return None
        if _is_json(self.media):
            return json.dumps(self.chosen.value, indent=2, ensure_ascii=False, default=str)
        return self.chosen.value if isinstance(self.chosen.value, str) else None

    def lines(self) -> list[str]:
        shown = self.shown
        return [f'Content-Type: {self.media}'] + ([] if shown is None else ['', shown])


@dataclass(frozen=True, slots=True)
class Input:
    where: Where
    name: str
    base: BaseShape
    required: bool
    #: Where it comes from, when that is not the method itself.
    via: str | None = None


class Steps(Writer):
    """Renders `raml:send` and `raml:expect`. Registers no targets; links only at the end."""

    # -- raml:send -------------------------------------------------------------

    def send(self, path: str, method: str, operation: Operation, ask: Ask) -> list[Node]:
        key = f'{method.upper()} {path}'
        request = operation.request
        scheme = self.scheme(key, operation.secured_by, ask.security)
        inputs = [
            *self.parameters_of('path', self.catalogue.base_uri_parameters(), ask),
            *self.parameters_of('path', self.catalogue.endpoints[path].uri_parameters, ask),
            *self.scheme_inputs(scheme, ask),
            *self.parameters_of('header', request.headers if request else {}, ask),
            *self.parameters_of('query parameter', request.query_parameters if request else {}, ask),
        ]
        self.check_names(key, inputs, ask, request.bodies if request else {})
        values = {item.name: self.value(key, item, ask) for item in inputs}
        base_uri = ''.join(self.catalogue.value('base_uri')).rstrip('/')
        out: list[Node] = [
            nodes.paragraph('', '', nodes.Text('Send '), nodes.literal(key, f'{method.upper()} {base_uri}{path}')),
        ]
        out.extend(self.authentication(scheme))
        out.extend(self.inputs(inputs))
        payload = self.payload(key, request.bodies if request else {}, ask)
        if payload is not None:
            out.extend(self.body_fields(payload, ask))
        out.append(self.request_block(method, f'{base_uri}{path}', inputs, values, payload))
        out.extend(_no_example(payload))
        out.append(nodes.paragraph('', '', nodes.Text('Full reference: '), self.xref('method', key, key)))
        return out

    # -- raml:expect -----------------------------------------------------------

    def expect(self, method_key: str, status: str, response: Response, ask: Ask) -> list[Node]:
        key = f'{method_key} {status}'
        inputs = list(self.parameters_of('header', response.headers, ask))
        self.check_names(key, inputs, ask, response.bodies)
        values = {item.name: self.value(key, item, ask) for item in inputs}
        phrase = status_phrase(status)
        out: list[Node] = [
            nodes.paragraph('', '', nodes.Text('You get back '), nodes.literal(status, f'{status} {phrase}'.strip())),
        ]
        out.extend(markdown(text(response.description), self.document))
        out.extend(self.inputs(inputs))
        payload = self.payload(key, response.bodies, ask)
        if payload is not None:
            out.extend(self.body_fields(payload, ask))
        out.append(self.response_block(f'{status} {phrase}'.strip(), inputs, values, payload))
        out.extend(_no_example(payload))
        out.append(nodes.paragraph('', '', nodes.Text('Full reference: '), self.xref('response', key, key)))
        return out

    # -- inputs ----------------------------------------------------------------

    def parameters_of(
        self, where: Where, parameters: dict[str, Parameter], ask: Ask, via: str | None = None
    ) -> list[Input]:
        """The required parameters, and the optional ones the author named."""
        return [
            Input(where, name, parameter.base, parameter.required, via)
            for name, parameter in parameters.items()
            if parameter.required or name in ask.optional or name in ask.values
        ]

    def scheme(self, key: str, secured_by: list[SecurityScheme], wanted: str | None) -> SecurityScheme | None:
        """The requirement to authenticate with: the one asked for, else the first that is a scheme."""
        if not secured_by:
            return None
        if wanted is not None:
            for requirement in secured_by:
                if (requirement.is_null and wanted == 'none') or requirement.name == wanted:
                    return requirement
            self.warn(key, f'is not secured by {wanted!r}; it takes {", ".join(r.name for r in secured_by)}')
        return next((requirement for requirement in secured_by if not requirement.is_null), secured_by[0])

    def scheme_inputs(self, scheme: SecurityScheme | None, ask: Ask) -> list[Input]:
        definition = scheme.definition if scheme is not None and not scheme.is_null else None
        adds = definition.resolved().described_by if definition is not None else None
        if adds is None or scheme is None:
            return []
        via = f'for {scheme.name}'
        return [
            *self.parameters_of('header', adds.headers, ask, via),
            *self.parameters_of('query parameter', adds.query_parameters, ask, via),
        ]

    def check_names(self, key: str, inputs: list[Input], ask: Ask, bodies: dict[str, Body]) -> None:
        """An option naming an input the method does not have is a mistake worth a warning."""
        known = {item.name for item in inputs}
        for name in sorted((ask.optional | set(ask.values)) - known):
            self.warn(key, f'has no input named {name!r}')
        if ask.media is not None and ask.media not in bodies:
            self.warn(key, f'has no {ask.media} body; it has {", ".join(bodies) or "none"}')

    def value(self, key: str, item: Input, ask: Ask) -> Chosen | None:
        written = ask.values.get(item.name)
        if written is None:
            return choose(item.base)
        chosen, error = supplied_text(item.base, written)
        if error is not None:
            self.warn(key, f'{item.name} = {written!r} is not valid: {error}')
        return chosen

    def payload(self, key: str, bodies: dict[str, Body], ask: Ask) -> Payload | None:
        """The body to show: the media type asked for, else the first JSON one, else the first."""
        if not bodies:
            if ask.body is not NO_BODY:
                self.warn(key, 'has no body to give a value for')
            return None
        media = (
            ask.media
            if ask.media in bodies
            else next((media for media in bodies if _is_json(media)), next(iter(bodies)))
        )
        shape = bodies[media].shape
        if shape is None:
            return None
        if ask.body is NO_BODY:
            chosen = choose(shape)
        else:
            chosen, error = supplied(shape, ask.body)
            if error is not None:
                self.warn(key, f'the body given is not valid: {error}')
        return Payload(media, shape, chosen, self.words(shape))

    # -- what the reader reads -------------------------------------------------

    def authentication(self, scheme: SecurityScheme | None) -> list[Node]:
        if scheme is None:
            return []
        if scheme.is_null:
            return [nodes.paragraph('', 'No authentication is needed.')]
        definition = scheme.definition
        kind = definition.resolved().type if definition is not None else ''
        line = nodes.paragraph('', '', nodes.Text('Authenticate with '), nodes.literal(scheme.name, scheme.name))
        if kind:
            line += nodes.Text(f' ({kind})')
        if scheme.compiled_params:
            line += nodes.Text(', with the scope' + ('s ' if len(scheme.compiled_params) > 1 else ' '))
            line += nodes.literal(', '.join(scheme.compiled_params), ', '.join(scheme.compiled_params))
        line += nodes.Text('.')
        return [line]

    def inputs(self, inputs: list[Input]) -> list[Node]:
        rows = []
        for item in inputs:
            where = item.where if item.via is None else f'{item.where}, {item.via}'
            lead = nodes.paragraph('', '', nodes.literal(item.name, item.name), nodes.Text(f' ({where})'))
            if not item.required:
                lead += nodes.Text(', optional')
            rows.append([lead, *self.explained(item.base)])
        if not rows:
            return []
        return [nodes.paragraph('', 'With:'), bullets(rows)]

    def body_fields(self, payload: Payload, ask: Ask) -> list[Node]:
        media = payload.media
        lead = nodes.paragraph(
            '', '', nodes.Text('Body, '), nodes.literal(media, media), nodes.Text(f': {payload.words}')
        )
        out: list[Node] = [lead]
        shape = target(payload.shape).shape
        properties = shape.properties or {} if isinstance(shape, ObjectShape) else {}
        shown = {name: prop for name, prop in properties.items() if prop.required or ask.all_fields}
        rows = []
        for name, prop in shown.items():
            row = nodes.paragraph('', '', nodes.literal(name, name), nodes.Text(f' ({self.words(prop.base)})'))
            if ask.all_fields and not prop.required:
                row += nodes.Text(', optional')
            rows.append([row, *self.explained(prop.base)])
        if rows:
            out.append(bullets(rows))
        hidden = len(properties) - len(shown)
        if hidden:
            out.append(nodes.paragraph('', f'{hidden} optional field{"s" if hidden > 1 else ""} not shown.'))
        return out

    def explained(self, base: BaseShape) -> list[Node]:
        """An input's meaning in place: its description, constraints and allowed values."""
        own = target(base)
        return [
            *markdown(text(own.description) or self.described_by_type(own), self.document),
            *constraint_line(constraints(own)),
            *one_of([plain(member) for member in own.enum or []]),
        ]

    def described_by_type(self, base: BaseShape) -> str | None:
        """For a field typed with a declared object, what that object holds, by name."""
        declared = next(iter(self.declared_parents(base)), None)
        shape = declared.shape if declared is not None else None
        if isinstance(shape, ObjectShape) and shape.properties:
            required = [name for name, prop in shape.properties.items() if prop.required]
            return f'{self.words(base)}: {", ".join(required)}.' if required else None
        return None

    def words(self, base: BaseShape | None) -> str:  # noqa: PLR0911 - one return per kind of type
        """A type in words, never a link: `Book`, `array of string`, `string or number`."""
        if base is None:
            return 'any value'
        if base.alias is not None:
            return self.words(base.alias)
        declared = self.catalogue.declared_at(base.id)
        if declared is not None:
            return declared[1].partition('#')[2]
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            return shape.head.name or 'the enclosing type'
        if isinstance(shape, ArrayShape):
            return f'array of {self.words(shape.items)}'
        if isinstance(shape, UnionShape):
            return ' or '.join(self.words(member) for member in shape.any_of or [])
        if isinstance(shape, JsonShape):
            return 'JSON'
        parents = self.declared_parents(base)
        return self.words(parents[0]) if parents else base.type

    # -- the concrete message --------------------------------------------------

    def request_block(
        self, method: str, url: str, inputs: list[Input], values: dict[str, Chosen | None], payload: Payload | None
    ) -> nodes.literal_block:
        filled = self.fill(url, [item for item in inputs if item.where == 'path'], values)
        parts = urlsplit(filled)
        query = '&'.join(
            f'{quote(item.name)}={_query_text(values[item.name], item.name)}'
            for item in inputs
            if item.where == 'query parameter'
        )
        target_path = (parts.path or '/') + (f'?{query}' if query else '')
        lines = [f'{method.upper()} {target_path} HTTP/1.1']
        if parts.netloc:
            lines.append(f'Host: {parts.netloc}')
        lines.extend(f'{item.name}: {_text(values[item.name], item.name)}' for item in inputs if item.where == 'header')
        return _message(lines, payload)

    def response_block(
        self, status: str, inputs: list[Input], values: dict[str, Chosen | None], payload: Payload | None
    ) -> nodes.literal_block:
        lines = [f'HTTP/1.1 {status}']
        lines.extend(f'{item.name}: {_text(values[item.name], item.name)}' for item in inputs)
        return _message(lines, payload)

    def fill(self, url: str, inputs: list[Input], values: dict[str, Chosen | None]) -> str:
        """The URL with each URI parameter's value, or its placeholder.

        Only a declared parameter is filled. `{version}` is left as written:
        binding it to `version:` is RAML's rule, and fastraml keeps `baseUri`
        as written (see the README).
        """
        names = {item.name for item in inputs}
        return re.sub(
            r'\{([^{}]+)\}',
            lambda found: _text(values.get(found[1]), found[1]) if found[1] in names else found[0],
            url,
        )

    def warn(self, key: str, message: str) -> None:
        logger.warning('%s %s', key, message, location=self.location, type='fastraml', subtype='example')


def _message(lines: list[str], payload: Payload | None) -> nodes.literal_block:
    shown = '\n'.join([*lines, *(payload.lines() if payload is not None else [])])
    return nodes.literal_block(shown, shown, language='http')


def _no_example(payload: Payload | None) -> list[Node]:
    """Why the block ends at its headers, when it does."""
    if payload is None or payload.shown is not None:
        return []
    if payload.chosen is None:
        return [nodes.paragraph('', f'The specification has no example of this {payload.words} body.')]
    # A value that is not text, for a media type that is not JSON: an XML body
    # given as data, say. There is an example; it cannot be written as this.
    return [nodes.paragraph('', f'The example of this {payload.words} body is not written as {payload.media}.')]


def _text(chosen: Chosen | None, name: str) -> str:
    if chosen is None:
        return f'<{name}>'
    value = chosen.value
    return value if isinstance(value, str) else json.dumps(value)


def _query_text(chosen: Chosen | None, name: str) -> str:
    return f'<{name}>' if chosen is None else quote(_text(chosen, name), safe='')


def _is_json(media: str) -> bool:
    return media == 'application/json' or media.endswith('+json')
