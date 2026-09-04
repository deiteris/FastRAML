# 10 — Validation

Two different questions, two different methods, often confused:

| Method | Question | Runs on |
|--------|----------|---------|
| `shape.check()` | Is this **declaration** self-consistent? | the declaration |
| `shape.validate(value)` | Does this **data** conform to the declaration? | user data |

`check()` catches `minLength: 10, maxLength: 5`. `validate()` catches
`example: "abc"` against `minLength: 10`. P10 runs both.

## 1. The validation pass

```python
def validate_shapes(self) -> None:
    unwrap_cache: dict[int, BaseShape] = {}
    acc = Accumulator()
    acc.add(self._validate_types(unwrap_cache))
    acc.add(self._validate_domain_extensions(unwrap_cache))
    acc.raise_if_any()
```

```python
def _validate_types(self, cache):
    for location, shapes in self.fragment_typedefs.items():
        for shape in shapes:
            shape = self._ensure_unwrapped(shape, cache)  # clone if needed
            shape.check()
            self._validate_commons(shape)
```

`_ensure_unwrapped` is the reason `validate=True` works without `unwrap=True`:
if the shape is not already unwrapped it is `clone_detached()`-ed, the *copy* is
unwrapped and recursion-marked, and the copy is cached by the original's id. The
declared model the caller sees stays un-flattened.

That costs one deep copy per declared type. The public docs therefore recommend
passing both options together when the un-flattened view is not needed — the same
advice go-raml gives.

`_validate_commons(shape)` recurses through `properties`, `patternProperties`,
`items`, `anyOf` and custom facet declarations, and at each level runs facet
validation (§4) and example/default validation (§3).

## 2. `check()` — declaration consistency

Per kind:

Every length and count facet — `minLength`, `maxLength`, `minItems`, `maxItems`,
`minProperties`, `maxProperties` — must be **non-negative**, which the spec
states per facet as "Value MUST be equal to or greater than 0". A negative bound
is worse than unsatisfiable: `minLength: -2` accepts every string while reading
as though it constrains something.

| Kind | Checks |
|------|--------|
| string | `minLength ≤ maxLength` |
| number/integer | `minimum ≤ maximum`; `format` in the kind's table (deviation D2); `multipleOf ≠ 0` |
| array | `minItems ≤ maxItems`; recursive `check()` on `items` |
| object | `minProperties ≤ maxProperties`; recursive `check()` on every property and pattern property; pattern properties forbidden with `additionalProperties: false`; discriminator rules ([05](05-type-model.md) § 9) |
| union | recursive `check()` on every member |
| file | `minLength ≤ maxLength`; `fileTypes` entries are media types or `*/*` |
| unknown | always fails — reaching it means P7 was skipped |

`BaseShape.check()` additionally validates **every `enum` member against the
shape itself**, so `type: integer, enum: [1, "two"]` fails at the declaration,
not at first use.

## 3. Examples, defaults, enums

For every shape:

- `example` — validated unless `strict: false`;
- each entry of `examples` — same rule, per entry;
- `default` — always validated (there is no `strict` for defaults).

Failures are positioned at the example/default value, and the nested path is
reported: `validate property $.address.zip: value must be one of (…)`.

The `$`-rooted path is threaded through validation as a plain string
(`ctx_path + "." + key`, `ctx_path + "[3]"`), constructed eagerly on the way
down. For very large example documents this string building is the dominant
allocation in P10.

The alternative is to carry the path as a list of segments and join it only when
an error is raised. That change is deferred until a benchmark justifies it; this
paragraph records the trade-off so it can be revisited with data.

## 4. Custom facet validation

Walk the inheritance chain collecting `facets:` declarations — **starting at
`inherits[0]`, not at the shape itself**. A `facets:` block declares what
*subtypes* must supply, so the declaring type neither has to satisfy its own
required facets nor may supply a value for one; supplying one is `unknown
facet`. Both halves were measured against the reference implementation, whose
`validateShapeFacets` walks from `base.Inherits[0]`.

