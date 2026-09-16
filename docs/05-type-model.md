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
        "allowed_targets",   # list[DomainLocation] | None — annotation types only
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
    def check(self) -> None: ...              # is the declaration self-consistent?
    def validate(self, value: Any, path: str) -> None: ...  # does data conform?
    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> Shape: ...
    def is_scalar(self) -> bool: ...
```

**`inherit` and `alias_to` are not on this protocol.** Earlier drafts put them
here, one method per kind, which is where go-raml has them. They live in
`types/inherit.py` as functions over two `BaseShape`s instead, for two reasons
that only appear once they are written:

- Merging is **mutually recursive across kinds**. Two objects merge by merging
  their like-named properties, and a property is a declaration of any kind at
  all — so a method on `ObjectShape` would have to call back into the
  base-level driver, which is the callback shape [02](02-architecture.md) § 2
  rejects.
- The union rules (§ 3.4 of [07](07-resolution-and-inheritance.md)) **construct
  a `UnionShape`** when a merge collapses several members, and `base.py` cannot
  import `complex_.py`.

[02](02-architecture.md) § 2 already listed `types/inherit.py` as the home of
the per-kind inheritance rules, so the module layout was right and only this
protocol listing was wrong. `clone` stays on the protocol: it recurses through
`BaseShape.clone`, never back through a driver, and constructs nothing it
cannot already see.

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

The kind objects do share one small base class, `KindBase`: the `base`
back-pointer, the five methods later phases fill, and the default
`decode_facets` that files an unrecognised key as a custom facet value. Two
subclasses of it, `ScalarKind` and `ComplexKind`, differ only in `is_scalar`.
This is a shared base for the kind objects, and it is not the
hierarchy rejected above: **no facet may live on it**, because a facet there
would be one no `BaseShape` knows about.

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
| `array` | `items`*, `minItems`, `maxItems`, `uniqueItems` | `BaseShape`, `ScalarFacet[int]`×2, `ScalarFacet[bool]` |
| `union` | `anyOf` | `list[BaseShape]` |
| `string` | `pattern`, `minLength`, `maxLength` | `ScalarFacet[Pattern]`, `ScalarFacet[int]`×2 |
| `number` | `minimum`, `maximum`, `multipleOf`, `format` | `ScalarFacet[Fraction]`×3, `ScalarFacet[str]` |
| `integer` | same four | `ScalarFacet[int]`×2, `ScalarFacet[Fraction]`, `ScalarFacet[str]` |
| `file` | `fileTypes`, `minLength`, `maxLength` | `list[ScalarFacet[str]]`, `ScalarFacet[int]`×2 |
| `datetime` | `format` (`rfc3339` \| `rfc2616`) | `ScalarFacet[str]` |
| `boolean`, `nil`, `any`, other dates | — | |

\* `patternProperties` is not a RAML facet name; it is where keys of the form
`/regex/` inside `properties:` are routed (spec § Additional Properties).

\* `items:` takes **a reference or an inline type declaration, never a bare
sequence**. `items: [Foo, Bar]` is rejected at decode time. The temptation is
real and the spec reads both ways: a sequence in a `type:` position *is*
multiple inheritance (§ Multiple Inheritance), and `items:` holds a type
declaration — so `[Foo, Bar]` looks like a composite. But the `items` facet is
defined as "a reference to an existing type or an inline type declaration", and
a sequence is neither. go-raml reads it the same way and
the TCK fixture is named `invalid`. The multiply-inheriting form remains
available one level in, as `items: {type: [Foo, Bar]}`, which is unambiguous.

```python
class Property:  # a named property, a header, a query parameter, a facet def
    __slots__ = ("name", "base", "required")


class PatternProperty:  # always optional by definition
    __slots__ = ("pattern", "base")  # pattern is a compiled regex


class Parameter:  # a *bound* property: a header, a query/URI parameter
    __slots__ = ("id", "binding", "declaration", "key_pos", "value_pos", "synthesized")
