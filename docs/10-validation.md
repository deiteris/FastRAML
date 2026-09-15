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
| union | recursive `check()` on every member; where the members discriminate uniformly, their `discriminatorValue` claims must be distinct ([05](05-type-model.md) § 9.1) |
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
facet`. Both halves were measured against go-raml, which walks the chain from
the first parent for the same reason.

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

**A union is checked like anything else**, and that is recent. A facet written
beside `type: A | B` used to arrive here in `custom_facets` for the same reason a
typo does, though it is not one — it is a real facet of the members that had no
kind to be decoded against — so the check was skipped and the constraint went
unenforced. P9 now hands each facet to the members instead
([07](07-resolution-and-inheritance.md) § 3.4), so what reaches this point on a
union is a facet with nowhere to go, and `unknown facet` is the right answer.

The member it could not be placed on is where the diagnostic lands, because the
distributed facet is decoded onto a subtype of that member — which is also what
lets a member's own `facets:` declaration cover it.

**A recursion marker is a stop, and is not checked here.** P9 builds one by
cloning the cycle's head and clearing `inherits` (docs/07 § 4), so the clone
still carries the head's `custom_facets` with nothing left in the chain to
declare them. Walked as though it were a declaration of its own, every one of
them came back `unknown facet` — a valid document rejected, once per path that
reached the cycle. `Book: Entity` supplying a facet `Entity` declares was
rejected the moment `Book` held a `Book[]`.

The rule is the one `RecursiveShape.check` already states for its own half: the
head is checked where it is declared, and by the time the walk arrives at the
marker it already has been. `_validate_commons` returns at a marker, so its
examples are not re-checked either, for the same reason.

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
| string | `str` | `minLength`, `maxLength`, `pattern` (a **search**, § 5.4) |
| integer | `int`, `Decimal`/`float` with integral value, `str` from a number-preserving decoder | `minimum`, `maximum`, `multipleOf`, `format` range |
| number | `int`, `float`, `Decimal` | `minimum`, `maximum`, `multipleOf` |
| date-only | `str` matching `YYYY-MM-DD` | strict parse |
| time-only | `str` matching `hh:mm:ss[.f…]` | strict parse |
| datetime-only | `str` matching `YYYY-MM-DDThh:mm:ss[.f…]` | strict parse, no offset |
| datetime | `str` | RFC 3339 by default; RFC 2616 when `format: rfc2616` |
| file | `str` (base64) or `bytes` | `minLength`/`maxLength` in **bytes**, `fileTypes` |
| array | `list` | `minItems`, `maxItems`, `items` per element, `uniqueItems` |
| object | `dict` | required properties present; each declared property; `additionalProperties`; pattern properties; `minProperties`/`maxProperties` |
| union | anything | dispatch on the discriminator where every member declares the same one ([05](05-type-model.md) § 9.1); otherwise the first member that validates wins, and if none does, report *every* member's failure as attached detail |
| json | anything | delegate to the compiled JSON Schema validator |
| recursive | anything | delegate to `head` |

`bool` is a subclass of `int` in Python, so integer/number validation **must**
reject `bool` explicitly (`type(v) is bool`). A missed check here means
`True` validates as `1` against `type: integer`. There is a test for it.

### 5.1 Object validation order

1. missing required properties — reported as one message listing all of them;
2. declared properties present in the data, **in declaration order** (so the
   first error is deterministic and matches the document);
3. everything else in the data: pattern properties in declaration order, first
   match validates. If patterns are declared and none matches → error, because
   declaring any makes the set exhaustive ([05](05-type-model.md) § 5.1). If none
   are declared, `additionalProperties: false` → error and `true` → accepted.

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
every case that matters; go-raml converts through
`big.Rat.SetString(fmt.Sprintf("%v", v))` for the same reason.

### 5.4 `pattern` is a search; the author writes the anchors

A `pattern:` facet is `re.search`, not `re.fullmatch`. The spec's definition is
one line — "Regular expression that this string MUST match" — and says nothing
about anchoring. What settles it is that the spec **writes the anchors itself**
wherever it means anchored: `^.+@.+\..+$`, `^\d+\-\w+$`, `^\w{16}$`. Under a
full match every one of those is noise, written three separate times.

go-raml agrees: `regexp.Compile` on the raw pattern and
`MatchString`, which is Go's unanchored search.

This section said the opposite until it was rechecked, resting on one TCK
fixture: `Annotations/complex-11`'s pair differs only in
`simpleAnnotationValueOnType` versus `simpleAnnotation_value_on_type` under
`pattern: "[a-zA-Z0-9]{8,32}"`, and a search accepts both because the first
sixteen characters match. But go-raml fails that fixture too — measured, both
files parse clean — so it encodes an assumption its author never checked rather
than a rule any implementation follows. The fixture's pattern is unanchored
where it means anchored, and it is **fixed in the suite**
(`^[a-zA-Z0-9]{8,32}$`), which makes the pair discriminate under either reading.

`/regex/` *property names* were always a search and are unchanged: those match
against a key rather than describing one, and `/^x/` is how an anchored one is
written ([05](05-type-model.md) § 5.1).

**`/regex/` property names stay unanchored** ([05](05-type-model.md) § 5.1). The
two are different jobs: a `pattern:` facet *describes* a value, while a pattern
property is matched *against* a key it does not own, and `/^x/` is how those are
written in practice. An author-written `^…$` is redundant under a full match
rather than wrong, which is what keeps the change compatible with real documents.

## 6. External JSON Schema

An external JSON Schema becomes a `JsonShape`, in `types/jsonschema_.py`:

```python
class JsonShape:
    __slots__ = ("base", "raw", "validator", "_compiled", "_cached_shape", "_cached_defs")
