"""Order-independent JSON Schema conjunction for the RAML projection (docs/10 § 7).

The `allOf` reducers behind `schema_projection.py`. Source schemas retain their
resolvers until child declarations are projected. No input schema, cached
projection, or RAML inheritance rule is changed here. A child left unchanged
goes back through the projection, which `intersect` is handed rather than
imports, so the two modules form no cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from functools import cache
from math import ceil, floor, gcd, isfinite, lcm
from typing import TYPE_CHECKING, Any, Final

from fastraml.errors import RamlError
from fastraml.types.base import TYPE_ANY, TYPE_NIL, TYPE_RECURSIVE, TYPE_UNION, BaseShape, Property, ScalarFacet
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.scalars import AnyShape, BooleanShape, IntegerShape, NilShape, NumberShape, StringShape
from fastraml.types.schema_compile import document_of, ref_target, specification_of
from fastraml.types.schema_view import (
    KEYWORD_TYPE,
    attach,
    attach_kind,
    check_depth,
    try_compile,
    unsupported,
    view_base,
    view_data,
)
from fastraml.types.values import EnumValues, as_fraction

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Iterator

    from fastraml.types.schema_view import Projection, Visiting

type _Part = tuple[Projection, dict[str, Any]]
type _Source = tuple[Projection, Any]
type _ScopeKey = tuple[int, str | None]
type _CompositeKey = frozenset[tuple[str | None, str]]
#: The projection of one schema node: the walk that called `intersect`, handed
#: in so that the two modules do not import each other.
type _Project = Callable[[Projection, Any, Visiting], BaseShape]


@dataclass(slots=True, eq=False)
class _Intersection:
    visiting: Visiting
    project: _Project
    strict: bool = True
    built: dict[_CompositeKey, BaseShape] = field(default_factory=dict)
    active: set[_CompositeKey] = field(default_factory=set)
    checked: set[_ScopeKey] = field(default_factory=set)


_UNSUPPORTED: Final = frozenset(
    {
        '$dynamicRef',
        '$recursiveRef',
        'not',
        'if',
        'then',
        'else',
        'oneOf',
        'anyOf',
        'patternProperties',
        'propertyNames',
        'dependencies',
        'dependentSchemas',
        'dependentRequired',
        'contains',
        'minContains',
        'maxContains',
        'additionalItems',
        'unevaluatedItems',
        'unevaluatedProperties',
        'format',
        'prefixItems',
    }
)


@dataclass(frozen=True, slots=True)
class _Drafts:
    """The keywords each draft acts on, and its validator class."""

    active: dict[str, frozenset[str]]
    validators: dict[str, Any]
    known: frozenset[str]


@cache
def _drafts() -> _Drafts:
    """Built on first use, which keeps `jsonschema` off the import path."""
    from jsonschema import (  # noqa: PLC0415 - deferred for startup cost
        Draft4Validator,
        Draft6Validator,
        Draft7Validator,
        Draft201909Validator,
        Draft202012Validator,
    )

    active = {
        'draft-04': frozenset(Draft4Validator.VALIDATORS) | {'exclusiveMinimum', 'exclusiveMaximum'},
        'draft-06': frozenset(Draft6Validator.VALIDATORS),
        'draft-07': frozenset(Draft7Validator.VALIDATORS) | {'then', 'else'},
        'draft2019-09': frozenset(Draft201909Validator.VALIDATORS) | {'then', 'else', 'minContains', 'maxContains'},
        'draft2020-12': frozenset(Draft202012Validator.VALIDATORS) | {'then', 'else', 'minContains', 'maxContains'},
    }
    validators = {
        'draft-04': Draft4Validator,
        'draft-06': Draft6Validator,
        'draft-07': Draft7Validator,
        'draft2019-09': Draft201909Validator,
        'draft2020-12': Draft202012Validator,
    }
    return _Drafts(active, validators, frozenset().union(*active.values()))


_CONDITIONAL: Final = KEYWORD_TYPE | {
    'dependentRequired': 'object',
    'dependentSchemas': 'object',
    'unevaluatedProperties': 'object',
    'minContains': 'array',
    'maxContains': 'array',
    'unevaluatedItems': 'array',
    'prefixItems': 'array',
}
# Length bounds make `$` an absolute end for the fixed-width spelling, including
# on `re`, where `$` alone also matches before a trailing newline (docs/10 § 7).
STRING_FORMATS: Final = {
    'uuid': (r'^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$', 36),
}


class _EmptyIntersection(Exception):  # noqa: N818 - an internal signal, caught at the projection boundary
    __slots__ = ()


class _JsonEnumValues(EnumValues):
    """Use JSON Schema equality on projected enums, not RAML value equality."""

    __slots__ = ('_json_keys',)

    def __init__(self, members: list[Any]) -> None:
        super().__init__(members)
        self._json_keys = {_json_key(member.raw) for member in members}

    def contains(self, value: Any) -> bool:
        return _json_key(value) in self._json_keys


def intersect(  # noqa: PLR0913 - the walk's state, and the walk itself
    context: Projection,
    contents: dict,
    base: BaseShape,
    visiting: Visiting,
    project: _Project,
    *,
    strict: bool = True,
) -> BaseShape:
    try:
        state = _Intersection(visiting, project, strict)
        return _intersect(context, [(context, contents)], base, state, len(visiting))
    except _EmptyIntersection:
        if context.allow_empty:
            raise
        raise unsupported(context, 'unsatisfiable allOf') from None


def _effective_specification(context: Projection, contents: dict[str, Any]) -> tuple[Any, Any]:
    # A JSON Pointer entry's selected schema need not repeat its document's
    # $schema. The compiled validator, not that selected body, owns the draft.
    root_spec = specification_of(type(context.validator).META_SCHEMA)
    if '$schema' in contents:
        return specification_of(contents), root_spec
    resource = context.resolver._registry.get(document_of(context.resolver) or '')  # noqa: SLF001
    if resource is not None and isinstance(resource.contents, dict) and '$schema' in resource.contents:
        return resource._specification, root_spec  # noqa: SLF001 - referencing exposes no specification query
    return root_spec, root_spec


def ref_siblings_apply(context: Projection, contents: dict[str, Any]) -> bool:
    specification, _ = _effective_specification(context, contents)
    return specification.name not in {'draft-04', 'draft-06', 'draft-07'} and bool(
        contents.keys() & (_drafts().known - {'$ref'})
    )


def _parts(  # noqa: PLR0912 - refs, draft-specific siblings, and nested conjunctions
    context: Projection, contents: Any, active: set[_ScopeKey], done: set[_ScopeKey], depth: int
) -> Iterator[_Part]:
    """Flatten conjunctions and references, keeping each leaf's resolution scope."""
    check_depth(context, depth)
    if contents is False:
        raise _EmptyIntersection
    if contents is True:
        return
    key = id(contents), document_of(context.resolver)
    if key in active:
        raise unsupported(context, 'recursive allOf member')
    if key in done:
        return
    active.add(key)
    try:
        from referencing.jsonschema import DRAFT4, DRAFT6, DRAFT7  # noqa: PLC0415 - JSON Schema only

        specification, root_spec = _effective_specification(context, contents)
        subresource = specification.create_resource(contents)
        if subresource.id() is not None:
            context = context.at(context.resolver.in_subresource(subresource), context.pointer)
        reference = contents.get('$ref')
        if isinstance(reference, str):
            if specification != root_spec and len(contents.keys() - {'$schema', '$id', '$ref', 'id'}) > 0:
                # jsonschema.descend selects applicable $ref siblings with the
                # enclosing validator class before evolving to the new draft.
                raise unsupported(context, 'allOf mixed-draft $ref siblings')
            resolved = context.resolver.lookup(reference)
            _, pointer = ref_target(context.resolver, reference)
            yield from _parts(context.at(resolved.resolver, pointer), resolved.contents, active, done, depth + 1)
            # Draft 4/6/7 ignore siblings of $ref; newer drafts apply them.
            if specification in (DRAFT4, DRAFT6, DRAFT7):
                return
        drafts = _drafts()
        keywords = drafts.active.get(specification.name)
        siblings = {
            key: value
            for key, value in contents.items()
            if key not in {'allOf', '$ref'} and (keywords is None or key not in drafts.known or key in keywords)
        }
        if 'if' not in contents:
            siblings.pop('then', None)
            siblings.pop('else', None)
        if 'contains' not in contents:
            siblings.pop('minContains', None)
            siblings.pop('maxContains', None)
        if not isinstance(contents.get('items'), list):
            siblings.pop('additionalItems', None)
        if siblings.keys() & drafts.known:
            yield context, siblings
        for index, member in enumerate(contents.get('allOf', ()) if keywords is None or 'allOf' in keywords else ()):
            yield from _parts(context.into('allOf', str(index)), member, active, done, depth + 1)
    finally:
        active.remove(key)
        done.add(key)