Then:

- a facet name declared twice in one chain → `duplicate custom facet`;
- a declared facet that is `required` and has no value on the shape →
  `required custom facet is missing`;
- a supplied value → validated against its declaration;
- a supplied value with **no** declaration anywhere in the chain →
  `unknown facet`.

That last rule is what turns a typo (`maxLenght: 5`) into an error: unrecognised
facet keys become custom facet *values* during decoding
([05](05-type-model.md) § 4), and this is where they are caught.

**Skipped on a union base.** A facet written beside `type: A | B` arrives in
`custom_facets` for the same reason a typo does, but it is not one — it is a
real facet of the members that had no kind to be decoded against. Reporting
`unknown facet` there would reject what the spec allows, so the check is
skipped and the constraint goes unenforced. Tracked as a v1.1 conformance item
([01](01-scope-and-coverage.md) § 3.7, [07](07-resolution-and-inheritance.md)
§ 3.4).

Known limitation, inherited: the chain walk follows `inherits[0]` only, so a facet
declared on the second parent of a multiply-inheriting type is not seen. Fixing it
means walking all parents with a visited set; it is a small change, tracked as a
v1.1 item, and the current behaviour is pinned by a test so the fix is visible.

## 5. Instance validation

`shape.validate(value)` is the public API's data-validation entry point
(`base.validate_at(value, "$")` internally).

Order: **enum first**. If the shape has an `enum`, membership in it is the whole
check — the enum values were already validated against the shape by `check()`,
so re-running facet validation would be redundant.

Per kind:

| Kind | Accepts | Checks |
|------|---------|--------|
| any | anything | — |
| nil | `None` | — |
| boolean | `bool` | — |
| string | `str` | `minLength`, `maxLength`, `pattern` |
| integer | `int`, `Decimal`/`float` with integral value, `str` from a number-preserving decoder | `minimum`, `maximum`, `multipleOf`, `format` range |
| number | `int`, `float`, `Decimal` | `minimum`, `maximum`, `multipleOf` |
| date-only | `str` matching `YYYY-MM-DD` | strict parse |
| time-only | `str` matching `hh:mm:ss[.f…]` | strict parse |
| datetime-only | `str` matching `YYYY-MM-DDThh:mm:ss[.f…]` | strict parse, no offset |
| datetime | `str` | RFC 3339 by default; RFC 2616 when `format: rfc2616` |
| file | `str` (base64) or `bytes` | `minLength`/`maxLength` in **bytes**, `fileTypes` |
| array | `list` | `minItems`, `maxItems`, `items` per element, `uniqueItems` |
| object | `dict` | required properties present; each declared property; `additionalProperties`; pattern properties; `minProperties`/`maxProperties` |
| union | anything | first member that validates wins; if none, report *every* member's failure as attached detail |
| json | anything | delegate to the compiled JSON Schema validator |
| recursive | anything | delegate to `head` |

`bool` is a subclass of `int` in Python, so integer/number validation **must**
reject `bool` explicitly (`type(v) is bool`). A missed check here means
`True` validates as `1` against `type: integer`. There is a test for it.

### 5.1 Object validation order

1. missing required properties — reported as one message listing all of them;
2. declared properties present in the data, **in declaration order** (so the
   first error is deterministic and matches the document);
3. everything else in the data: if `additionalProperties: false` → error; else try
   pattern properties in declaration order, first match validates.

### 5.2 `uniqueItems`

Duplicate detection uses semantic equality: `1` and `1.0` are the same item;
`{"a":1}` equals `{"a":1.0}`. Two strategies by size, as in go-raml:

- ≤ 20 items → pairwise comparison, no allocation;
- more → hash into buckets with a type-tagged, key-sorted hash, resolving
  collisions by full comparison so there are no false positives.

