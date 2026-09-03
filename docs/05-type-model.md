# 05 — The type model (shapes)

"Shape" is the internal name for a RAML type declaration, borrowed from go-raml
(itself borrowed from AMF). Using a distinct word matters: `type` is taken by
Python, and a RAML type is not a Python type.

## 1. Split: `BaseShape` + kind

Every declaration is **one `BaseShape` holding one kind-specific shape object**.

```python
class BaseShape:
    __slots__ = (
        "id", "name", "type", "shape",
        # common facets (spec § Type Declarations)
        "display_name", "description", "default", "required",
        "example", "examples", "enum", "xml",
        # structure
        "inherits",          # list[BaseShape]  — the `type:` parents
        "alias",             # BaseShape | None — pure reference, not inheritance
        "link",              # DataTypeFragment | None — !include target
        "custom_facets",           # dict[str, DataNode]  values
        "custom_facet_defs",       # dict[str, Property]  declarations (`facets:`)
        "annotations",             # dict[str, DomainExtension]
        # provenance / tooling
        "type_expr", "type_expr_refs", "is_annotation_type",
        "anchor", "location", "key_pos", "value_pos",
        # state
        "_unwrapped", "_visiting", "_raml",
    )
```

```python
class Shape(Protocol):            # the kind-specific half
    base: BaseShape
    def decode_facets(self, pairs: list[Node]) -> None: ...
    def inherit(self, source: Shape) -> Shape: ...
    def alias_to(self, source: Shape) -> Shape: ...
    def check(self) -> None: ...              # is the declaration self-consistent?
    def validate(self, value: Any, path: str) -> None: ...  # does data conform?
    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> Shape: ...
    def is_scalar(self) -> bool: ...
```

Why the split rather than a class per type with inherited common facets?

- The kind of a declaration is **not known when the object is created**. `type:
  Foo` cannot be classified until `Foo` resolves. The parser creates the
  `BaseShape` immediately (it needs an identity to register and to reference),
  attaches an `UnknownShape`, and swaps in the real kind during P7 — *without*
  invalidating any reference already taken to the `BaseShape`.
- Kind can change again during resolution: `Foo | Bar` starts as unknown, becomes
  a union; a single-member union collapses to its member. Mutating one field
  beats rebuilding an object other things point at.
- Common facets are decoded once, in one place, regardless of kind.

Concrete kinds: `AnyShape`, `NilShape`, `ObjectShape`, `ArrayShape`,
`UnionShape`, `StringShape`, `NumberShape`, `IntegerShape`, `BooleanShape`,
`FileShape`, `DateTimeShape`, `DateTimeOnlyShape`, `DateOnlyShape`,
`TimeOnlyShape`, plus three internal ones: `JsonShape` (external JSON Schema),
`UnknownShape` (not yet resolved), `RecursiveShape` (cycle marker).

## 2. Facet values carry positions

A facet is never a bare Python value:

```python
class ScalarFacet(Generic[T]):
    __slots__ = ("value", "location", "key_pos", "value_pos", "include", "annotations")
```

So `minLength: 5` is a `ScalarFacet[int]` and "minLength must be ≤ maxLength"
can point at the exact line. `annotations` holds extensions collected from the
annotated-scalar form ([03](03-yaml-and-io.md) § 7).

The class lives in `types/base.py`; `make_scalar_facet`, which builds one from a
key/value pair, lives in `parser/facets.py` and is the single import `types/`
takes from `parser/` ([02](02-architecture.md) § 2). A shape's `decode_facets`
calls it once per scalar facet, passing the converter for that facet's type.

The rule: **facets that hold a single scalar use `ScalarFacet[T]`; facets that
hold arbitrary user data use `DataNode`.** `default`, `enum` members and
`discriminatorValue` are `DataNode`; `minLength`, `pattern`, `format`,
`additionalProperties`, `discriminator` are `ScalarFacet`.

## 3. Per-kind facets

| Kind | Facets | Value types |
|------|--------|-------------|
| `object` | `properties`, `patternProperties`*, `minProperties`, `maxProperties`, `additionalProperties`, `discriminator`, `discriminatorValue` | `dict[str, Property]`, `dict[str, PatternProperty]`, `ScalarFacet[int]`×2, `ScalarFacet[bool]`, `ScalarFacet[str]`, `DataNode` |
| `array` | `items`, `minItems`, `maxItems`, `uniqueItems` | `BaseShape`, `ScalarFacet[int]`×2, `ScalarFacet[bool]` |
| `union` | `anyOf` | `list[BaseShape]` |
| `string` | `pattern`, `minLength`, `maxLength` | `ScalarFacet[Pattern]`, `ScalarFacet[int]`×2 |
| `number` | `minimum`, `maximum`, `multipleOf`, `format` | `ScalarFacet[Fraction]`×3, `ScalarFacet[str]` |
| `integer` | same four | `ScalarFacet[int]`×2, `ScalarFacet[Fraction]`, `ScalarFacet[str]` |
| `file` | `fileTypes`, `minLength`, `maxLength` | `list[Node[str]]`, `ScalarFacet[int]`×2 |
| `datetime` | `format` (`rfc3339` \| `rfc2616`) | `ScalarFacet[str]` |
| `boolean`, `nil`, `any`, other dates | — | |

