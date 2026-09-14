# Phase 4b brief — domain extensions (P8)

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

---

## 1. Why this phase exists, and why it is numbered 4b

`docs/15-implementation-plan.md` filed P8 under **Phase 7 — Security and
annotations**, build step 4. Nothing in P8 touches security: it binds an
annotation application to the annotation *type* it names, and annotation types
are shapes that Phase 2 already builds. It was grouped with security because the
phase title says "and annotations", not because of a dependency.

P8 is also **the only gap left in the P0–P9 chain**. `parser/entry.py` runs P0–P3,
then `resolve_shapes` (P7), then `unwrap_shapes` (P9). Between them sits a bare
comment. Every other implemented pass has an implemented predecessor.

The number: every existing phase keeps its number, because code comments name
phases by number (`types/shape.py` says "Phase 7 decodes it", `types/base.py`
says "Phase 8: docs/10-validation.md section 2") and renumbering invalidates them
across the tree. `4b` also states the real dependency — the binding needs only
Phase 2, but the re-binding of step 6 needs Phase 4's unwrap.

### 1.1 Where the project stands

Master is at the Phase 4 merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy fastraml/ && uv run pytest -q
# 1805 passed, 42 skipped
```

TCK: 594 of 930, ratchet clean.

**Phases 0 to 4 are complete.** Phase 0: positions, errors, uris, loaders,
yamlnode. Phase 1: the fragment layer and the pass driver (P0–P3). Phase 2: the
whole `types/` package. Phase 3: `types/resolve.py` (P7). Phase 4:
`types/inherit.py` and `types/unwrap.py` (P9).

| Module | What you will use from it |
|---|---|
| `fastraml.parser.annotations` | `DomainExtension`, `unmarshal_domain_extension`, `is_annotation_key` |
| `fastraml.parser.fragments` | `ReferenceResolver.reference_annotation_type`, implemented by every fragment kind |
| `fastraml.parser.references` | `UnresolvedReferenceError`, carrying `reason` and `name` for the caller to position |
| `fastraml.registry` | `Raml.domain_extensions` — the flat list this pass is a single loop over |
| `fastraml.types.unwrap` | `_Walk.done`, the `id → unwrapped shape` map step 6 needs |
| `fastraml.errors` | `RamlError.new/.wrap`, `Accumulator`, `ErrorKind` |

### 1.2 The seams you pick up

- **`entry.py`** — the P8 slot is a comment reading `# P8 — resolve domain
  extensions against their annotation types. Phase 7.`
- **`types/shape.py`** — the `allowedTargets` case in the common-facet walk is a
  bare `pass` with a comment naming Phase 7. It is already excluded from
  `COMMON_FACETS`, so the facet is consumed rather than leaking into
  `custom_facets`; nothing fills the seam.
- **`DomainExtension.defined_by`** — declared, typed `BaseShape | None`,
  commented "Filled by P8", never written.

`check` and `validate` remain Phase 8's. Leave them raising.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules. The one that bites here: **accumulate
   errors, do not fail fast**. An unresolvable annotation name is an error, and a
   document with three of them must report three.
2. **`docs/09-security-and-annotations.md` Part B** — your whole specification.
   B4 (resolution) and B5 (`allowedTargets`) are the two with work in them; B5's
   two subtleties are about phases that have not happened yet, so read them for
   what they oblige *later* phases to pass, not for what to build now.
3. **`docs/04-fragments-and-namespaces.md` § 3.1** — why
   `reference_annotation_type` falls back to `types`, which is not obvious and is
   load-bearing for the qualified form.

Skim only: doc 02 § 1 (the pass diagram, to confirm P8's position), doc 10 § 1
(what P10 will do with `defined_by`, so you can see the handoff).

---

## 3. What to build

Six steps in dependency order. A session can stop after any one and leave the
tree green.

### 3.1 `DomainLocation` (doc 09 § B5)

A `StrEnum` with the seventeen members doc 09 lists, in `parser/annotations.py`.
The string values are the spec's own target names (`DocumentationItem`,
`TypeDeclaration`, …), because `allowedTargets` is written in those terms and the
comparison should be against the enum's value without a translation table.

### 3.2 Carry the target on the `ParseCtx` stack

`ParseCtx` gains a `target: DomainLocation`; `unmarshal_domain_extension` reads
`current_ctx().target` the same way it already reads the anchor, and adds a
`target` slot to `DomainExtension`. A decoder that establishes a narrower site
wraps itself in `Raml.target_scope(...)`.

**This supersedes an explicit `target` parameter**, which is what this brief
first specified. Two things ruled it out once the code was in front of it. The
annotated-scalar form is built by `make_scalar_facet`, which has some four dozen
call sites across `types/` and `parser/` — every one would have to thread a
value. And § B5's own subtleties need an answer the decode site does not have:
an annotation inside a trait body records where the trait was *materialised*.

`target_scope` is a context manager, not a push/pop pair, so a decoder that
raises mid-construct cannot leave its site behind for the next annotation.

Four scopes are pushed today — `unmarshal_types`, `make_property_map`,
`make_example`, `decode_documentation_item` — over a per-fragment default keyed
off `FragmentKind`. Six of the seventeen locations are reachable:

| Where | `DomainLocation` |
|---|---|
| API fragment root | `API` |
| Library fragment root | `LIBRARY` |
| `unmarshal_types`, `make_property_map` | `TYPE_DECLARATION` |
| `unmarshal_types(is_annotation=True)` | `ANNOTATION_TYPE` |
| `decode_documentation_item` | `DOCUMENTATION_ITEM` |
| `make_example` | `EXAMPLE` |

The remaining eleven (`RESOURCE`, `METHOD`, `RESPONSE`, `REQUEST_BODY`,
`RESPONSE_BODY`, `RESOURCE_TYPE`, `TRAIT`, `SECURITY_SCHEME`,
`SECURITY_SCHEME_SETTINGS`, `OVERLAY`, `EXTENSION`) belong to sites Phases 5–7
create. Define them now so those phases push a scope rather than extend an enum.

**The annotated-scalar decision.** `resolve_annotated_scalar` builds extensions
for the `minLength: {value: 10, (a): x}` form, and doc 09's target vocabulary has
no member for a facet. go-raml is no oracle here — § B5 records that it parses
`allowedTargets` and ignores it entirely, so there is no behaviour to measure.
**Decision: the site is the enclosing declaration's**, which the ctx stack gives
for free — the facet builder pushes nothing, so it reads whatever the
declaration around it established. A facet is not independently annotatable in
the spec's vocabulary, and the enclosing declaration is what is being annotated.

### 3.3 Decode `allowedTargets` (doc 09 § B5)

Replace the `pass` in `types/shape.py` with a real decode onto a new `BaseShape`
slot. Accepts a single string or a sequence; each value must name a
`DomainLocation`, and one that does not is an error positioned at that value, not
at the facet key.

`None` and the empty sequence are different: absent means "any target", and the
distinction has to survive into P10. Do not collapse them.

Remember `__slots__` — a new field on `BaseShape` goes in the slot tuple, in the
group it belongs to (this one is a common facet, but only for annotation types;
put it with the common facets and say so in a comment). Check whether `clone`
needs to copy it: it does, and `test_clone.py` is where that gets pinned.

### 3.4 `resolve_domain_extensions(raml)` — the pass itself (doc 09 § B4)

One loop over `Raml.domain_extensions`. For each, resolve `name` through
`extension.anchor.reference_annotation_type(name)` and assign `defined_by`. The
qualified form `lib.name` works through the anchor's `uses:` without special
handling — `resolve_reference` already does the last-dot split.

An unresolvable name is an error: spec § Annotations, "All annotations used in an
API specification MUST be declared in its annotationTypes node." Accumulate;
position at `key_pos`.

`anchor is None` is possible for a programmatically built extension. Fall back to
`raml.resolver_at(extension.location)`, as P7 does for a shape with no anchor,
and treat a miss as the same unresolvable-name error.

Placement: a free function, in `parser/annotations.py` beside the model it
resolves. It imports `types/` only under `TYPE_CHECKING` (`defined_by` is a
`BaseShape`), so no new runtime edge is created — check doc 02 § 2's layering
rules before you place it anywhere else.

### 3.5 Wire it into the driver

`entry.py`, at the existing P8 comment: after `resolve_shapes`, before the
`options.unwrap` block. Unconditional — binding is not opt-in, because an
undeclared annotation is malformed input whether or not the caller asked to
unwrap or validate.

### 3.6 Re-bind `defined_by` after unwrap (doc 09 § B4)

"Because unwrap replaces shape objects, P9 re-binds `defined_by` to the unwrapped
instance; skipping that step silently validates against the un-flattened
declaration and misses inherited constraints."

The machinery exists. `unwrap_shapes` already rebinds `fragment_types` and
`fragment_annotations` through `walk.done`, in a loop commented as "a cheap
second pass over `done`". Add the extension loop beside it, same pattern:
`walk.done.get(defined_by.id, defined_by)`.

Do this in the same commit as step 3.4. A `defined_by` that is bound but stale is
worse than one that is unbound, because the second fails loudly.

---

## 4. Decisions already settled — do not re-litigate

1. **Binding is P8; enforcement is P10.** Doc 09 § B5: "In P10, if the annotation
   type declares targets and the application's site is not among them, the
   diagnostic is `annotation not allowed at this target`". This phase decodes
   `allowedTargets` and records the site. It does **not** compare them, and it
   does not validate any annotation *value*. Both are Phase 8a's.
2. **The target rides the `ParseCtx` stack, not a parameter** (§ 3.2).
3. **The annotated-scalar site is its enclosing declaration's** (§ 3.2).
4. **Absent `allowedTargets` ≠ empty `allowedTargets`** (§ 3.3).
5. **The pass is unconditional** (§ 3.5).
6. **The flat `domain_extensions` list is why this is a loop and not a
   traversal** (doc 09 § B3). Do not add a model walk.

---

## 5. Reference source

go-raml is at `../go-raml-main`, but it is a weak oracle for this phase: it
parses `allowedTargets` and ignores it, so §§ 3.1–3.3 have no reference
behaviour. Where it is worth reading:

| Need | Where |
|---|---|
| Its P8 equivalent, the binding loop | grep `domainExtension` in `parse.go` |
| What it does with an unresolvable annotation name | same loop |

Go is installed: when the question is what it *does*, run it (`go test -run
<name> .` against a throwaway `zz_*_test.go`, deleted afterwards). Its comments
have been wrong about its own behaviour.

---

## 6. Definition of done

- Every one of the six reachable `DomainLocation`s has a test that an extension
  created at that site records it.
- A qualified annotation name (`lib.ann`) binds through the anchor's `uses:`;
  an unqualified one binds locally; a name declared in neither is one accumulated
  error, and three such names are three errors.
- `allowedTargets` round-trips in both forms (scalar and sequence); an
  unrecognised target name is an error positioned at the value.
- Absent and empty `allowedTargets` are distinguishable on the model.
- **After a parse with `unwrap=True`, every bound `defined_by` is the same object
  the corresponding name index points at.** Assert it over the corpus in
  `tests/tck/test_invariants.py`, beside I1/I4/I5/I6 — it is exactly the failure
  doc 09 § B4 warns about, and it is invisible to a unit test on a type with no
  parents. Use the existing `unwrapped_corpus` fixture.
- A `clone`d shape keeps its `allowed_targets` (`test_clone.py`).
- The full gate passes.

**Ratchet expectation: almost nothing moves, and that is the expected result.**
The only thing P8 rejects by itself is an undeclared annotation name. Of the two
`invalid-undefined-annotation` fixtures, `Annotations/resource-06` applies the
annotation on a resource — with no endpoint decoding the `DomainExtension` is
never created, so it stays failing until Phase 5. Expect one fixture, possibly
zero. The value of this phase is that it is the prerequisite for the 22
Annotations fixtures Phase 8a can reach, and that it closes the pass chain.

**The risk to watch is the other direction.** This pass only ever adds
diagnostics, and 474 valid fixtures currently pass. If anchor resolution is
imperfect on any qualified form, a valid fixture regresses. Read every ratchet
diff; a valid fixture moving to `fail` is a bug in this phase, not progress.

Unit tests: `tests/unit/test_domain_extensions.py`.

---

## 7. Scope boundary

This phase binds and records. It does not:

- compare a site against `allowedTargets` (P10, Phase 8a);
- validate an annotation's value against its type (P10, Phase 8a);
- create any new application site — the eleven unreachable `DomainLocation`s
  stay unreachable until Phases 5–7 build the sites that use them;
- touch `securitySchemes`, `securedBy` or anything else in doc 09 Part A, which
  remains Phase 7's.

---

## 8. Working method

Branch: `git checkout -b phase-4b-domain-extensions`. One logical change per
commit. If the code must diverge from a document, **amend the document in the
same commit**.

Two doc amendments are known in advance:

- **`docs/15-implementation-plan.md`** — Phase 7 loses build step 4; a Phase 4b
  section is added; Phase 8's prerequisite line stops claiming it needs Phase 7
  for annotation values.
- **`docs/09-security-and-annotations.md` § B1** — the `DomainExtension` slot
  list gains `target` and drops `_raml`, which the implementation does not carry.
  § B5 records the annotated-scalar decision from § 3.2.
