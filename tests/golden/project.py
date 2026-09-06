"""A stable, position-free JSON projection of a parsed model.

Test-only, and deliberately not part of `pyraml`: this is one opinionated view
of the model for regression purposes, not an API anyone should build on.

**Driven off `__slots__`, not a hand-written field list.** `copyable_slots` is
the same walk `KindBase.clone` uses, and the reason is the same one docs/05 § 1
gives for that: `__slots__` on every model class is a project rule rather than a
convention, so the field list cannot go stale. A facet added to a kind and not
wired into a golden would otherwise be invisible here — which is the failure a
golden layer exists to prevent, so the layer must not have it.

Positions are excluded by default and projected separately by `positions_of`.
A one-line edit to a fixture shifts every position after it, and a golden that
churned on every edit would be re-generated unread, which is the same as not
having one (docs/14 § 2).
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from pyraml.datanode import DataNode, ValueNode
from pyraml.types.base import BaseShape, Parameter, PatternProperty, Property, ScalarFacet, copyable_slots
from pyraml.yamlnode import Node, NodeKind

if TYPE_CHECKING:
    from pyraml.registry import Raml

#: Fields that carry a source position, an object identity or a back-pointer.
#: None of them is stable across an edit to a fixture, and none describes what
#: the model *means*.
_SKIP = frozenset(
    {
        'anchor',
        'id',
        'key_pos',
        'raml',
        'value_pos',
        '_raml',
        '_unwrapped',
        '_visiting',
        'type_expr_refs',
    }
)


def project(raml: Raml) -> dict[str, Any]:
    """The whole parse, as a dict `json.dumps` will accept."""
    return {
        'entry_point': _fragment(raml.entry_point),
        'types': {
            _relative(raml, uri): {name: _shape(base) for name, base in declared.items()}
            for uri, declared in raml.fragment_types.items()
            if declared
        },
        'endpoints': {path: _endpoint(endpoint) for path, endpoint in raml.endpoints.items()},
        'annotations': [_annotation(extension) for extension in raml.domain_extensions],
    }


def positions_of(raml: Raml) -> dict[str, Any]:
    """Every declared shape's span, keyed by file and name. Projected apart.

    Only the cases that are *about* positions carry one of these, so an edit to
    any other fixture cannot churn it.
    """
    return {
        _relative(raml, uri): {
            name: {'key': _position(base.key_pos), 'value': _position(base.value_pos)}
            for name, base in declared.items()
        }
        for uri, declared in raml.fragment_types.items()
        if declared
    }


def _relative(raml: Raml, uri: str) -> str:
    """A URI as a path relative to the entry point's directory.

    Absolute paths differ per machine and per temp directory, so a golden that
    kept them would be unreadable and unrepeatable.
    """
    root = raml.location.rsplit('/', 1)[0] + '/'
    return uri.removeprefix(root)


def _position(position: Any) -> list[int] | None:
    return None if position is None else [position.line, position.column]


# -- shapes -------------------------------------------------------------------


def _shape(base: BaseShape | None, seen: set[int] | None = None) -> Any:  # noqa: PLR0912 - one branch per optional facet
    """One declaration, with its kind's own facets inlined.

    `seen` closes recursion. A self-referential type is a cycle in the model by
    design (docs/07 § 4), so the projection has to be finite by construction
    rather than by hoping the input is a tree.
    """
    if base is None:
        return None
    seen = set() if seen is None else seen
    if base.id in seen:
        return {'recursive_ref': base.name or '<anonymous>'}
    seen = seen | {base.id}

    out: dict[str, Any] = {'name': base.name, 'type': base.type}
    if base.shape is not None:
        out['kind'] = type(base.shape).__name__
    for field in ('display_name', 'description', 'required', 'is_annotation_type'):
        value = getattr(base, field, None)
        if value is not None:
            out[field] = _value(value, seen)
    for field in ('default', 'example', 'examples', 'enum', 'xml', 'allowed_targets'):
        value = getattr(base, field, None)
        if value is not None:
            out[field] = _value(value, seen)
    if base.inherits:
        out['inherits'] = [parent.name or _shape(parent, seen) for parent in base.inherits]
    if base.link is not None:
        out['link'] = type(base.link).__name__
    if base.alias is not None:
        out['alias_of'] = base.alias.name
    if base.custom_facets:
        out['custom_facets'] = {name: _value(node, seen) for name, node in base.custom_facets.items()}
    if base.custom_facet_defs:
        out['declares_facets'] = sorted(base.custom_facet_defs)
    if base.annotations:
        out['annotations'] = sorted(base.annotations)
    if base.type_expr:
        out['type_expr'] = _value(base.type_expr, seen)
    if base.shape is not None:
        out.update(_kind_facets(base.shape, seen))
    return out


def _kind_facets(shape: Any, seen: set[int]) -> dict[str, Any]:
    """Every field the concrete kind declares, skipping the unset ones."""
    out: dict[str, Any] = {}
    for name in copyable_slots(type(shape)):
        if name in _SKIP or name.startswith('__'):
            continue
        value = getattr(shape, name, None)
        if value is None or value in ([], {}):
            continue
        out[name] = _value(value, seen)
    return out


def _node(node: Node) -> Any:
    """A YAML node as its text, never its `repr`.

    Two fields hold raw nodes — `type_expr` and the `pending_facets` a union or
    an unknown kind could not digest — and a node's `repr` carries its source
    position. Projecting that would put a line number into every golden that
    touches a type expression, which is exactly the churn § 2 keeps positions in
    a separate file to avoid.
    """
    if node.kind is NodeKind.SCALAR:
        return node.value
    if node.kind is NodeKind.SEQUENCE:
        return [_node(item) for item in node.content]
    return {node.content[i].value: _node(node.content[i + 1]) for i in range(0, len(node.content) - 1, 2)}


def _value(value: Any, seen: set[int]) -> Any:  # noqa: PLR0911, PLR0912 - one arm per model type
    if isinstance(value, Node):
        return _node(value)
    if isinstance(value, BaseShape):
        return _shape(value, seen)
    if isinstance(value, ScalarFacet):
        return _value(value.value, seen)
    if isinstance(value, Parameter):
        return {'binding': value.binding, 'required': value.required, 'type': _shape(value.base, seen)}
    if isinstance(value, Property):
        return {'required': value.required, 'type': _shape(value.base, seen)}
    if isinstance(value, PatternProperty):
        return {'pattern': value.pattern.pattern, 'type': _shape(value.base, seen)}
    if isinstance(value, DataNode):
        return _plain(value.value)
    if isinstance(value, ValueNode):
        return _plain(value)
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _value(item, seen) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item, seen) for item in value]
    if hasattr(value, 'entries'):  # Examples
        return {name: _value(example, seen) for name, example in value.entries().items()}
    if hasattr(value, 'data'):  # Example
        return _value(value.data, seen)
    return _plain(value)


def _plain(value: Any) -> Any:  # noqa: PLR0911 - as above, for the leaf types
    if isinstance(value, ValueNode):
        # Exactly one of the three applies, and a scalar `None` is a legitimate
        # YAML null — so the mapping and sequence are tested first rather than
        # falling back on `scalar` being None.
        if value.mapping is not None:
            return {str(key): _plain(item) for key, item in value.mapping.items()}
        if value.sequence is not None:
            return [_plain(item) for item in value.sequence]
        return _plain(value.scalar)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# -- fragments, endpoints, annotations ----------------------------------------


def _fragment(fragment: Any) -> Any:
    if fragment is None:
        return None
    out: dict[str, Any] = {'kind': type(fragment).__name__}
    for field in ('title', 'version', 'base_uri', 'media_types', 'protocols', 'usage'):
        value = getattr(fragment, field, None)
        if value is not None:
            out[field] = _value(value, set())
    return out


def _endpoint(endpoint: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        'operations': {method: _operation(op) for method, op in (endpoint.operations or {}).items()},
        'secured_by': _schemes(endpoint.secured_by),
    }
    if endpoint.uri_parameters:
        out['uri_parameters'] = {name: _value(prop, set()) for name, prop in endpoint.uri_parameters.items()}
    return out


def _operation(operation: Any) -> dict[str, Any]:
    """A method, with its parameters' **shapes** and not merely their names.

    The names alone were what the first draft projected, and it made the
    `collection-merge-enum` case — which exists to pin the spec's own
    `[mac, unix, win]` — assert nothing at all: the merged `enum` lives on the
    query parameter's shape. A golden that omits the thing its case is named
    after is worse than no golden, because it looks like cover.
    """
    out: dict[str, Any] = {'responses': {str(code): _response(r) for code, r in (operation.responses or {}).items()}}
    if operation.description is not None:
        out['description'] = _value(operation.description, set())
    if operation.display_name is not None:
        out['display_name'] = _value(operation.display_name, set())
    if operation.secured_by:
        out['secured_by'] = _schemes(operation.secured_by)
    request = operation.request
    if request is not None:
        for field in ('headers', 'query_parameters'):
            declared = getattr(request, field, None)
            if declared:
                out[field] = {name: _value(prop, set()) for name, prop in declared.items()}
        if request.query_string is not None:
            out['query_string'] = _shape(request.query_string)
        if request.bodies:
            out['bodies'] = {media: _body(body) for media, body in request.bodies.items()}
    return out


def _response(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if response.description is not None:
        out['description'] = _value(response.description, set())
    if response.headers:
        out['headers'] = {name: _value(prop, set()) for name, prop in response.headers.items()}
    if response.bodies:
        out['bodies'] = {media: _body(body) for media, body in response.bodies.items()}
    return out


def _body(body: Any) -> Any:
    return _shape(body.shape)


def _schemes(schemes: Any) -> list[Any]:
    """A `securedBy:` list, with the two fields that carry its meaning.

    `is_null` is `securedBy: [null]` — how an author *removes* an inherited
    scheme (docs/09 § A3). It binds to a real definition of type `null` rather
    than to nothing, so a projection that only kept names would render it as a
    scheme called "null" and lose the distinction from one actually named that.

    `compiled_params` is OAuth 2.0's narrowed scope list. An earlier draft read
    a `scopes` attribute that does not exist, so every narrowing projected as
    `[]` — `getattr` with a default turning a wrong field name into a plausible
    empty answer, which is the whole hazard of writing a projector by guesswork.
    """
    return [
        {
            'name': scheme.name,
            'is_null': scheme.is_null,
            'bound': scheme.definition is not None,
            'scopes': scheme.compiled_params,
        }
        for scheme in schemes or []
        if scheme is not None
    ]


def _annotation(extension: Any) -> dict[str, Any]:
    return {
        'name': extension.name,
        'target': str(getattr(extension, 'target', '') or ''),
        'value': _value(extension.value, set()) if getattr(extension, 'value', None) is not None else None,
    }
