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
declared model remains unflattened. The copies keep the declarations' ids, so
none of their shapes is added to `Raml.shapes`, and a merge one rejects is
reported by P10 and marks nothing in `Raml.broken`. Public value validation asserts that its root
shape is already unwrapped.

## 2. Declaration consistency

P10 checks:

- all length/count bounds are non-negative and ordered;
- number and integer bounds are ordered, `multipleOf` is nonzero, and `format`
  belongs to the kind's format set;
- arrays, objects, and unions recursively check their child declarations;
- pattern properties cannot coexist with `additionalProperties: false`, judged
  on the effective type, so either may be inherited (*spec section Property
  Declarations*: "explicitly or by inheritance");
- file media-type strings are media ranges (RFC 9110 § 12.5.1): `type/subtype`,
  `type/*` or `*/*`, each name an RFC 6838 § 4.2 restricted-name, optionally
  followed by RFC 9110 parameters. The spec names only `*/*`; `text/*` is as
  meaningful. The root `mediaType` uses the same grammar without wildcards;
  both are `MEDIA_RANGE` and `MEDIA_TYPE` in `parser/facets.py`. A `body:` key
  takes `MEDIA_RANGE`, checked when the body is decoded (docs/08 § 6.3);
- every enum member validates against the shape's non-enum constraints;
- discriminator declarations satisfy the contracts in
  [05](05-type-model.md#6-discriminators).

Unknown shapes fail declaration checking. A uniformly discriminated union also
fails when two members claim the same discriminator value.

A check reads only the shape it is called on and changes nothing, so P10
checks each shape once, however many paths reach it. A second path raises the
first path's error, which the accumulator keeps once.

P10 also checks each effective `queryString` declaration on operations and
security-scheme descriptions. Its flattened shape may admit scalar or object
values, but it must not contain an array member. A declaration that cannot be
unwrapped is skipped here because the ordinary type-validation walk has already
reported that failure.

## 3. Examples, defaults, and enums

Examples validate unless their wrapper says `strict: false`. Defaults always
validate. Named and included examples are read through `examples_of`
([05](05-type-model.md) § 5).
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

P7 publishes the supplied-facet binding index independently of validation, and
public P9 replaces it when it replaces shapes (docs/07 § 2, docs/07 § 4).
`Raml.custom_facet_refs` maps a supplied `DataNode` by identity to its known
facet declarations. P10 shares the ancestor traversal for checking, but only
reports diagnostics and validates values; it does not publish references or
retain private validation copies in the public model. The index materializes
only when a consumer reads it; P10 does not force that work.

A duplicate is one facet name declared by two different ancestors. An alias
shares its referent's declarations ([07](07-resolution-and-inheritance.md)
§ 3), so reaching both is not a duplicate. The spec forbids a facet name that
matches an ancestor's (*spec section User-defined Facets*). That is reported
as `duplicate custom facet` at the type that declares the name again, with the
ancestor's declaration as its origin, whether or not anything inherits from
it. Its subtypes do not report it again, whichever of the two declarations
their walk reaches first and by whatever path (`[A, B]`, `[B, A]`, or through
a further subtype of the redeclaring type), and an alias of it does not report
it at all. Ancestry is followed through alias edges, because each parent in
`type: [A, B]` is an alias of the type it names. The spec says nothing about two unrelated parents declaring the same
name; that is reported as a duplicate too, at each subtype that reaches both.
go-raml follows only the first parent at each step, and reports at the
ancestor's declaration.

Recursion markers are traversal stops for these checks: the corresponding head
is checked where it is declared.

## 5. Instance validation

| Kind | Accepted values and checks |
|---|---|
| any / nil / boolean | any value / `None` / exactly `bool` |
| string | `str`, length bounds, unanchored `pattern` search |
| integer | integral `int`, `float`, `Decimal`, or numeric string; exact bounds, `multipleOf`, and format range |
| number | `int`, `float`, or `Decimal`; exact bounds and `multipleOf` |
| date-only, time-only, datetime-only | strict RAML date/time grammar, ASCII digits only; the schema exports write the same grammar (docs/16 § 8) |
| datetime | RFC 3339 by default or RFC 2616 with that format, ASCII digits only |
| file | `bytes` or `str`; byte-length bounds. `fileTypes` has no instance media-type input and is not enforced here |
| array | `list`, item validation, count bounds, semantic `uniqueItems` |
| object | required properties, declared properties, patterns, extras, and property counts |
| union | discriminated dispatch when available, otherwise first matching member |
| json | compiled JSON Schema validator |
| recursive | validate through the marker head |

