# Phase 8b brief — JSON Schema, and the last of validation

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

---

## 0. What this brief got wrong

Kept for the next brief's author. The phase is complete;
[15](../15-implementation-plan.md) § Phase 8b holds the outcome.

1. **The three-way split of the 25 open fixtures was wrong.** JSON Schema
   accounted for 14, not 10, and the row called "type-system corners:
   discriminator, pattern-property characters, constraint conflicts,
   `Root/baseuri`, `Methods/protocols-array`" was eight unrelated conformance
   rules, most of them a facet value nobody had checked — a method's
   `protocols:`, a `baseUri` that was never parsed as a URI template, a YAML
   local tag that was not `!include`. Tracing each one, as § 1 said to, was the
   right instruction; the grouping around it was noise.
2. **`allowedTargets` was already enforced.** Phase 8a built `_check_target` in
   `types/validate.py`, reading all seventeen `DomainLocation`s. The two fixtures
   § 3.4 was written for were passing before the phase opened. § 1.1's "decoded
   in Phase 4b, never read" was stale.
3. **"No schema in query parameters … at those four decoders" is the wrong
   home.** A parameter may *name* a JSON-schema type rather than declare one
   inline, and a name is not bound to a kind until P7. The check runs after it,
   over the built endpoint model.
4. **"A JSON-schema-typed name in a type expression fails when the array's item
   inherit runs" is not true.** An item written as a bare reference is an
   *alias*, so nothing merges and nothing fails. P7's visitor refuses the operand
   directly.
5. **The ratchet moved in both directions, as § 6 predicted — but not from
   compilation.** The one valid fixture lost was
   `Fragments/namedexample-01/valid.raml`, and it broke because validating an
   included NamedExample for the first time exposed a defect in the union merge
   two layers away.

This is the last phase before hardening, and it is three unrelated jobs sharing
a number: compiling external JSON Schema, the four places a schema is forbidden,
and enforcing `allowedTargets`. Only the first is large.

---

## 1. Where the project stands

Master is at the Phase 7 merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 2153 passed, 54 skipped
```

TCK: **891 of 916**, ratchet clean. Every RAML construct is decoded; there are
no `_raw_*` seams left. All 25 remaining failures are `invalid-` fixtures:

| What is missing | Fixtures |
|---|---|
| JSON Schema compilation and validation | 10 |
| `allowedTargets` enforcement | 2 |
| Type-system corners: discriminator, pattern-property characters, constraint conflicts, `Root/baseuri`, `Methods/protocols-array` | 12 |
| The documented union-facet gap (`docs/01` § 3.7) | 1 |

**Read the third row before planning.** Those twelve are not one job, and some
may not belong to this phase at all — classify each by tracing it, the way Phase
6's failures were classified, rather than by the directory it sits in.

### 1.1 What you inherit

| Module | State |
|---|---|
| `pyraml.types.complex_.JsonShape` | The class exists with `raw`, `validator`, `_cached_shape`, `_cached_defs`. `check()` and `validate()` **accept everything**, deliberately: a JSON-schema-typed declaration must parse today. `decode_facets` already rejects siblings. |
| `pyraml.registry.Raml.json_schema_registry` | Declared `dict[Any, Any]`, never written. |
| `pyraml.parser.fragments._decode_json_data_type` | An external `.json` include already becomes a `DataTypeFragment` whose declaration is `{type: "<raw json>"}`, so nothing downstream needs a branch. |
| `jsonschema>=4.21`, `referencing>=0.35` | Already in `pyproject.toml`, declared in Phase 0 so the lockfile would be stable. Unused so far. |
| `pyraml.domains` | All seventeen `DomainLocation`s, and every decode site already pushes one. |
| `pyraml.types.base.BaseShape.allowed_targets` | Decoded in Phase 4b (`None` ≠ `[]`), never read. |

`types/jsonschema_.py` is in `docs/02` § 3's module list and does not exist yet.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules.
2. **`docs/10-validation.md` § 6** — your whole specification for the large job.
   § 6.1 is compilation, § 6.2 the restrictions, § 6.3 the projection table.
3. **`docs/09-security-and-annotations.md` § B5 and `docs/05` § 7** — for
   `allowedTargets`. P8 already records both halves (where an annotation was
   applied, and what it was declared as); this phase only compares them.

---

## 3. What to build

Doc 15's order.

### 3.1 The shared registry and compilation (§ 6.1)

One registry per `Raml`, not per shape. A `$ref` target used by 40 schemas is
fetched and compiled once, and doc 10 calls this "the difference between linear
and quadratic on a schema-heavy project" — it is the only performance decision
in the phase, and it is structural, so make it first.

Two rules that are easy to miss and both security-relevant:

- **The schema registers under the RAML file's URI**, so a relative `$ref`
  resolves against the file that held the inline schema, not the process's
  working directory.
- **`$ref` resolution goes through `ResourceLoader`**, like every other read. A
  `$ref` to `http://json-schema.org/...` in an offline parse must fail loudly
  rather than reach the network — `referencing` will happily do the latter if
  handed a default retriever.