The hash must be **order-independent for mappings** (sort keys) and
**order-dependent for sequences**.

### 5.3 Numeric comparison

Never compare a decoded `float` to a `Fraction` bound via `float()`. Convert the
value **through its decimal text, never through its binary value**: `int` stays
`int`; `float` becomes `Fraction(repr(value))`; `Decimal`/numeric string becomes
`Fraction(text)`. Fast path: if both bound and value are `int`, compare directly.

`multipleOf`: `Fraction(value) / multiple_of` must have denominator 1.

`as_integer_ratio()` is the wrong conversion here and was specified in an earlier
draft of this section. A bound is built from the raw scalar text, so
`multipleOf: 1.1` is exactly `11/10`; a value arrives as a `float` because the
YAML decoder made one, and its exact binary ratio is
`2476979795053773/1125899906842624`. Those never divide evenly, so `2.2` would be
rejected — the precise failure the no-`float` rule exists to prevent. `repr`
recovers the shortest decimal that round-trips, which is the author's text in
every case that matters; the reference implementation converts through
`big.Rat.SetString(fmt.Sprintf("%v", v))` for the same reason.

## 6. External JSON Schema

An external JSON Schema becomes a `JsonShape`, in `types/jsonschema_.py`:

```python
class JsonShape:
    __slots__ = ("base", "raw", "validator", "_compiled", "_cached_shape", "_cached_defs")
```

`_compiled` is a `CompiledSchema` — the validator, the schema document and the
`referencing` resolver rooted at it. The validator alone is enough for
`validate()`, but § 6.3 walks the document and follows its `$ref`s, and the
library keeps both of those behind private attributes.

### 6.1 Compilation

A schema is compiled **where it is declared**, in `JsonShape.__init__`, not at
`check()`. Malformed JSON in a `type:` is a syntax error in the document, and
reporting it only under `validate=True` would let a broken schema through the
default parse. `check()` therefore has nothing left to do.

- One **shared registry** per `Raml` instance — `SchemaRegistry`, built on first
  use because `registry.py` imports nothing from `types/` at runtime. A `$ref`
  target used by 40 schemas is read and parsed once. This is
  `jsonSchemaCompiler` in go-raml and it is the difference between linear and
  quadratic on a schema-heavy project.

  The memo has to live on `SchemaRegistry` rather than in `referencing`:
  `Registry` is a persistent structure whose `get_or_retrieve` returns a *new*
  registry holding the retrieved resource, so a cache kept there is discarded
  with the copy that made it.
- The schema is registered under the **RAML file's URI**, so relative `$ref`s
  resolve against the file containing the inline schema. Each compilation gets
  its own `Registry` rooted at that URI, so two inline schemas in one file do not
  collide. go-raml registers both into one shared compiler under the same URI and
  reuses the first for the second; that is `AddResource` returning
  `ResourceExistsError`, whose comment — "the cached entry is identical" — holds
  only for the external-file case it was written for.
- `$ref` resolution goes through the same `ResourceLoader` as everything else, so
  the workspace sandbox and the remote-includes switch apply. A `$ref` to
  `http://json-schema.org/...` in an offline parse fails loudly rather than
  silently reaching the network.
- **References are resolved eagerly**, by walking the schema at compile time. The
  Python library resolves lazily, so a reference to a missing file in a type
  nothing validates against would never be reported; go-raml's compiler is eager
  and the TCK expects that. The walk skips `const`, `default`, `enum`, `example`
  and `examples`, whose values are user data — a `$ref` written inside a
  `default` is a value that happens to look like a reference — and treats
  `properties`, `patternProperties`, `definitions`, `$defs` and the two
  `dependencies` keywords as maps *of* schemas rather than as schemas.
