# 05 - The type model (shapes)

This document owns shape representation, declaration decoding, type inference,
properties, examples, custom-facet declarations, and XML metadata. Resolution,
inheritance, and flattening are in [07](07-resolution-and-inheritance.md).
Declaration and instance validation are in [10](10-validation.md).

## 1. Shape representation

Every RAML declaration is a `BaseShape` plus one kind object. `BaseShape` owns
identity, common facets, source information, reference edges, and parser state;
P7 can replace an `UnknownShape` on an existing base without invalidating
references already taken to that declaration.

The document-visible kinds are `any`, `nil`/`null`, `boolean`, `string`,
`integer`, `number`, `datetime`, `datetime-only`, `date-only`, `time-only`,
`file`, `object`, `array`, and `union`. Internal kinds are `json`, `unknown`,
and `recursive`.

`ScalarFacet[T]` carries one scalar constraint with its location, positions,
include information, and annotations. Arbitrary values use `DataNode`: defaults,
enum members, discriminator values, and custom-facet values are not scalar
facets. `BaseShape` has no value equality or hash; model and YAML-node identity
are load-bearing elsewhere in the parser.

## 2. Kinds and facets

| Kind | Kind-specific facets |
|---|---|
| object | `properties`, `/regex/` pattern properties, `minProperties`, `maxProperties`, `additionalProperties`, `discriminator`, `discriminatorValue` |
| array | `items`, `minItems`, `maxItems`, `uniqueItems` |
| union | `anyOf`; sibling facets are retained until P9 distribution |
| string | `pattern`, `minLength`, `maxLength` |
| number | `minimum`, `maximum`, `multipleOf`, `format` |
| integer | `minimum`, `maximum`, `multipleOf`, `format` |
| file | `fileTypes`, `minLength`, `maxLength` |
| datetime | `format` (`rfc3339` or `rfc2616`) |
| any, nil, boolean, date-only, time-only, datetime-only | none |

Integer bounds are Python `int`; number bounds and `multipleOf` are exact
`Fraction` values built from YAML scalar text. Integer formats have inclusive
ranges: `int8` is `-128..127`, `int16` is `-32768..32767`, `int`/`int32` are
32-bit, and `long`/`int64` are 64-bit. Number formats are `float` and `double`;
the two format sets do not mix. See [numeric formats](01-scope-and-coverage.md#41-numeric-formats).

`properties`, `items`, and `anyOf` are declaration facets. `shape.py` builds
their child shapes before constructing the concrete kind. `items:` accepts one
reference or inline declaration, not a bare sequence; use
`items: {type: [Foo, Bar]}` for multiply inherited items.

## 3. Decoding and inference

`make_shape()` is the general declaration constructor. It records common facets
on the base, keeps remaining YAML key/value pairs flat, determines or defers the
kind, builds declaration facets, and lets the concrete kind decode remaining
facets. An unrecognised facet becomes a custom-facet value until P10 determines
whether an ancestor declared it.

The input form determines the initial kind:

- A mapping may contain `type:` or `schema:` (never both), common facets, and
  kind-specific facets.
- A scalar is a type expression, except an empty string which infers from
  facets, an inline JSON Schema beginning with `{`, an `!include` data-type
  link, or YAML null which uses the caller default.
- A sequence under `type:` is multiple inheritance.

When no explicit type is present, facet hints infer `string`, `number`, `array`,
`object`, or `file`. Conflicting hints fail. `minLength` and `maxLength` can
combine with `fileTypes` to infer `file`; `pattern` is string-only and conflicts
with that result. The normal default is `string`; a body with no type, schema, or
properties defaults to `any`.

`UnknownShape.pending_facets` retains YAML until P7 knows the kind. A union also
retains sibling YAML facets because P9 must decode each against its settled
member kind. See [07](07-resolution-and-inheritance.md#5-union-inheritance).

## 4. Properties and parameters

`make_property()` serves `properties:`, headers, query parameters, URI
parameters, base-URI parameters, and `facets:` declarations.

- `name?` without explicit `required:` declares optional `name`.
- `name` without explicit `required:` declares required `name`.
- With explicit `required:`, a trailing `?` is literal in the name.
- Only one trailing `?` is removed, so `name??` declares optional `name?`.

`Property` is an unbound declaration. Headers, query parameters, URI parameters,
and base-URI parameters are `Parameter` entities that wrap a property with a
binding of `header`, `query`, or `uri`, source positions, and an ID.

A `/regex/` key in `properties:` is a pattern property. It is always optional;
`required:` and a trailing `?` are errors. Explicit names win, then the first
matching pattern in declaration order wins. Patterns use unanchored search. If
any pattern properties exist, an undeclared key must match one regardless of
`additionalProperties`; `additionalProperties: false` cannot be combined with
pattern properties.

## 5. Examples, custom facets, and XML

`example:` accepts raw data or a wrapper mapping containing `value`; the wrapper
may also carry `displayName`, `description`, `strict`, and annotations.
`examples:` is a named mapping or a `NamedExample` include. `example` and
`examples` are mutually exclusive. Consumers must use `Examples.entries()` so
included named examples are included.

`facets:` declares properties required or allowed on subtypes. A declared name
cannot start with `(`. The decoder rejects names known to be built-in common or
kind-specific facets; this guard currently follows `COMMON_FACETS` and
`TYPE_SPECIFIC_FACETS` in `types/shape.py`, whose coverage is narrower than all
facets the decoders accept. Validation semantics are in
[10](10-validation.md#4-custom-facets).

`xml:` decodes `attribute`, `wrapped`, `name`, `namespace`, and `prefix` into
`XmlSerialization`; unknown XML keys fail. The parser retains this metadata for
consumers and projections and does not apply XML wire serialization itself.

## 6. Discriminators

`discriminator` and `discriminatorValue` are object facets. P10 checks that the
named property exists after flattening, is scalar, and accepts any explicit
value. A discriminator value without a discriminator fails. Neither facet is
valid directly on a union. The inline-declaration rule is checked after P7 and
before P9 so an inherited discriminator is not mistaken for one authored inline.

`discriminatorValue` defaults to the declared type name when dispatch or example
validation needs it; it is not materialised on the shape. P10 also checks that
string discriminator values in examples name a known type, even when the example
is `strict: false`.

Uniformly discriminated unions receive a dispatch table at the end of P9. It
requires at least two object members with the same discriminator name and the
same numeric-versus-nonnumeric discriminator property kind, plus a distinct,
scalar claim for every member. Numeric tags use exact numeric keys; strings and
booleans retain their distinct meanings. A present unknown scalar tag fails;
missing, null, nonscalar, or non-uniform cases use ordinary member scanning.

Implementation: `types/base.py`, `types/shape.py`, `types/inference.py`,
`types/complex_.py`, `types/scalars.py`, `types/examples.py`, and `types/xml.py`.
Tests: `tests/unit/test_shape_decode.py`, `test_properties.py`, `test_inference.py`,
`test_examples.py`, `test_check.py`, and `test_validate.py`.