```

`_compiled` is a `CompiledSchema` — the validator, the schema document and the
`referencing` resolver rooted at it. The validator alone is enough for
`validate()`, but § 6.3 walks the document and follows its `$ref`s. The compiled
container stays private; `JsonShape.contents` exposes its already-decoded
document read-only to syntax-aware views so they never parse `raw` again.

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
- **Nesting is measured before anything walks the schema.** One iterative pass
  over the decoded document, against `Raml.max_depth`, reporting
  `JSON schema nesting too deep`. It has to happen first because the deepest
  recursion over a schema is not ours: the schema library's own meta-schema
  validation exhausts CPython's stack at around 200 levels and surfaces as a
  raw `RecursionError`, which [12](12-performance.md) § 14 forbids. Checking
  once at decode makes that, the eager `$ref` walk and the § 6.3 projection all
  safe. A `$ref` target is decoded through the same path, so a shallow schema
  cannot reach the stack by pointing at a deep one.
- Draft is taken from `$schema`; absent, the default draft is 7 (matching the
  go-raml's meta-schema validation). Unlike go-raml, which
  validates every schema against the draft-07 meta-schema whatever it declares,
  the schema is checked against **its own** draft's meta-schema — so a draft-04
  document may write `exclusiveMinimum: true` and a draft-07 one may not.

### 6.2 Restrictions

Spec § Using XML and JSON Schemas states three things. fastRAML enforces the first
two and deliberately not the third ([01](01-scope-and-coverage.md) § 4, D11).

**Enforced.** A schema type carries no sibling facets, and does not inherit:

- `JsonShape.decode_facets(pairs)` errors if any sibling facet is present, except
  the wrapper facets the spec explicitly allows: `displayName`, `description`,
  annotations, `example`/`examples`;
- `JsonShape.inherit(source)` errors unless the source carries the identical raw
  schema — the spec's own "SHALL NOT define sub-types to declare new properties,
  add restrictions, set facets", and go-raml's behaviour exactly. Inheritance is
  the one case that asks for something a compiled schema cannot supply: merging
  a RAML facet into it. There is no such operation.

**Not enforced.** The spec also bars a schema type from "effectively any type
expression" and from "any declaration of query parameters, query string, URI
parameters, and headers". fastRAML allows both. A `JsonShape` is a container for a
compiled schema exposing `validate(value)`; at every one of those sites that is
the only thing asked of it, and validation is delegated. A union is a list of
types to validate against and the union itself is only an entry point; an array
item and a property are the same story. `check_parameter_schemas` used to hold
the parameter half and is gone, along with `_reject_schema_operand` in
`types/resolve.py`.

An earlier draft of this section claimed the expression rule "fails when the
array's item inherit runs". It never did: an item written as a bare reference is
an *alias*, not a subtype ([06](06-type-expressions.md) § 3.1), so nothing merged
and nothing failed. The rule only ever existed as an explicit check, which is
part of why removing it changes no behaviour beyond permitting the construct.

Inner-element references (`!include elements.json#/definitions/Foo`) are handled
by the JSON Pointer fragment of the URI, resolved by the schema library.