- Draft is taken from `$schema`; absent, the default draft is 7 (matching the
  reference implementation's meta-schema validation). Unlike go-raml, which
  validates every schema against the draft-07 meta-schema whatever it declares,
  the schema is checked against **its own** draft's meta-schema — so a draft-04
  document may write `exclusiveMinimum: true` and a draft-07 one may not.

### 6.2 Restrictions

Spec § Using XML and JSON Schemas: a type that defines an external schema "MUST
NOT participate in type inheritance or specialization, or effectively in any type
expression". Enforced:

- `JsonShape.decode_facets(pairs)` errors if any sibling facet is present, except
  the wrapper facets the spec explicitly allows: `displayName`, `description`,
  annotations, `example`/`examples`;
- `JsonShape.inherit(source)` errors unless the source carries the identical raw
  schema;
- a JSON-schema-typed name used in an expression (`Person[]`, `Person?`,
  `Person | string`) is refused by P7's visitor, at the operand. An earlier draft
  of this section said it "fails when the array's item inherit runs", which it
  does not: an item written as a bare reference is an *alias*, not a subtype
  ([06](06-type-expressions.md) § 3.1), so nothing merges and nothing failed.
  A bare reference on its own stays legal — it is another name for the same
  type, not an expression.

Spec also forbids XML/JSON schemas "in any declaration of query parameters, query
string, URI parameters, and headers". `baseUriParameters` are URI parameters and
are covered by the same rule.

Enforced by `check_parameter_schemas` in `parser/endpoint_build.py`, run after
P7 — **not** at the four decoders, as an earlier draft of
[15](15-implementation-plan.md) said. A parameter may *name* a JSON-schema type
rather than declare one inline, and a name is not bound to a kind until P7. By
then the four declarations are four fields of a model that is already built, so
the rule has one home rather than four; the module already holds the other
parameter-only rule, `_check_slash_free`.

Inner-element references (`!include elements.json#/definitions/Foo`) are handled
by the JSON Pointer fragment of the URI, resolved by the schema library.

### 6.3 Projection to a RAML shape

`JsonShape.as_shape()` lazily converts a compiled JSON Schema into the nearest
RAML shape, for consumers that want a uniform model. Cached on first call.
Mappings:

| JSON Schema | RAML |
|-------------|------|
| `type: object` (+ properties, required, patternProperties, min/maxProperties, boolean `additionalProperties`) | `ObjectShape` |
| `type: array` (+ items, min/maxItems, uniqueItems) | `ArrayShape` |
| `type: string/integer/number/boolean/null` | corresponding scalar (+ min/maxLength, pattern, min/maximum, multipleOf) |
| `type: [a, b]` | union of bare members |
| `anyOf` / `oneOf` | `UnionShape` (`oneOf`'s exactly-one semantics is lost — documented) |
| `allOf` | sequential `inherit` merge |
| `$ref` to a named definition | a named shape registered in `as_shape_defs()` |
| cyclic `$ref` | `RecursiveShape` |
| `if`/`then`/`else` | error — no RAML equivalent |
| schema-form `additionalProperties` | error |
| tuple-form `items` | error |
| `false` schema | error |
| `true` schema, or no `type` and no combinator | `AnyShape` |

`title`, `description`, `default`, `enum` and `examples` map onto the common
facets of whatever shape the row above produced. A numeric bound goes through
`Fraction(repr(value))`, not through `float` — `json` has already made a binary
approximation of `1.1` by the time it is read (§ 5.3).

The shapes produced here are *view* objects: they are not registered in
`Raml.shapes` or in `Raml.fragment_typedefs`, they carry no positions, and they
are marked unwrapped. They must never be fed back into the parser's own passes —
the model looks right until P9 tries to flatten it. (go-raml also skips three
always-empty ordered maps here; in Python those are plain dicts and there is
nothing to skip.)

A `patternProperties` regex that this engine cannot compile is an error, because
the key would be lost; a `pattern` on a string that it cannot compile is dropped
instead, because the view is a view and `validate()` still enforces the schema.
