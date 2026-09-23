# 10 - Validation

This document owns P10 declaration checks, embedded-value validation, custom
facets, and JSON Schema validation. Shape decoding is in [05](05-type-model.md)
and resolution/unwrap are in [07](07-resolution-and-inheritance.md).

## 1. Entry points and prerequisite

`check()` asks whether a declaration is self-consistent. `validate_at(value,
path)` raises for a nonconforming value; public `validate(value)` returns a
`RamlError | None`, and `validate_or_raise(value)` raises.

`validate_shapes(raml)` is P10. It checks all declarations in
`fragment_typedefs`, validates examples, defaults, custom facets, and annotation
applications, and accumulates failures. If `ParseOptions(validate=True)` is used
without `unwrap=True`, P10 clones and unwraps private shape graphs; the caller's
declared model remains unflattened. Public value validation asserts that its root
shape is already unwrapped.

## 2. Declaration consistency

P10 checks:

- all length/count bounds are non-negative and ordered;
- number and integer bounds are ordered, `multipleOf` is nonzero, and `format`
  belongs to the kind's format set;
- arrays, objects, and unions recursively check their child declarations;
- pattern properties cannot coexist with `additionalProperties: false`;
- file media-type strings are well formed or `*/*`;
- every enum member validates against the shape's non-enum constraints;
- discriminator declarations satisfy the contracts in
  [05](05-type-model.md#6-discriminators).

Unknown shapes fail declaration checking. A uniformly discriminated union also
fails when two members claim the same discriminator value.

P10 also checks each effective `queryString` declaration on operations and
security-scheme descriptions. Its flattened shape may admit scalar or object
values, but it must not contain an array member. A declaration that cannot be
unwrapped is skipped here because the ordinary type-validation walk has already
reported that failure.

## 3. Examples, defaults, and enums

Examples validate unless their wrapper says `strict: false`. Defaults always
validate. Named and included examples are read through `Examples.entries()`.
Validation errors identify nested values with `$`-rooted paths.

Enum membership is checked before ordinary instance facets. The declaration
check already established that every enum member satisfies those facets, so a
member need not be checked again.

## 4. Custom facets

A `facets:` block declares values for subtypes, not for its declaring shape.
P10 starts at the shape's parents: the declaring type neither supplies nor is
required to supply its own declared facet. It walks every parent, transitively,
because *spec section User-defined Facets* names "any ancestor type in the
inheritance chain". The walk is breadth-first in declaration order with a
visited set, so a diamond reaches its shared ancestor once. It reports missing
required values, unknown supplied values, values that fail their facet
declaration, and duplicate declarations.

A duplicate is one facet name declared by two different ancestors. An alias
shares its referent's declarations ([07](07-resolution-and-inheritance.md)
§ 3), so reaching both is not a duplicate. The spec forbids a facet name that
matches an ancestor's. It says nothing about two unrelated parents declaring
the same name; that is reported as a duplicate too. go-raml follows only the
first parent at each step.

Recursion markers are traversal stops for these checks: the corresponding head
is checked where it is declared.

## 5. Instance validation

| Kind | Accepted values and checks |
|---|---|
| any / nil / boolean | any value / `None` / exactly `bool` |
| string | `str`, length bounds, unanchored `pattern` search |
| integer | integral `int`, `float`, `Decimal`, or numeric string; exact bounds, `multipleOf`, and format range |
| number | `int`, `float`, or `Decimal`; exact bounds and `multipleOf` |
| date-only, time-only, datetime-only | strict RAML date/time grammar |
| datetime | RFC 3339 by default or RFC 2616 with that format |
| file | `bytes` or `str`; byte-length bounds. `fileTypes` has no instance media-type input and is not enforced here |
| array | `list`, item validation, count bounds, semantic `uniqueItems` |
| object | required properties, declared properties, patterns, extras, and property counts |
| union | discriminated dispatch when available, otherwise first matching member |
| json | compiled JSON Schema validator |
| recursive | validate through the marker head |

Objects report all missing required properties first, validate declared properties
in declaration order, then validate extras against the first matching pattern in
declaration order. With patterns present, an unmatched extra fails; without
patterns, `additionalProperties: false` rejects extras.

`uniqueItems` and enum matching use semantic equality: numeric spellings such as
`1` and `1.0` are equal, but booleans do not equal integers. Enum narrowing
([07](07-resolution-and-inheritance.md) § 4) uses the same equality.
`same_value` defines it. `value_key` gives each value a hashable key, and two
keys are equal exactly when `same_value` calls the values equal:

- a boolean gets a tag;
- a number gets an exact numeric key, where a float goes through its `repr`;
- a string gets the same number when it reads as a number;
- a mapping gets a tagged `frozenset`, and a sequence a tagged tuple.

`uniqueItems` and the subset check therefore look keys up in a set. A value
with no key (NaN, or an unhashable type) is compared with `same_value`, against
other such values only. `tests/property/test_value_key.py` checks that the key
and `same_value` agree.

Numeric values never compare through binary floating point. Bounds are exact
fractions from source text; values use `as_fraction()`, which converts floats
through `repr(value)`, `Decimal` through decimal text, and numeric strings
through their text. A value is a multiple when its exact fraction divided by the
exact `multipleOf` has denominator one. `bool` is rejected before numeric
conversion.

Both string `pattern:` and `/regex/` property names use unanchored search. The
author writes `^` and `$` when anchors are required.

## 6. Discriminator values in data

Before ordinary example conformance, P10 checks string discriminator values
against the known declared values for that discriminator name. This is a
declaration-graph check and is not waived by `strict: false`. The index is keyed
by discriminator name, so unrelated hierarchies using the same name share known
values; this can be permissive but does not create false rejection.

## 7. JSON Schema

An inline JSON document or included `.json` schema produces `JsonShape`. It is
compiled during decoding, not lazily under P10. Compilation validates the
schema's declared draft (draft 7 if absent), applies the parser depth limit,
eagerly resolves `$ref` through the parse's `ResourceLoader`, and caches fetched
resources in one `SchemaRegistry` per parse. Schema instance validation delegates
to the compiled validator.

RAML sibling facets that reach `JsonShape.decode_facets()` are rejected. Common
facets are removed earlier by `make_shape()` and are therefore currently
accepted, including `displayName`, `description`, `default`, `required`,
`example`, `examples`, `enum`, `xml`, `allowedTargets`, and annotations. This is
current parser behavior; it is broader than the intended wrapper-facet subset
expressed by the RAML restriction.

JSON Schema types are accepted in type expressions, properties, and parameter
declarations. A JSON Schema type may be aliased, but RAML inheritance can only
merge an identical schema; attempts to specialize it with RAML constraints fail.

`JsonShape.as_schema()` returns a cached self-contained schema view with external
references bundled locally. `JsonShape.as_shape()` returns a cached nearest-RAML
shape projection for consumers. Projection shapes are unregistered, positionless,
already unwrapped view objects and must not re-enter parser passes. The projection
can lose semantics: `oneOf` becomes a union, unsupported conditionals,
schema-form `additionalProperties`, tuple `items`, and false schemas fail
projection, and a schema with incompatible inferred kinds projects as `any`.
`contents` exposes the decoded schema object by convention only; consumers must
not mutate it.

Implementation: `types/validate.py`, `types/values.py`, `types/scalars.py`,
`types/complex_.py`, and `types/jsonschema_.py`. Tests:
`tests/unit/test_check.py`, `test_validate.py`, `test_jsonschema.py`,
`test_depth_guard.py`, and `test_regex_engine.py`.