Objects report all missing required properties first, validate declared properties
in declaration order, then validate extras against the first matching pattern in
effective order, inherited patterns first
([07](07-resolution-and-inheritance.md) § 4). An extra that no pattern matches is an ordinary additional
property: only `additionalProperties: false` rejects it. Patterns restrict the
keys they match and do not close the key set; the spec's own example accepts
`note: 123` beside `/^note\d+$/: string` "as it does not match the pattern"
(*spec section Property Declarations*).

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
resources in one `SchemaRegistry` per parse. Each fetched document is checked
against its own declared draft before anything crawls it, or, if it declares
none, against the draft of the schema being compiled, as the validator reads it; a
document that fails is `invalid JSON schema`, the cause of an
`unresolvable JSON schema reference` at the referring schema. Schema instance validation delegates
to the compiled validator with `jsonschema.FormatChecker()` enabled. Recognized
`format` values are validated regardless of the schema's draft, including `uuid`
in a schema without a declared draft, through `$ref`, and inside `oneOf`.
Unknown formats remain annotations and do not reject values. The
`jsonschema[format-nongpl]` dependency supplies the optional format-checking
libraries without GPL-licensed dependencies.

`multipleOf` is exact, as a RAML `multipleOf` is
(§ 5): the validator replaces `jsonschema`'s float division with a check on
both numbers read as exact fractions of their decimal text, so `multipleOf: 0.1`
accepts `0.7`. This holds in every supported draft a schema reaches, including a `$ref`
target that declares another `$schema`. A non-number is left to `type`; a
failure keeps the keyword as its `schema_path`.

A schema file is one document per parse: the one it compiles from and the one
a `$ref` into it retrieves are the same resource, so every walk recognises a
reference back into it. An inline schema is not the RAML file it is written
in and is not registered under that file's URI.

Each schema has its own registry: its document and every document the eager
walk reached, crawled once at compilation. The validator, the projection and
the bundle all resolve through it. `referencing` keeps a retrieved document
only in the registry the lookup returns, so on the entry document alone each
of them would re-crawl it and re-retrieve every other document at each `$ref`.
Only what the schema reaches is in it: a parse-wide registry would let one
schema resolve another's `$id` depending on parse order.

RAML sibling facets that reach `JsonShape.decode_facets()` are rejected. Common
facets are removed earlier by `make_shape()` and are therefore currently
accepted, including `displayName`, `description`, `default`, `required`,
`example`, `examples`, `enum`, `xml`, `allowedTargets`, and annotations. This is
current parser behavior; it is broader than the intended wrapper-facet subset
expressed by the RAML restriction.

JSON Schema types are accepted in type expressions, properties, and parameter
declarations, and in a body of any media type; the spec forbids both a parameter and a non-JSON body
(docs/01 § 4.5). A JSON Schema type may be aliased, but RAML inheritance can only
merge an identical schema; attempts to specialize it with RAML constraints fail.

