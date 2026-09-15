# Phase 8a brief — declaration and instance validation (P10)

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Why this phase runs before Phases 5 to 7

`docs/15-implementation-plan.md` orders validation last because it validates
everything else. That is true of its *coverage*, not of its dependencies. P10
iterates `Raml.fragment_typedefs`, which the endpoint decoders will register
into through the same helpers `types:` already uses — so endpoints widen the
validator's input without changing its shape.

The corpus says the rest. Of 336 fixtures the ratchet records as failing, **325
are `invalid` fixtures the parser does not reject** and only 11 are valid
fixtures it wrongly rejects. 133 of those 325 declare no resource and no
template, so they are reachable with nothing but this phase:

| Category | Reachable now |
|---|---|
| `Types/` | 81 |
| `EdgeCases/` | 18 |
| `Annotations/` | 16 |
| `Fragments/` | 6 |
| `Examples/` | 5 |
| `Root/`, `Libraries/` | 7 |

Validation is also on the critical path *for* Phases 5–7: most of the invalid
fixtures in `Resources/`, `Methods/` and `Responses/` are wrong about a type,
not about an endpoint, and cannot fail until something checks types.

### 1.1 Where the project stands

Master is at the Phase 4b merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastraml/ && uv run pytest -q
# 1837 passed, 42 skipped
```

TCK: 595 of 930, ratchet clean. **P0 through P9 all run; P10 is the only
no-op.**

| Module | What you will use from it |
|---|---|
| `fastraml.types.base` | `BaseShape` (28 slots), `Property`, `PatternProperty`, `KindBase`, `Shape`, `ScalarFacet[T]` |
| `fastraml.types.scalars` | the eleven scalar kinds and their facet fields |
| `fastraml.types.complex_` | `ObjectShape`, `ArrayShape`, `UnionShape`, `JsonShape`, `RecursiveShape`, `UnknownShape` |
| `fastraml.types.unwrap` | `unwrap_shape(raml, base)` — what `_ensure_unwrapped` calls on a clone |
| `fastraml.datanode` | `DataNode`, `ValueNode`, `MappingEntry`, `SequenceItem` — positions at every depth |
| `fastraml.types.examples` | `Example` (`data`, `strict`), `Examples` (`values`, `link`) |
| `fastraml.parser.annotations` | `DomainExtension.defined_by`, `.target` — both filled by Phase 4b |
| `fastraml.errors` | `RamlError.new/.wrap`, `Accumulator`, `ErrorKind.VALIDATING` |

### 1.2 The seams you pick up

`grep -rn 'NotImplementedError' fastraml/types/` finds exactly two, both on
`KindBase`:

| Method | Doc |
|---|---|
| `check(self)` | 10 § 2 |
| `validate(self, value, path)` | 10 § 3 |

`ParseOptions.validate` is accepted and ignored; `entry.py`'s driver ends with
`# P10 — validate, when options.validate. Phase 8.`

`JsonShape.validator`, `_cached_shape` and `_cached_defs` are declared and never
written — those are Phase 8b's, not this phase's (§ 7).

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules. Three bear directly on this phase:
   **numeric facets never pass through `float`**; **accumulate, do not fail
   fast**; **no per-character Python loops**.
2. **`docs/10-validation.md`** — your whole specification, §§ 1–5. § 5.3
   (numeric comparison) and § 5.2 (`uniqueItems`) are the two that get
   implemented wrong; read them twice.
3. **`docs/05-type-model.md` § 9** — the discriminator rules, which are P10's
   even though the facets are decoded on `ObjectShape`.
4. **`docs/01-scope-and-coverage.md` D2** — the numeric `format` tables, which
   deviate from a literal reading of the spec on purpose.

Skim only: doc 13 § 5 (the public `validate()` returns rather than raises), doc
14 § 3's `validate` row and § 4's law 7, doc 12 § 12.

---

## 3. What to build

Doc 10 § 1's decomposition, in dependency order.

### 3.0 Settle the module layout first

Doc 02 § 2 lists `types/validate.py` as the home of "check() and validate()",
and doc 05's `Shape` protocol keeps both as *methods* — unlike `inherit` and
`alias_to`, which Phase 4 moved off it. Both are right, and the split is:

- **The per-kind rules are methods**, because dispatch is free and the recursion
  into `properties` / `items` / `anyOf` is each kind's own business. `KindBase`
  already stubs them.
- **The pass driver is `types/validate.py`**: `validate_shapes`,
  `_validate_types`, `_ensure_unwrapped`, and the annotation half.