Draft comes from `$schema`; absent, default to 7, which is what the reference
implementation's meta-schema validation assumes.

### 3.2 The restrictions (§ 6.2)

`decode_facets` is done. What remains:

- `JsonShape.inherit(source)` errors unless the source carries the identical raw
  schema;
- a JSON-schema-typed name used in a type expression fails when the array's item
  inherit runs — check whether this already falls out of the previous rule
  before writing anything;
- **no schema in query parameters, query string, URI parameters or headers**, at
  those four decoders. Three of the four are `make_property_map` callers, so
  the check has one natural home rather than four.

### 3.3 The projection (§ 6.3)

`as_shape()`, lazily and cached. The table in § 6.3 is complete, including the
five rows that are *errors* (`if`/`then`/`else`, schema-form
`additionalProperties`, tuple-form `items`, a `false` schema) and the one
documented loss (`oneOf`'s exactly-one semantics).

**These are view objects.** They are not in `Raml.shapes`, they skip the three
always-empty ordered maps, and they are marked unwrapped. Feeding one back into
the parser's own passes is the failure mode to guard with a test, because the
model will look right until P9 tries to flatten it.

### 3.4 `allowedTargets`

Two fixtures, and the smallest job here: P8 already stores
`DomainExtension.target` and `BaseShape.allowed_targets`. Compare them in P10
and emit. `None` means "unrestricted"; `[]` means "nothing is allowed" and is a
different thing — Phase 4b made that distinction deliberately.

---

## 4. Decisions already settled — do not re-litigate

1. **`JsonShape.check()`/`validate()` accepting everything is deliberate**, not
   an oversight. They become real here; until they do, a JSON-schema-typed
   declaration parses rather than being rejected.
2. **An external `.json` include is already a `DataTypeFragment`** wrapping
   `{type: "<raw>"}` (`docs/04` § 5.2). Do not add a second path.
3. **The projection is one-way.** § 6.3's shapes are for consumers.
4. **`allowedTargets` enforcement is P10's**, not P8's (`docs/15`, Phase 7).

---

## 5. Reference source

go-raml is at `../go-raml-main`; grep `jsonSchemaCompiler` for the registry and
`JSONShape` for the rest. **Read it, and run it when the question is what it
does** — every phase since 5 has turned up a place where its comments describe
behaviour it does not have, and three of those became `KNOWN-ISSUES.md` entries.

Where a *fixture* is wrong, fix it in the suite (`docs/14` § 1.2).

---

## 6. Definition of done

Doc 15 sets the bar higher for this phase than for any other, because it is the
last one that changes behaviour:

- the full TCK runs with `unwrap=True, validate=True`; **every** `*invalid*`
  fixture outside the skip list produces an error, and every `*valid*` one does
  not;
- the skip list contains only Overlays, Extensions, XSD and the two network
  fixtures;
- a counting loader proves a `$ref` target shared by N schemas is read once;
- a test that an offline parse refuses a remote `$ref` rather than fetching it;
- one test per error row of § 6.3's table.

**Ratchet expectation: the last large move, and two-directional.** Compilation
can reject something valid — the risk this phase carries — so watch the valid
side.

Unit tests: `tests/unit/test_jsonschema.py`.

---

## 7. Scope boundary

Phase 8b finishes *correctness*. Benchmarks, depth guards, `re2`,
`parse_lenient`, the CLI and the public-API export list are Phase 9's — including
reconciling `docs/01` § 4's deviation list against what was actually built, and
widening `pyraml.__all__`, which `docs/13` § 4 now says is still narrow.

---

## 8. Working method

Branch: `git checkout -b phase-8b-jsonschema`. One logical change per commit;
**commit the ratchet separately from the code that moves it.**

If the code must diverge from a document, **amend the document in the same
commit**, and record what this brief got wrong in a section at its top — every
brief since Phase 5 has needed one.