\* `patternProperties` is not a RAML facet name; it is where keys of the form
`/regex/` inside `properties:` are routed (spec § Additional Properties).

```python
class Property:  # a named property, a header, a query parameter, a facet def
    __slots__ = ("name", "base", "required")


class PatternProperty:  # always optional by definition
    __slots__ = ("pattern", "base")  # pattern is a compiled regex
```

### 3.1 Numerics

go-raml uses `big.Int` for integer bounds and `big.Rat` for `multipleOf` and
number bounds, so that `minimum: 10000000000000000001` and
`multipleOf: 1.1` are exact. Python:

- integer bounds → plain `int` (arbitrary precision natively);
- number bounds and `multipleOf` → `fractions.Fraction`, constructed from the
  **raw scalar text** (`Fraction("1.1")`), never from a `float`. `Fraction(1.1)`
  would embed the binary-float error and make `multipleOf: 1.1` reject `2.2`.

`Fraction` arithmetic is slow, so validation takes an int fast path first and
only builds a `Fraction` when a bound is non-integral or the value is a
`Decimal`/float that must be compared exactly. See [12](12-performance.md) § 9.

### 3.2 `format` on integers

`format` is validated against a size table which doubles as the inheritance
compatibility check:

```python
INTEGER_FORMATS = {"int8": 0, "int16": 1, "int32": 2, "int": 2, "int64": 3, "long": 3}
NUMBER_FORMATS = {"float", "double"}
```

At validation time an `int8` value must lie in `[-2⁷, 2⁷)`; the limit is
`1 << (8 << size - 1)`. Deviation **D2** ([01](01-scope-and-coverage.md)) forbids
crossing the two tables.

## 4. Decoding a declaration

`make_shape(key_node, value_node, location, default_type)` is the single entry
point. Every property, header, query parameter, body, URI parameter, type
declaration and inline declaration goes through it.

```
value_node kind?
├── SCALAR or SEQUENCE ────► it is a type expression / multiple inheritance;
│                            no facets. (`base.decode` returns it as the type node)
└── MAPPING ──────────────► iterate key/value pairs once:
       ├── type: / schema:  → the type node (mutually exclusive → error if both)
       ├── displayName:     → ScalarFacet[str]
       ├── description:     → ScalarFacet[str]
       ├── required:        → ScalarFacet[bool]
       ├── facets:          → custom facet declarations
       ├── example:         → Example       (error if `examples` already set)
       ├── examples:        → Examples      (error if `example` already set)
       ├── default:         → DataNode
       ├── enum:            → list[DataNode]
       ├── xml:             → XmlSerialization
       ├── (annotation):    → DomainExtension
       └── anything else    → appended to `facets`, a flat [k0,v0,k1,v1,…] list
```

The leftover `facets` list is then handed to the concrete shape's
`decode_facets`. pyRAML keeps go-raml's allocation-lean structure here: one pass,
one list, no intermediate dict, and the concrete shape sees only the keys it
might handle.

Any key the concrete shape does not recognise becomes a **custom facet value** in
`base.custom_facets`. P10 validates those values against the `facets:`
declarations found in the inheritance chain.

### 4.1 Determining the type

```
type node absent?  → infer from the facet key set (§4.2)
type node present:
  SCALAR
    tag !!str, empty value  → infer from facets
    tag !!str, starts "{"   → inline JSON Schema  → JsonShape
    tag !!str, otherwise    → a type expression   → UnknownShape (resolved in P7)
    tag !include            → parse the DataType fragment; store as base.link
    tag !!null              → "string" (spec § Determine Default Types)
  SEQUENCE                  → multiple inheritance: each item is itself a shape;
                              base.inherits = those; kind = "composite" (internal)
  MAPPING / ALIAS / DOC     → error
```

The raw expression text is retained on `base.type_expr` (a `Node[str]` with its
position) so P7 can report column-accurate errors inside a type expression and so
tooling can offer go-to-definition on each name within it.

### 4.2 Default-type inference

Spec § Determine Default Types, implemented as a single pass over the facet key
list:

```python
FACET_TYPE_HINT = {
    "minLength": "string",
    "maxLength": "string",
    "pattern": "string",  # string-only, never file
    "minimum": "number",
    "maximum": "number",
    "multipleOf": "number",
    "minItems": "array",
    "maxItems": "array",
    "uniqueItems": "array",
    "items": "array",
    "properties": "object",
    "minProperties": "object",
    "maxProperties": "object",
    "additionalProperties": "object",
    "discriminator": "object",
    "fileTypes": "file",
}
```