`JsonShape.as_schema()` returns a cached self-contained schema view with external
references bundled locally. A reference back into the bundled file, from a
document pulled in, points into the result (`#`, `#/definitions/line`) rather
than pulling in a copy of it. A pointer within the document stands only when
the bundle is the whole document: a subschema included by pointer
(`schema.json#/definitions/User`) is bundled on its own, so what its
`#/definitions/...` name is pulled in, and a reference to the subschema
itself is `#`. When a referenced document defines an exact external alias
(`definitions: {uuid: {$ref: "uuid.json"}}`), the target is expanded in that
slot, or the slot points to an earlier claim of the same target. References to
the alias point to its slot in the bundle, without creating another copy.
`JsonShape.as_shape()` returns a cached nearest-RAML shape projection for
consumers. Projection shapes are unregistered, positionless, already unwrapped
view objects and must not re-enter parser passes. The projection can lose
semantics: `oneOf` becomes a union, unsupported conditionals,
schema-form `additionalProperties`, tuple `items`, and false schemas fail
projection, and a schema with incompatible inferred kinds projects as `any`.
`allOf` intersects source constraints before projection; it does not use RAML
inheritance. Nested conjunctions, references, and sibling constraints beside
`allOf` participate. Each referenced schema keeps its resolution scope and draft:
drafts 4, 6, and 7 ignore `$ref` siblings; newer drafts apply them. Keyword
recognition also follows the draft: newer keywords remain annotations in drafts
that do not define them. The result has its own identity and containers, with
no invented inheritance edges. Unchanged reference children retain their cached
identity. Nothing mutates an input or uses detached cloning.
The compiled validator supplies the entry draft, even when a JSON Pointer
selects a subschema that does not repeat its document's `$schema` declaration.
Mixed-draft schemas with `$ref` siblings fail projection explicitly rather than
guessing which draft's sibling rules apply.

A schema that fails projection is still a valid type: it validates through its
compiled schema, and the parse reports nothing. `as_shape()` returns `None`
for it, and `projection_error()` returns the `RamlError` the walk raised
(`JSON schema construct has no RAML equivalent` with its `construct`, or
`JSON schema nesting too deep`). Only a `RamlError` is caught. The failure is
cached on the `JsonShape`, and beside the shared projection, so types
including one schema file share one failure and the walk runs once. Both are
keyed by the subschema's canonical URI and the entry schema's draft, the one a
document declaring no `$schema` is read in: `s.json` reached from a draft 7
type and from a 2020-12 schema's `$ref` is two readings, each computed once
and final. A projection shared under a key wins over a failure cached for it.
A type's projection or failure therefore does not depend on which types were
projected before it.
`projected(base)` returns `base` itself, an opaque
JSON Schema leaf that keeps its schema. Views therefore degrade at that type
alone: the tree carries `json_schema` without `projection`, the graph, render
and `nodes` read a leaf, a sample needs a declared value, OpenAPI writes the
type without a constraint and a loss notice naming the error, and the JSON
Schema export writes the schema itself. The failure surfaces as the
`unprojectable-json-schema` lint finding (docs/18 § 2). `to_raml` alone raises
it, since its output is the projection (docs/16 § 8). Tests:
`tests/unit/test_unprojectable_schema.py`.

Ordinary shapes and conjunctions share the reducers for supported type, numeric,
enum, object, and array constraints. A cached reference therefore has the same
restrictions whether it was first projected on its own or as a conjunction's
child. Type-list alternatives carry their applicable constraints, including
length bounds on a nullable string.
Before reusing a cached child, the conjunction checks its nested declarations
for unsupported keywords. An earlier ordinary projection cannot hide those
restrictions.

`format: uuid` projects as a string with an anchored ASCII hexadecimal
8-4-4-4-12 pattern and `minLength: 36`, `maxLength: 36`. This applies to ordinary
schemas, conjunctions, referenced children, and string alternatives of a union.
`format: uuid` alone infers `string` under the existing projection policy.
Uppercase, lowercase, mixed-case, and nil UUIDs are accepted; version and variant
bits are not restricted. Compatible authored length bounds are subsumed by the
fixed length. Incompatible bounds fail projection as `unsatisfiable allOf`.
Repeated UUID formats and an identical pattern contribute one restriction;
an additional distinct pattern fails projection rather than being dropped.
UUID formats inside `oneOf`/`anyOf` members are projected normally. A UUID format
beside `oneOf`/`anyOf` fails projection: the ordinary union projector does not
distribute sibling restrictions. A nullable `type: [string, null]` remains supported.

The UUID projection deliberately accepts only the canonical spelling. The
compiled schema's `FormatChecker` also accepts some noncanonical strings, such
as a UUID followed by `-` or `uuid:`, an underscore in place of a hex digit, or
Unicode decimal digits. Those strings fail the projected pattern. Original
schema instance validation retains the checker's behavior. The length bound
also rejects trailing newlines, since Python's `$` anchor alone matches before
a final newline. Both regex engines use the same projected pattern and bounds.