def _types(parts: list[_Part]) -> list[str]:
    allowed: set[str] | None = None
    order: list[str] = []
    for _, contents in parts:
        declared = contents.get('type')
        if declared is None:
            continue
        written = declared if isinstance(declared, list) else [declared]
        if allowed is None:
            order = list(written)
        names = set(written)
        if 'number' in names:
            names.add('integer')
        allowed = names if allowed is None else allowed & names
    if allowed is None:
        return _inferred_types(parts)
    if not allowed:
        raise _EmptyIntersection
    if 'number' in allowed:
        allowed.discard('integer')
    result: dict[str, None] = {}
    for name in order:
        if name in allowed:
            result[name] = None
        elif name == 'number' and 'integer' in allowed:
            result['integer'] = None
    return list(result)


def _inferred_types(parts: list[_Part]) -> list[str]:
    from jsonschema import FormatChecker  # noqa: PLC0415 - deferred for startup cost

    values = _finite_values(parts)
    if values is not None:
        names = dict.fromkeys(_value_type(value) for value in values)
        if 'number' in names:
            names.pop('integer', None)
        return list(names)
    hinted = {_CONDITIONAL[key] for _, contents in parts for key in contents if key in _CONDITIONAL}
    if any(contents.get('format') in FormatChecker.checkers for _, contents in parts):
        hinted.add('string')
    if len(hinted) > 1:
        raise unsupported(parts[0][0], 'allOf constraints on multiple inferred types')
    return list(hinted) or [TYPE_ANY]