```

`Property` has no `id` and no position, and that is what makes it a record
rather than an entity (`docs/02` § 3.1). `Parameter` is the entity: see § 5.

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
point, and it lives in `types/shape.py` with the kind dispatch and
`make_property`. Every property, header, query parameter, body, URI parameter,
type declaration and inline declaration goes through it.

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

Before the leftover `facets` list reaches the concrete shape, `make_shape`
removes the keys that hold declarations — object `properties`, array `items`,
union `anyOf` — builds them, and passes them to the shape's constructor. Which
keys those are is a class-level table, `DECLARATION_FACETS`, on the three kinds
that have any; `types/` points one way, so a kind never calls back into
`shape.py` ([02](02-architecture.md) § 2). `properties:` fills two constructor
keywords, since a `/regex/` key inside it becomes a pattern property (§ 5.1).

What remains is handed to `decode_facets`. fastRAML keeps go-raml's
allocation-lean structure here: one pass, one list, no intermediate dict, and the
concrete shape sees only the keys it might handle.

Any key the concrete shape does not recognise becomes a **custom facet value** in
`base.custom_facets`. P10 validates those values against the `facets:`
declarations found in the inheritance chain.

**Two kinds keep the list as YAML instead — `pending_facets`.** Digesting a facet
into a `DataNode` discards the source node, and both of these need it later:

| Kind | Why it cannot digest now | Who consumes it |
|------|--------------------------|-----------------|
| `UnknownShape` | the kind is unknown, so *which* of these are facets at all is unknown | P7's `attach_kind` |
| `UnionShape` | the kind is known and recognises none of them — they belong to the members, and the members are not settled until the merge | P9's `_distribute_union_facets` ([07](07-resolution-and-inheritance.md) § 3.4) |

go-raml threads the same list through every shape and
stores it in exactly one place, its `UnknownShape`; the union case is the gap it
still has ([01](01-scope-and-coverage.md) § 3.7).

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

Four of the six are **bound**. A header, a query parameter, a URI parameter and
a base-URI parameter each say *where* they apply, and which of those a
declaration is belongs to the map that holds it, not to the type: the same
declared type is a required path parameter here and an optional header there. A
`Property` cannot record that, and does not try to — one class serves all six
precisely because it stays out of the question.

So `make_parameter_map` wraps each one in a `Parameter`, which adds the three
facts the property has nowhere to put: the binding, an `id`, and where the key
was written. `properties:` and `facets:` are not bound and stay bare `Property`
values. `baseUriParameters:` binds as `uri` — it declares the same thing about
the same template variables (`docs/08` § 8.2).

The binding is `uri` | `query` | `header`. The graph projection spells `uri` as
`path`, in the attribute and in the IRI segment; that is a vocabulary mapping
owned by `docs/16` § 3, not a second name for the model's.

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
- **Declaring any pattern makes the set of them exhaustive.** A key that matches
  no declared property and no pattern is refused, whatever `additionalProperties`
  says. The regex is matched **unanchored**, unlike the `pattern:` facet
  ([10](10-validation.md) § 5.4) — a pattern property is matched *against* a key
  rather than describing one, and `/^x/` is how they are written.

  That is not the reading `additionalProperties: true` suggests, and the
  go-raml does not do it. The spec's own examples decide it, in
  their own comments: `types-pattern-properties.raml` says pattern properties
  are "restricting the property names of any additional properties", and
  `additional-properties.raml` uses the empty pattern `//` to "force all
  additional properties to be a string" — which is only a thing you would need
  to write if the non-empty patterns restricted what is allowed.

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

In the included form the examples live on the fragment and `Examples.values` is
empty, so **read `Examples.entries()`, never `values` directly**. That is not a
convenience: a consumer reading `values` sees no examples at all, which is how
an included NamedExample went unvalidated by P10 until Phase 8b.

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