The conjunction walk visits a shared source once per intersection, retaining its
resolution scope. It does not expand a shared reference graph into a tree.
A subschema whose projection is still open, reached again without a `$ref` (an
`allOf` member's property flattened back into it), is a back-edge to its head,
not a new walk. Recursive references to an original declaration keep that declaration's head;
narrowing the containing object does not narrow its recursive children.
When several recursive child declarations are intersected together, a repeated
set of scoped constraints refers back to the composite head. Pure conjunction
cycles without a concrete type head still fail projection. All walks obey the
parser's depth limit.

The effective restrictions are independent of member order. Declaration order
is still retained for properties, surviving type alternatives, and enum values.
The intersection:

- intersects explicit type sets, with `integer` a subset of `number`;
- applies type-specific keywords only to the selected instance kinds. Without
  an explicit type, finite enum values supply their actual kinds; otherwise a
  single inferred kind uses the existing projection policy;
- takes the strongest minimum and maximum bounds for numbers, string lengths,
  array lengths, and property counts;
- combines `multipleOf` by exact rational least common multiple;
- intersects `enum` and `const` using JSON equality, without coercing numeric
  strings, and removes values that fail the other projected restrictions;
- unions required-property names, including names absent from `properties`, and
  recursively intersects shared property and array-item declarations;
- preserves each closed object's own allowed-property set: properties forbidden
  by any closed member stay forbidden, even if another member declares them;
- requires `uniqueItems` if any member does, and preserves a single distinct
  string pattern;
- converts integer exclusive or fractional bounds to equivalent inclusive integer
  bounds. Number bounds remain exact fractions; an exclusive number bound is
  supported only when a stronger inclusive bound makes it redundant.

A definitions-only schema, an empty schema, or `true` contributes no root
restriction. Referring to a definitions-only document does not apply the
definitions inside it; `$ref` must select a definition to apply its constraints.
Detected contradictions fail projection as `unsatisfiable allOf`, not as an
invalid JSON Schema. An impossible optional property can be omitted from a
closed object; contradictory array items admit only the empty array when its
length bounds allow it.

Conjunctions that the projection cannot represent fail explicitly rather than
selecting one member's restriction. These include distinct string patterns,
effective exclusive number bounds, known string formats other than `uuid`, `patternProperties`,
schema-form `additionalProperties`, tuple or prefix items, `oneOf`/`anyOf`,
conditionals, dependencies, dynamic/recursive reference keywords, unevaluated
keywords, constraints on several inferred kinds, and an impossible optional
property in an open object. Recursive reference-only conjunctions are refused;
recursive child references remain supported. Unknown
keywords and formats remain annotations. Ordinary schema instance validation
continues to use the original compiled schema, independently of projection.

`as_shape_definitions()` additionally projects unused top-level `definitions`
and `$defs` entries and collects named references inside cached subtrees for
document exports, without changing the cached `as_shape_defs()` result. Its
names are distinct when two referenced files have the same stem or both
definition keywords contain the same key; their declaration order is retained.
JSON Pointer escapes in definition keys are decoded. A reference-only cycle has no
RAML type head and fails projection with a diagnostic rather than recursing. A
cycle through a schema with a type head is productive from whichever of its
schemas the walk enters by: a reference-only schema on it projects as that
head, and a back-edge reference is never shared as its target's projection.
`contents` exposes the decoded schema object by convention only; consumers must
not mutate it.

Implementation: `types/validate.py`, `types/values.py`, `types/scalars.py`,
`types/complex_.py`, and the JSON Schema modules `types/jsonschema_.py`,
`types/schema_compile.py`, `types/schema_view.py`, `types/schema_projection.py`,
`types/schema_intersection.py`, and `types/schema_bundle.py`. Tests:
`tests/unit/test_check.py`, `test_validate.py`, `test_jsonschema.py`,
`test_depth_guard.py`, `test_regex_engine.py`, and `test_unprojectable_schema.py`.