def _intersect(
    context: Projection, sources: list[_Source], base: BaseShape, state: _Intersection, depth: int
) -> BaseShape:
    check_depth(context, depth)
    done: set[_ScopeKey] = set()
    parts = [part for scope, schema in sources for part in _parts(scope, schema, set(), done, depth)]
    key = frozenset((document_of(scope.resolver), scope.pointer) for scope, _ in parts)
    previous = state.built.get(key)
    if previous is not None:
        if key in state.active:
            return attach_kind(view_base(context), TYPE_RECURSIVE, RecursiveShape, head=previous)
        return previous
    state.built[key] = base
    state.active.add(key)
    try:
        return _build(context, parts, base, state, depth)
    except BaseException:
        del state.built[key]
        raise
    finally:
        state.active.remove(key)


def _build(context: Projection, parts: list[_Part], base: BaseShape, state: _Intersection, depth: int) -> BaseShape:
    names = _types(parts)
    if len(names) == 1:
        _body(context, parts, names[0], base, state, depth)
    else:
        members = []
        for name in names:
            member = view_base(context)
            try:
                _body(context, parts, name, member, state, depth)
                _enum(member, parts, name)
            except _EmptyIntersection:
                continue
            members.append(member)
        if not members:
            raise _EmptyIntersection
        attach_kind(base, TYPE_UNION, UnionShape, any_of=members)
    _enum(base, parts, names[0] if len(names) == 1 else TYPE_UNION)
    return base


def _body(  # noqa: PLR0913, PLR0917 - the selected kind and recursion state
    context: Projection, parts: list[_Part], name: str, base: BaseShape, state: _Intersection, depth: int
) -> None:
    if state.strict:
        _check_keywords(parts, name)
    if name == 'object':
        _object(context, parts, base, state, depth)
    elif name == 'array':
        _array(context, parts, base, state, depth)
    elif name in {'number', 'integer'}:
        _number(context, parts, name, base)
    elif name == 'string':
        _string(context, parts, base, strict=state.strict)
    elif name == TYPE_ANY:
        attach_kind(base, name, AnyShape)
    elif name == 'boolean':
        attach_kind(base, name, BooleanShape)
    elif name == 'null':
        attach_kind(base, TYPE_NIL, NilShape)
    else:
        raise unsupported(context, f'type: {name}')