### 6.2a The schema as one self-contained document

`JsonShape.as_schema()` is the compiled document with every reference *out* of
it pulled in under `definitions` and rewritten to a local pointer. Cached on
first call, like the projection.

A consumer of a view holds one document and not the directory the schema was
written in, so `$ref: "money.json#/definitions/Amount"` names something it has
no way to reach. Resolution is already done — `compile` reads every target
through the `ResourceLoader` and `_prefetch` walks them all — so this is a
second reading of what the registry holds, not a second fetch.

A pointer *within* the document stays a pointer. It is followable where it
stands, inlining it discards the sharing the author expressed, and
`#/definitions/node` inside `node` has no finite expansion. A pointer inside a
pulled-in file is rewritten, because it is a pointer into that file and the
result is not that file: left alone it names whatever the bundle has at the
same path.

Names come from the pointer's last segment, and a collision with a name the
document already uses takes a suffix rather than the existing entry.

### 6.3 Projection to a RAML shape

`JsonShape.as_shape()` lazily converts a compiled JSON Schema into the nearest
RAML shape, for consumers that want a uniform model. Cached on first call.

**It survives unwrap, and that took a fix.** `_narrow_json` in `types/inherit.py`
carried a hand-written list of fields into the subtype — `raw` and `validator`,
but not `_compiled`, which is what this reads — so after P9 it returned `None` on
every *declared* schema type, which is precisely the shape a consumer is handed.
Nothing noticed until the effective view ([16](16-graph.md) § 9.7) became the
first caller to want the projection rather than the validator. It now copies
`copyable_slots(type(shape))`, the same mechanism `clone` and `alias_to` use so
that a field added to a kind cannot be missed — the hand list *was* the defect.

`projected(base)` beside it is the substitution a consumer walking structure
wants: a schema type through this projection, anything else unchanged. One
function rather than the same `isinstance` in every consumer, because a consumer
that forgets it does not fail — it sees a leaf with no properties and reports
that a schema type is made of nothing.

**A subschema with no `type` is projected by the kind its keywords imply. That
is a decision this projection makes, not JSON Schema semantics.**

In JSON Schema a keyword is an assertion applied *conditionally on the instance
type*. `{"properties": {…}, "required": ["a"]}` does not say the instance is an
object; it says that if the instance is an object, it must have an `a` property.
Against the string `"hello"` the same schema passes, vacuously. Validation here
is unaffected, because it runs against the compiled schema.

The projection still has to choose a kind, because RAML cannot express "a
constraint that applies only to objects and is otherwise silent". When every
keyword present points at one kind, that kind is the least lossy choice. When
they point at more than one, the shape stays `any` rather than choose silently —
`{"properties": {…}, "minLength": 3}` constrains objects *and* strings, which is
legal, and losing constraints is better than claiming the wrong kind.

The keyword table is **not** RAML's `FACET_TYPE_HINT`, which is wrong here in
three ways:

- it maps `fileTypes` and `discriminator`, which are not JSON Schema keywords;
- it omits `patternProperties`, `required`, `dependencies`, `contains` and
  `exclusiveMinimum`, which are;
- its `identify_shape_type` *raises* when two kinds are hinted, so reusing it
  would reject valid schemas.

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