Rules, in order:

1. If any facet is unique to a type, that is the type.
2. Conflicting hints are an error: `detected types by facets are not equal`.
3. `string`/`file` are reconciled to `file` — `minLength`/`maxLength` are shared
   between them — **unless** `pattern` was seen, which is string-only.
4. Nothing inferred → the caller's default.

The caller's default is `"string"` everywhere **except body declarations**, where
it is `"any"`. Spec § Determine Default Types: "The default type `any` is applied
to any `body` node that does not contain `properties`, `type`, or `schema`."
Hence two thin wrappers: `make_shape` and `make_body_shape`.

## 5. Properties and optionality

Spec § Property Declarations defines four cases. Rules 3 and 4 are the ones
implementations most often get wrong:

1. `name?` with no explicit `required:` → property `name`, optional.
2. `name` with no explicit `required:` → property `name`, required.
3. `name?` **with** an explicit `required:` → the `?` is **part of the name**;
   the property is literally called `name?` and its requiredness is what
   `required:` says.
4. `name??` → property `name?`, optional (rule 1 applied to the name `name?`).

Implementation: chomp **one** trailing `?`, remember whether one was chomped, and
then:

```python
if shape.required is None:
    required = not had_question
    final_name = chomped
else:
    required = shape.required.value
    final_name = key_node.value if had_question else chomped
```

The same function serves `properties:`, `headers:`, `queryParameters:`,
`uriParameters:`, `baseUriParameters:` and `facets:` — all six are "properties
declarations" per the spec, and all six therefore get `?` handling and inline
type declarations for free.

### 5.1 Pattern properties

A property key of the form `/regex/` (including the empty `//`) is a pattern
property. Rules:

- The regex is compiled at parse time; a compile failure is a positioned error.
- Pattern properties may not be `required` — declaring `required:` or using `?`
  on one is an error.
- Explicitly declared properties win over any pattern that also matches.
- Among patterns, **the first declared match wins** — which is why
  `pattern_properties` must be an ordered mapping (free in Python).
- `additionalProperties: false` together with pattern properties is rejected, per
  spec § Additional Properties. (JSON Schema would allow it; the spec does not.)

## 6. Examples

```python
class Example:
    __slots__ = (
        "id",
        "name",
        "display_name",
        "description",
        "strict",
        "data",
        "annotations",
        "location",
        "key_pos",
        "value_pos",
    )
```

Spec § Single Example allows two forms and they are ambiguous:

```yaml
example:                # form A: the example IS this object
  name: Bob
example:                # form B: a wrapper with metadata
  value: {name: Bob}
  strict: false
  (pii): true
```

Disambiguation rule (from go-raml, and the only workable one): if the value is a
mapping **containing a `value` key**, treat it as form B; otherwise form A. A
type that genuinely has a property called `value` must therefore use form B
explicitly — which is exactly what the spec's own example does and comments on
("needs to be declared since instance contains a 'value' property").

`examples:` (plural) is a mapping of name → single example, or an `!include` of a
`NamedExample` fragment. `example` and `examples` on the same declaration are
mutually exclusive.

## 7. Custom (user-defined) facets

`facets:` declares new facets that subtypes must/may provide. Its value is a
properties declaration, so it reuses `make_property` — including `?` optionality.

Two parse-time checks (spec § User-defined Facets):

- A facet name may not start with `(` — that would be ambiguous with an
  annotation.
- A facet name may not shadow a built-in facet of the shape's kind, nor a common
  facet. Two tables: `COMMON_FACETS` and `TYPE_SPECIFIC_FACETS[kind]`.

The value a subtype supplies lands in `base.custom_facets`; the check that every
required declared facet has a value, that no undeclared facet value exists, and
that each value validates against its declaration, happens in P10
([10](10-validation.md) § 4).

## 8. `xml:`

Parsed into a small record and retained; nothing consumes it in v1.

```python
class XmlSerialization:
    __slots__ = ("attribute", "wrapped", "name", "namespace", "prefix", "location", "position")
```

Unknown keys inside `xml:` are an error, so a typo is caught rather than silently
ignored.

## 9. Discriminator

Parsed on `ObjectShape`; checked in P10 rather than at decode time, because the
property it names may be inherited and therefore not visible until unwrap.
Checks (spec § Using Discriminator):

- the named property must exist after unwrap;
- its type must be scalar;
- if `discriminatorValue` is given explicitly it must validate against that
  property's type;
- neither facet may appear on a union type (checked at decode time — a union has
  no properties, so it can never become valid later);
- `discriminator` without any `properties` is an error.

`discriminatorValue` defaults to the type's name; the default is computed on read,
not materialised at parse time.