def _string(context: Projection, parts: list[_Part], base: BaseShape, *, strict: bool) -> None:
    shape = StringShape(base)
    shape.min_length, shape.max_length = _bounds(base, parts, 'minLength', 'maxLength')
    patterns = {contents['pattern'] for _, contents in parts if 'pattern' in contents}
    for _, contents in parts:
        format_name = contents.get('format')
        if format_name not in STRING_FORMATS:
            continue
        pattern, length = STRING_FORMATS[format_name]
        patterns.add(pattern)
        if (shape.min_length is not None and shape.min_length.value > length) or (
            shape.max_length is not None and shape.max_length.value < length
        ):
            raise _EmptyIntersection
        shape.min_length = ScalarFacet(value=length, location=base.location)
        shape.max_length = ScalarFacet(value=length, location=base.location)
    if len(patterns) > 1:
        raise unsupported(context, 'allOf with multiple patterns')
    if patterns:
        shape.pattern = _pattern_facet(context, base, patterns.pop())
        if shape.pattern is None and (strict or any(contents.get('format') in STRING_FORMATS for _, contents in parts)):
            raise unsupported(context, 'allOf pattern')
    attach(base, 'string', shape)


def _check_keywords(parts: list[_Part], name: str) -> None:
    from jsonschema import FormatChecker  # noqa: PLC0415 - deferred for startup cost

    for scope, contents in parts:
        for key in contents:
            if key not in _UNSUPPORTED:
                continue
            if key == 'format' and (
                name != 'string' or contents[key] in STRING_FORMATS or contents[key] not in FormatChecker.checkers
            ):
                continue
            applies = _CONDITIONAL.get(key)
            if applies is not None and applies != name and not (applies == 'number' and name == 'integer'):
                continue
            raise unsupported(scope, f'allOf keyword: {key}')


def _bounds(
    base: BaseShape, parts: list[_Part], low: str, high: str
) -> tuple[ScalarFacet[int] | None, ScalarFacet[int] | None]:
    lows = [_count(scope, contents[low], low) for scope, contents in parts if low in contents]
    highs = [_count(scope, contents[high], high) for scope, contents in parts if high in contents]
    minimum, maximum = max(lows, default=0), min(highs, default=None)
    if maximum is not None and minimum > maximum:
        raise _EmptyIntersection
    return (
        ScalarFacet(value=minimum, location=base.location) if lows else None,
        ScalarFacet(value=maximum, location=base.location) if maximum is not None else None,
    )


def _count(context: Projection, value: Any, keyword: str) -> int:
    # The entry schema is checked, but a referenced document need not be. JSON
    # Schema accepts integral JSON numbers such as 1.0; RAML count facets are int.
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or (isinstance(value, float) and not isfinite(value))
        or int(value) != value
        or value < 0
    ):
        raise unsupported(context, f'allOf invalid {keyword}')
    return int(value)


def _numeric_bound(parts: list[_Part], keyword: str) -> tuple[Fraction, bool] | None:
    exclusive = 'exclusive' + keyword.title()
    bounds = []
    for context, contents in parts:
        if keyword in contents:
            bounds.append((_fraction(context, contents[keyword]), contents.get(exclusive) is True))
        value = contents.get(exclusive)
        if value is not None and not isinstance(value, bool):
            bounds.append((_fraction(context, value), True))
    if not bounds:
        return None
    return max(bounds) if keyword == 'minimum' else min(bounds, key=lambda bound: (bound[0], not bound[1]))