**There is a cycle waiting here.** `_ensure_unwrapped` imports `types/unwrap.py`,
which imports `types/complex_.py`. So if the kinds import shared helpers *from
`validate.py`*, the graph closes. Put the shared machinery — numeric coercion
and comparison, `uniqueItems`, the `$`-path helpers, the date parsers — in a
**leaf module, `types/values.py`**, which imports only `base.py`. Both the kinds
and the driver import it and nothing imports them back.

Record whichever layout lands in doc 02 § 2's module list.

### 3.1 `check()` per kind (doc 10 § 2)

First: it needs no instance data and it catches the largest class of fixtures.
The table in § 2 is the specification, one row per kind, and each row wants a
test that names the rule.

`BaseShape.check()` does the base-level work before dispatching: **every `enum`
member is validated against the shape itself**, so `type: integer, enum: [1,
"two"]` fails at the declaration.

`UnknownShape.check()` always fails. Reaching it means P7 was skipped, and a
silent pass there would hide invariant I5 breaking.

The discriminator rules are doc 05 § 9, not doc 10, and they are checked here
rather than at decode time because the property named may be inherited.

### 3.2 Instance `validate()` per kind (doc 10 § 5)

**Order: enum first.** If the shape has an `enum`, membership is the whole
check — the members were already validated against the shape by `check()`, so
re-running facet validation is redundant.

Three things go wrong here and there is a test for each:

- **`bool` is a subclass of `int` in Python.** Integer and number validation
  must reject it with `type(v) is bool`, or `True` validates against
  `type: integer`.
- **Numeric comparison never goes through `float`** (§ 5.3). `int` stays `int`;
  `float` becomes `Fraction(*value.as_integer_ratio())`; `Decimal` or a numeric
  string becomes `Fraction(text)`. Fast path when both sides are `int`.
  `multipleOf`: `Fraction(value) / multiple_of` must have denominator 1.
- **`uniqueItems` uses semantic equality** (§ 5.2): `1` and `1.0` are the same
  item. Two strategies by size — pairwise at ≤ 20, hashed buckets above, with
  collisions resolved by full comparison so there are no false positives. The
  hash is order-independent for mappings and order-dependent for sequences.

Object validation order is § 5.1 and is observable: missing required properties
first as *one* message listing all of them, then declared properties in
declaration order, then everything else.

A union reports **every** member's failure when none matches; that is the
diagnostic users actually need.

`RecursiveShape.validate` delegates to `head`. Do not guard it with a visited
set — the *data* is finite even though the type is cyclic.

### 3.3 Examples, defaults and enums (doc 10 § 3)

On top of 3.1 and 3.2. `example` and each entry of `examples` are validated
unless `strict: false`; `default` is always validated, there being no `strict`
for defaults.

The `$`-rooted path is threaded as a plain string, built eagerly on the way
down. Doc 10 § 3 records that a segment list joined lazily is the faster shape
and that the change waits for a benchmark — leave that alone, and leave the
paragraph in place.

### 3.4 Custom facet validation (doc 10 § 4)

Walk the inheritance chain collecting `facets:` declarations, then the four
rules. The last one — a supplied value with no declaration anywhere is `unknown
facet` — is what turns `maxLenght: 5` into an error, because an unrecognised
facet key became a custom facet *value* during decoding (doc 05 § 4).

**Start the walk at `inherits[0]`, not at the shape.** A `facets:` block
declares what *subtypes* must supply: the declaring type neither has to satisfy
its own required facets nor may supply a value for one, and supplying one is
`unknown facet`. Both halves are measured — go-raml’s
`validateShapeFacets` walks from `base.Inherits[0]`. Getting this wrong regresses
fifteen valid fixtures and every one of them looks like a different bug.

**The chain walk follows `inherits[0]` only.** That is an inherited limitation,
it is documented in doc 10 § 4 as a v1.1 item, and it must be **pinned by a
test** so the eventual fix is visible rather than silent.

### 3.5 Annotations (doc 09 §§ B4–B5)

Phase 4b filled `defined_by` and `target`; this phase is the only consumer.

- Each `DomainExtension.value` validates against `defined_by`.
- If `defined_by.allowed_targets` is not `None` and `target` is not in it,
  `annotation not allowed at this target`, with both the site and the list.
  **`None` and `[]` differ**: absent allows any target, empty allows none.

Six of the seventeen `DomainLocation`s are reachable before Phase 5, and the
`Annotations/target-locations/*-used-in-api` fixtures are the ones this moves.

### 3.6 The driver (doc 10 § 1)

`validate_shapes(raml)` runs both halves under one `Accumulator`.

