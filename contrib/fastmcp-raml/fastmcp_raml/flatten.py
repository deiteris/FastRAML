"""Flat tool arguments, and the map that says where each one goes.

An MCP tool takes one flat object of arguments. An HTTP request puts them in
four places and a body. `RequestDirector._unflatten_arguments` reads
`route.parameter_map` to put each back, so both halves are built together here:
the schema a model fills in, and the routing table that undoes it.

The contract with the director is small and exact::

    parameter_map[flat_name] = {'location': ..., 'openapi_name': ...}

`location` is one of `path`, `query`, `header`, `cookie`, `body`. Where one name
occurs in two places the non-body one is suffixed `name__location`; the director
splits on that suffix only as a fallback, so the map is what actually decides.

`fastmcp._combine_schemas_and_map_params` does this for OpenAPI, and three more
things a RAML-derived schema never needs: rewriting `#/components/schemas/`
refs, merging a top-level `allOf`, and flattening an OpenAPI
`discriminator.mapping`. `fastraml.views.jsonschema` emits none of the three -- a
discriminated union arrives as a plain object, a union as `anyOf`. What is left
is the part both formats share, and `tests/test_flatten.py` runs the two over
the same routes and requires they agree, so it stays shared.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp.utilities.openapi import HTTPRoute, JsonSchema

__all__ = ['flatten']

#: Upstream's, character for character, because it names things: a tool built
#: from RAML and one built from the equivalent OpenAPI should answer to the same
#: argument names. It replaces each offending character rather than each run.
_NOT_ARGUMENT = re.compile(r'[^a-zA-Z0-9_]')


def _body(route: HTTPRoute) -> tuple[JsonSchema | None, dict[str, Any]]:
    """The body's schema and its properties, if it has either.

    The *first* media type wins, which is why `routes.py` puts a JSON one there.
    """
    if route.request_body is None or not route.request_body.content_schema:
        return None, {}
    media = next(iter(route.request_body.content_schema))
    # Copied: upstream writes the description back into the route's own dict.
    schema = dict(route.request_body.content_schema[media])
    if route.request_body.description and not schema.get('description'):
        schema['description'] = route.request_body.description
    return schema, schema.get('properties', {})


def _colliding(route: HTTPRoute, body_properties: dict[str, Any]) -> set[str]:
    """Names that would mean two different things as one argument.

    Either a parameter shares a name with a body property, or two parameters in
    different places share one -- `id` in the path and `id` in the query.
    """
    counts = Counter(parameter.name for parameter in route.parameters)
    return (set(counts) & set(body_properties)) | {name for name, count in counts.items() if count > 1}


def _described(schema: JsonSchema, description: str | None, suffix: str | None) -> JsonSchema:
    described = dict(schema)
    if description and not described.get('description'):
        described['description'] = description
    if suffix is not None:
        existing = described.get('description')
        described['description'] = f'{existing} {suffix}' if existing else suffix
    return described


def _fallback_name(schema: JsonSchema) -> str:
    """What to call a body that is an array or a scalar rather than an object."""
    name = _NOT_ARGUMENT.sub('_', str(schema.get('title', 'body')).lower())
    return 'body_data' if not name or name[0].isdigit() else name


def flatten(route: HTTPRoute) -> tuple[JsonSchema, dict[str, dict[str, str]]]:
    """*route*'s arguments as one object, and where each of them belongs."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    parameter_map: dict[str, dict[str, str]] = {}

    body_schema, body_properties = _body(route)
    colliding = _colliding(route, body_properties)

    for parameter in route.parameters:
        collides = parameter.name in colliding
        name = f'{parameter.name}__{parameter.location}' if collides else parameter.name
        # The suffix is not self-explanatory to a model, so the place is named.
        suffix = f'({parameter.location.capitalize()} parameter)' if collides else None
        properties[name] = _described(parameter.schema_, parameter.description, suffix)
        if parameter.required:
            required.append(name)
        parameter_map[name] = {'location': parameter.location, 'openapi_name': parameter.name}

    if body_schema is not None:
        wanted = route.request_body is not None and route.request_body.required
        if '$ref' in body_schema and not body_properties:
            # Nothing to spread: the body is one named type.
            properties['body'] = body_schema
            parameter_map['body'] = {'location': 'body', 'openapi_name': 'body'}
            if wanted:
                required.append('body')
        elif body_properties:
            for name, schema in body_properties.items():
                properties[name] = schema
                parameter_map[name] = {'location': 'body', 'openapi_name': name}
            if wanted:
                required.extend(body_schema.get('required', []))
        else:
            name = _fallback_name(body_schema)
            properties[name] = body_schema
            parameter_map[name] = {'location': 'body', 'openapi_name': name}
            if wanted:
                required.append(name)

    combined: JsonSchema = {'type': 'object', 'properties': properties, 'required': required}
    if route.request_schemas:
        # Already local and already pruned -- `views/jsonschema.py` writes a
        # definition only for the entry point and for a recursion head.
        combined['$defs'] = route.request_schemas
    return combined, parameter_map