def _fraction(context: Projection, value: Any) -> Fraction:
    number = as_fraction(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
    if number is None:
        raise unsupported(context, 'allOf invalid numeric facet')
    return number


def _number(  # noqa: PLR0912 - per-kind bounds and exact multiples share their consistency check
    context: Projection, parts: list[_Part], name: str, base: BaseShape
) -> None:
    low, high = _numeric_bound(parts, 'minimum'), _numeric_bound(parts, 'maximum')
    if low and high and (low[0] > high[0] or (low[0] == high[0] and (low[1] or high[1]))):
        raise _EmptyIntersection
    if name == 'integer':
        minimum = Fraction(floor(low[0]) + 1 if low[1] else ceil(low[0])) if low else None
        maximum = Fraction(ceil(high[0]) - 1 if high[1] else floor(high[0])) if high else None
        shape: IntegerShape | NumberShape = IntegerShape(base)
    else:
        if (low and low[1]) or (high and high[1]):
            raise unsupported(context, 'allOf exclusive number bound')
        minimum, maximum = low[0] if low else None, high[0] if high else None
        shape = NumberShape(base)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise _EmptyIntersection
    if isinstance(shape, IntegerShape):
        shape.minimum = ScalarFacet(value=int(minimum), location=base.location) if minimum is not None else None
        shape.maximum = ScalarFacet(value=int(maximum), location=base.location) if maximum is not None else None
    else:
        shape.minimum = ScalarFacet(value=minimum, location=base.location) if minimum is not None else None
        shape.maximum = ScalarFacet(value=maximum, location=base.location) if maximum is not None else None
    multiple: Fraction | None = None
    for scope, contents in parts:
        if 'multipleOf' not in contents:
            continue
        value = as_fraction(contents['multipleOf']) if isinstance(contents['multipleOf'], int | float) else None
        if value is None or value <= 0:
            raise unsupported(scope, 'allOf invalid multipleOf')
        multiple = (
            value
            if multiple is None
            else Fraction(lcm(multiple.numerator, value.numerator), gcd(multiple.denominator, value.denominator))
        )
    if multiple is not None:
        if name == 'integer':
            multiple = Fraction(multiple.numerator)
        shape.multiple_of = ScalarFacet(value=multiple, location=base.location)
        if minimum is not None and maximum is not None and ceil(Fraction(minimum) / multiple) * multiple > maximum:
            raise _EmptyIntersection
    attach(base, name, shape)


def _check_child(scope: Projection, contents: Any, state: _Intersection, depth: int) -> None:
    """Check representability before a canonical cache can bypass the source walk."""
    check_depth(scope, depth)
    target, target_scope = contents, scope
    if isinstance(contents, dict) and set(contents) == {'$ref'}:
        resolved = scope.resolver.lookup(contents['$ref'])
        target, target_scope = resolved.contents, scope.at(resolved.resolver, '')
    key = id(target), document_of(target_scope.resolver)
    if key in state.checked:
        return
    parts = list(_parts(scope, contents, set(), set(), depth))
    names = _types(parts)
    for name in names:
        _check_keywords(parts, name)
    state.checked.add(key)
    children: list[_Source] = []
    for context, part in parts:
        if 'object' in names:
            children.extend(
                (context.into('properties', name), child) for name, child in part.get('properties', {}).items()
            )
        if 'array' in names and isinstance(part.get('items'), dict | bool):
            children.append((context.into('items'), part['items']))
    for context, child in children:
        try:
            _check_child(context, child, state, depth + 1)
        except _EmptyIntersection:
            # The projector decides whether a contradictory descendant forbids an
            # optional property, restricts an array to empty, or makes its parent empty.
            continue


def _child(context: Projection, sources: list[_Source], state: _Intersection, depth: int) -> BaseShape:
    check_depth(context, depth + 1)
    if len(sources) == 1:
        scope, contents = sources[0]
        # An unchanged child uses the ordinary projector, including its canonical
        # reference cache and recursion heads. Only combined children get a new head.
        if state.strict:
            _check_child(scope, contents, state, depth + 1)
        return state.project(replace(scope, allow_empty=True), contents, state.visiting)
    return _intersect(context, sources, view_base(context), state, depth + 1)


def _object(  # noqa: PLR0912 - closed members and impossible optional properties are distinct cases
    context: Projection, parts: list[_Part], base: BaseShape, state: _Intersection, depth: int
) -> None:
    required: dict[str, None] = {}
    properties: dict[str, list[_Source]] = {}
    allowed: set[str] | None = None
    for scope, contents in parts:
        extras = contents.get('additionalProperties')
        if isinstance(extras, dict):
            raise unsupported(scope, 'schema-form additionalProperties')
        declared = contents.get('properties', {})
        if extras is False:
            allowed = set(declared) if allowed is None else allowed & declared.keys()
        required.update(dict.fromkeys(contents.get('required', ())))
        for name, schema in declared.items():
            properties.setdefault(name, []).append((scope.into('properties', name), schema))
    if allowed is not None and not required.keys() <= allowed:
        raise _EmptyIntersection
    for name in required:
        properties.setdefault(name, [])
    built: dict[str, Property] = {}
    for name, sources in properties.items():
        if allowed is not None and name not in allowed:
            continue
        scope = context.into('properties', name)
        try:
            child = (
                _child(scope, sources, state, depth) if sources else attach_kind(view_base(scope), TYPE_ANY, AnyShape)
            )
        except _EmptyIntersection:
            if name in required:
                raise
            if allowed is not None:
                continue
            raise unsupported(scope, 'allOf with an impossible optional property') from None
        built[name] = Property(name=name, base=child, required=name in required)
    shape = ObjectShape(base, properties=built or None)
    shape.min_properties, shape.max_properties = _bounds(base, parts, 'minProperties', 'maxProperties')
    if shape.max_properties is not None and len(required) > shape.max_properties.value:
        raise _EmptyIntersection
    if allowed is not None:
        shape.additional_properties = ScalarFacet(value=False, location=base.location)
        if shape.min_properties is not None and shape.min_properties.value > len(built):
            raise _EmptyIntersection
    attach(base, 'object', shape)


def _array(context: Projection, parts: list[_Part], base: BaseShape, state: _Intersection, depth: int) -> None:
    sources = [(scope.into('items'), contents['items']) for scope, contents in parts if 'items' in contents]
    if any(isinstance(contents, list) for _, contents in sources):
        raise unsupported(context, 'tuple-form items')
    shape = ArrayShape(base)
    shape.min_items, shape.max_items = _bounds(base, parts, 'minItems', 'maxItems')
    if sources:
        try:
            shape.items = _child(context.into('items'), sources, state, depth)
        except _EmptyIntersection:
            if shape.min_items is not None and shape.min_items.value > 0:
                raise
            shape.max_items = ScalarFacet(value=0, location=base.location)
    if any(contents.get('uniqueItems') for _, contents in parts):
        shape.unique_items = ScalarFacet(value=True, location=base.location)
    attach(base, 'array', shape)


def _json_key(value: Any) -> Any:
    """JSON equality, unlike RAML's equality, never coerces numeric strings."""
    if isinstance(value, bool):
        return 'boolean', value
    if isinstance(value, str):
        return 'string', value
    if isinstance(value, dict):
        return 'object', frozenset((key, _json_key(child)) for key, child in value.items())
    if isinstance(value, list):
        return 'array', tuple(_json_key(child) for child in value)
    if value is None:
        return 'null', None
    return 'number', as_fraction(value)


def _value_type(value: Any) -> str:
    kind = _json_key(value)[0]
    if kind == 'number':
        number = as_fraction(value)
        if number is not None and number.denominator == 1:
            return 'integer'
    return str(kind)


def _finite_values(parts: list[_Part]) -> list[Any] | None:
    constraints = [contents['enum'] for _, contents in parts if 'enum' in contents]
    constraints += [[contents['const']] for _, contents in parts if 'const' in contents]
    if not constraints:
        return None
    allowed = {_json_key(value) for value in constraints[0]}
    for constraint in constraints[1:]:
        allowed.intersection_update(_json_key(value) for value in constraint)
    values = [value for value in constraints[0] if _json_key(value) in allowed]
    if not values:
        raise _EmptyIntersection
    return values


def _enum(base: BaseShape, parts: list[_Part], name: str) -> None:
    from jsonschema import FormatChecker  # noqa: PLC0415 - deferred for startup cost
    from jsonschema.validators import validator_for  # noqa: PLC0415

    candidates = _finite_values(parts)
    if candidates is None:
        base.enum = None
        return
    validators = [
        validator_for(contents, default=_validator_at(scope))(
            contents,
            registry=scope.resolver._registry,  # noqa: SLF001 - the compiled schema's registry
            _resolver=scope.resolver,
            format_checker=FormatChecker(),
        )
        for scope, contents in parts
    ]
    values = []
    for value in candidates:
        kind = _value_type(value)
        if name not in {TYPE_ANY, TYPE_UNION, kind} and not (name == 'number' and kind == 'integer'):
            continue
        assert base.shape is not None  # noqa: S101 - constructed before intersecting enum values
        try:
            base.shape.validate(value, '$')
        except RamlError:
            continue
        if not all(validator.is_valid(value) for validator in validators):
            continue
        values.append(view_data(base, value))
    if not values:
        raise _EmptyIntersection
    base.enum = _JsonEnumValues(values)


def _validator_at(scope: Projection) -> Any:
    resource = scope.resolver._registry.get(document_of(scope.resolver) or '')  # noqa: SLF001
    if resource is not None and isinstance(resource.contents, dict) and '$schema' in resource.contents:
        validators = _drafts().validators
        return validators.get(resource._specification.name, validators['draft-07'])  # noqa: SLF001 - authored draft
    return type(scope.validator)


def _pattern_facet(context: Projection, base: BaseShape, value: Any) -> ScalarFacet[re.Pattern[str]] | None:
    # A pattern that does not compile here is dropped rather than the whole
    # shape refused: the projection is a view, and `validate()` still enforces it.
    compiled = try_compile(context, value) if isinstance(value, str) else None
    return None if compiled is None else ScalarFacet(value=compiled, location=base.location)