`_ensure_unwrapped` is why `validate=True` works without `unwrap=True`: an
un-unwrapped shape is `clone_detached()`-ed, the **copy** is unwrapped and
recursion-marked, and the copy is cached by the original's id. The declared
model the caller sees stays un-flattened. That costs one deep copy per declared
type, which is why doc 13 § 4 recommends passing both options together.

Wire it at `entry.py`'s P10 comment, under `if options.validate:`.

---

## 4. Decisions already settled — do not re-litigate

1. **`check()` and `validate()` are different questions.** `check()` asks
   whether the declaration is self-consistent (`minLength: 10, maxLength: 5`);
   `validate(value)` asks whether data conforms. Doc 10's opening table.
2. **The public `validate()` returns a `RamlError` or `None`; it does not
   raise** (doc 13 § 5). The internal `validate_at(value, path)` is what
   recurses. `validate_or_raise` is the other-case variant.
3. **Validation is opt-in** and implies unwrapping a private copy.
4. **Numeric facets never pass through `float`** — a `CLAUDE.md` rule, and
   `Fraction('0.3') != 0.3` is exactly why.
5. **Enum members are checked against their own shape at declaration time**, so
   instance validation can treat a non-empty `enum` as the entire check.
6. **The `inherits[0]`-only facet chain walk stays**, pinned by a test (§ 3.4).
7. **`allowedTargets` absent ≠ empty** (doc 09 § B5).

## 5. Definition of done

- Every row of doc 10 § 2's table has a `check()` test that names the rule.
- Every row of doc 10 § 5's table has a `validate()` test, plus the three
  named hazards: `True` is not an `integer`; `multipleOf: 1.1` is exact;
  `uniqueItems` behaves the same at n=20 and n=21 (doc 14 § 3).
- Object validation order is asserted, not just its outcome: missing-required
  before per-property, and declaration order within.
- A union with no matching member reports *every* member's failure.
- `strict: false` suppresses an example failure; `default` has no such escape.
- `maxLenght: 5` is `unknown facet`; the `inherits[0]`-only limitation has a
  test that says it is a limitation.
- An annotation applied where `allowedTargets` forbids it fails; one with
  `allowedTargets` absent is allowed anywhere; one with `[]` is allowed nowhere.
- `validate=True, unwrap=False` leaves the caller's model un-flattened — assert
  the declared shape still has its `inherits` afterwards. This is the property
  `_ensure_unwrapped` exists for and the easiest to lose.
- Hypothesis law 7 (doc 14 § 4): if `child` inherits `parent` and a value
  validates against `child`, it validates against `parent`.
- The full gate passes.

**Ratchet expectation: this is the biggest single move in the project, and the
first phase that can lose ground.** Up to ~133 invalid fixtures may start
failing correctly. But P10 only ever *adds* diagnostics, and 474 valid fixtures
currently pass — a validator that is too strict regresses them. **Read every
line of the ratchet diff.** A valid fixture moving to `fail` is a bug in this
phase, not progress, and it is the one thing `--update-ratchet` will happily
record as if it were fine.

Unit tests: `tests/unit/test_check.py` (declaration consistency) and
`tests/unit/test_validate.py` (instance validation, examples, facets,
annotations).

---

## 6. Scope boundary

Phase 8a is doc 10 §§ 1–5. It does **not** include:

- **§ 6, external JSON Schema** — the shared registry, `JsonShape` compilation
  and the schema → shape projection. That is Phase 8b; ~8 fixtures, and the only
  part with a third-party dependency (`jsonschema`, `referencing`, both already
  in `pyproject.toml`).

  `JsonShape.check()` and `.validate()` **accept everything** rather than
  raising `NotImplementedError`, which is what this brief first said. A raise is
  right for a seam nothing reaches; this one is reached by every valid document
  that declares a JSON-schema type, and raising would reject it. Deferring
  validation means not validating, not failing.
- **Doc 10 § 6.2's four decoders** — "no schema in query parameters, query
  string, URI parameters or headers". Three of the four do not exist until
  Phase 5.
- **The eleven unreachable `DomainLocation`s** — Phases 5–7 build those sites.
- Endpoints, templates, security schemes.

---

## 7. Working method

Branch: `git checkout -b phase-8a-validation`. One logical change per commit,
and this phase has natural ones: `check()`, then `validate()`, then examples,
then facets, then annotations, then the driver. **Commit the ratchet separately
from the code that moves it**, so the diff is readable.

If the code must diverge from a document, **amend the document in the same
commit**. Phase 3 owed five such amendments, Phase 4 four, and Phase 4b's design
changed outright on contact with `make_scalar_facet`'s call sites — every one of
those was a real defect in the plan rather than a formality.