Parsed into a small record and retained; nothing consumes it in v1. It gets its
own module, `types/xml.py`: it is a leaf with no dependencies and no dependants,
and it has nothing to do with examples beyond both being facets of a declaration.

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
- `discriminator` without any `properties` is an error;
- neither facet may appear on an **inline** type declaration — anything that is
  not a named type in `types:`, `schemas:`, `annotationTypes:` or a DataType
  fragment's root.

**That last rule runs between P7 and P9, not with the others**, and the ordering
is the whole of it. A discriminator is *inherited*: a body written
`application/json: Person` against a discriminated `Person` carries one after
unwrap, and it is inline — so on the flattened model every correct document
reports as broken. On the declared model a discriminator is present only where it
was written. Decoding records only shapes that wrote either facet, and
`check_declared_discriminators` in `types/validate.py` checks that narrow list;
its cost is proportional to discriminator declarations, not to all types.
go-raml does not supply this check: it carries the rule as a `FIXME` ("need to
validate on which level the discriminator is applied to avoid potential false
positives"), its TCK case is commented out, and it enforces nothing.

`discriminatorValue` defaults to the type's name; the default is computed on read,
not materialised at parse time.

**A discriminator value in an example must name a type that exists**, and that
check runs **outside the `strict` gate**. `strict: false` waives conformance —
"this example deliberately does not validate" — and a value naming no type is a
different question: it is about the declaration graph, not about the instance.
The TCK's `EdgeCases/identifying-discriminator` pair turns on exactly this, its
two fixtures differing in one word with `strict: false` set in both.

The index is keyed by the **discriminator's name**, not by the parent shape.
After P9 a subtype carries its parent's discriminator but has no `inherits` edge
left to find the parent by, and the shape that needs the lookup is usually
anonymous — `type: Person[]` gives its items a nameless shape. One distinction is
lost: two unrelated hierarchies that both discriminate on `kind` share a set, so
an instance of one may borrow the other's value. That error is permissive and
never a false rejection, which is the right direction for a check no `strict`
can turn off.

### 9.1 Dispatch — a union that discriminates

Sections above concern *declarations*. A discriminator also answers a question
about **data**: given `Cat | Dog`, which member is this payload?

The spec makes the answer optional (`raml-10.md:762`):

> A RAML processor **MAY** provide an implementation that automatically selects a
> concrete type from a set of possible types, but a simpler alternative is to
> store a unique value associated with the type inside the object.

fastRAML provides one. `UnionShape` carries a `{discriminatorValue: member}` table
and validates by lookup. This is deviation **D12**
([01](01-scope-and-coverage.md)), because it narrows what the union accepts.

#### When a table exists

`_discriminated` answers the first question: does this union discriminate
uniformly? Four things make the answer no. Each means a linear scan is the only
correct treatment, and none is an error:

| Condition | Why |
|-----------|-----|
| two or more members | nothing to choose between otherwise |
| every member an `ObjectShape` carrying a `discriminator` | `Cat \| string` cannot be selected by a property |
| one discriminator name across all of them | a value would have to be looked up under several keys |
| one *type* under that name | a numeric property and a string one need different key functions, and a table has one |
| every member with something to be keyed by | see below |

Each member claims a value: its `discriminatorValue`, or its declared name, which
is the default the spec gives. A member with neither is anonymous and rules the
union out.

**Distinct claims are the fifth condition, and failing it is an error.** Spec
§ Type Declarations requires `discriminatorValue` to be "unique in the hierarchy
of the type". Within a union the requirement is also what makes the union usable:
where two members answer to `cat`, no payload can say which it is.
`UnionShape.check` reports `discriminator value is claimed by more than one
member of the union`, and no table is built. The collision is easy to write
unseen, because the default is silent:

```raml
A: { type: P }                         # claims "A", by default
B: { type: P, discriminatorValue: A }  # claims "A", explicitly
```

The check is scoped to the union, not to the hierarchy. After P9 a subtype has no
`inherits` edge back to the type that declared the discriminator, so the
hierarchy a value must be unique *in* is not walkable from the shape that needs
the answer; what is checked is the set of members a document wrote together. A
hierarchy-wide check needs the declared model, and
`check_declared_discriminators` is where it would go.

#### The key depends on the property's declared type

A tag is keyed by `_tag_key(value, numeric=...)`, and `numeric` is read off the
discriminator property rather than inferred from the value. The two cases pull in
opposite directions and both are real:

| Property | Accepts | So the key is |
|----------|---------|---------------|
| `integer` | `1`, `1.0`, `'1'`, `'1.0'` as one value ([10](10-validation.md) § 5, a number-preserving decoder may hand a numeric string through) | the `Fraction`, so all four select the member claiming `1` |
| `string` | `'1'` and `'1.0'` as two *different* strings | the string, so the two stay apart |

Keying by value alone cannot serve both. `same_value` reads `'1'` as the number
`1`, so using it would merge two legal string claims and report the union as a
collision — a false rejection on a valid document. Keying by spelling alone fails
the other row, leaving `discriminatorValue: 1` selectable by one spelling of the
number and not the others.

Nothing crosses between the rows within one member: `_check_discriminator`
validates `discriminatorValue` against the property, so a `string` discriminator
refuses `discriminatorValue: 1` and a `number` one refuses `discriminatorValue:
"1"`. Across members it is the fourth table condition that holds the line — two
types under one discriminator name mean two key functions, and the union falls
back to the scan rather than pick one.

`true` and `false` key ahead of the numeric branch, because Python makes `bool` a
subclass of `int` and no RAML author means `true` and `1` to select one type.

#### The member's name is one alias hop away

`type: Cat | Dog` gives members that are aliases of `Cat` and `Dog`. An alias
shares its referent's containers, so the *discriminator* is visible on the member
and the *name* is not ([07](07-resolution-and-inheritance.md) § 3.6).

One hop reaches the name and no further hop is needed: `alias_to` points an alias
at a declaration, and a declaration is named. Given `Moggy: Feline` and
`Feline: Cat`, the member written `Moggy` resolves to `Moggy` — the name the
union was written with, which is the name `discriminatorValue` defaults from.

#### The table is built at the end of P9

`finish_unwrap` builds it, from a collector its walk fills. Three properties of
that placement matter:

- **It is the last pass that writes `any_of`.** Recursion marking substitutes a
  `RecursiveShape` for a cycle's head, so a table built during `_unwrap` would
  name members the union no longer has.
- **Its walk already visits every union**, so the collector costs a list append
  rather than a second traversal.
- **It runs on both unwrap paths** — `unwrap_shapes` for the registry, and the
  private copy `_ensure_unwrapped` makes for `validate=True` without
  `unwrap=True` — so a shape that can be validated has a table.

Settling it here rather than on first validation keeps the field out of the hot
path and makes its correctness a property of pass order rather than of nothing
having touched `any_of` in between.

#### What dispatch does at validation time

`UnionShape._select` decides, and returns the member or `None` for "fall back".

| The value | What happens |
|-----------|--------------|
| no table | linear scan |
| not a mapping | linear scan; it cannot carry a tag, and every member is an object, so the combined `invalid type` is the answer |
| tag absent, null, or not a scalar | linear scan — whether the property was required, and what type it had to be, are the *members'* rules, and they state them precisely where a dispatch failure could only say the lookup missed |
| tag present and scalar, in the table | validate against **that member only**, and report its failure as its own |
| tag present and scalar, not in the table | `unknown discriminator value`, carrying the path, the property, the tag and the known values in `info` |

Row three is what the feature buys: a failure inside the intended member is
reported as that member's failure, not as `value matches no member of the union`
with every member's complaint attached.

Row four is the narrowing. `{kind: Dog, meows: true}` against `Cat | Dog`
satisfies `Cat`, which has no required property `Dog` lacks, so a linear scan
accepts it. An author who writes a discriminator means the tag to identify the
type, so dispatch refuses it — and that refusal is why D12 is a deviation rather
than an optimisation.
