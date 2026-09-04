# Phase 6 brief — the structural merge and templates

A working brief for a fresh session. Read this, then the document it names. It
exists so you do not have to re-derive what earlier sessions already settled.

**Doc 15 calls this the phase with the highest defect risk.** It is the only one
where a subtle error produces a document that parses, validates and is wrong —
every other phase fails loudly. Budget accordingly: the golden set and the
property tests are not the finishing touch, they are how you find out.

---

## 1. Where the project stands

Master is at the Phase 5 merge. The gate passes:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
# 2033 passed, 52 skipped
```

TCK: 807 of 918, ratchet clean. **Every pass P0–P10 runs.** 84 fixtures still
failing need templates; this phase is the last large lever.

| Category | Fixtures waiting on templates |
|---|---|
| `ResourceTypes/` | 18 |
| `EdgeCases/` | 13 |
| `SecuritySchemes/`, `TemplateFunctions/` | 11 each |
| `Traits/` | 10 |
| `Resources/` | 9 |
| everything else | 12 |

`SecuritySchemes/` is on that list because a scheme often arrives *through* a
trait; Phase 7 needs this phase, not the other way round.

### 1.1 What you inherit

| Module | What you will use from it |
|---|---|
| `pyraml.parser.templates` | `parse_template_variables`, `collect_variables_index`, `collect_required_variables`, `apply_template_action`, `iter_indexed` — written ahead of the critical path, still unused |
| `pyraml.parser.source_ir` | `SourceEndPoint`, `SourceOperation`, `make_source_endpoint`, and the `traits`/`rt_traits` split that exists only for this phase |
| `pyraml.parser.directives` | `DirectiveRef` — name, params, position, for all three directive kinds |
| `pyraml.parser.endpoint_build` | the P4 driver, with the slot for directive resolution already marked between its two loops |
| `pyraml.registry` | `Raml._active_overlay`, declared and never written; `ParseCtx`, `push_ctx`/`pop_ctx` |
| `pyraml.parser.uritemplates` | `resource_path_name` — `§ 5.3`'s parameter, still unused |

### 1.2 The seams you pick up

- **`_raw_traits` and `_raw_resource_types`** on both `Library` and
  `APIFragment` (`fragments.py`), each a `Node` with a `# Phase 6` comment.
- **`TraitFragment` and `ResourceTypeFragment`** — the two fragment classes
  exist and retain their bodies.
- **`endpoint_build.build_endpoints`** — a comment reads "Phase 6 resolves
  `type:` and `is:` here, between the two stages". That placement is not
  incidental; see § 4.2.
- **`SourceEndPoint.provenance` / `SourceOperation.provenance`** — declared as
  `dict[Node, ParseCtx]`, never written.

---

## 2. Read these, in this order

1. **`CLAUDE.md`** — binding rules. Two are this phase's specifically:
   **`Node` defines no `__eq__`/`__hash__`** (the overlay is keyed by object
   identity — do not add equality, and do not key by `id()`), and **structural
   merge never mutates either input and preserves node identity**.
2. **`docs/08-templates-and-endpoints.md` §§ 1, 4–7** — your whole
   specification. § 1 restates the spec's algorithm in parser terms; read it
   before § 4 or the merge rules look arbitrary.
3. **`docs/02-architecture.md` § 4** — invariants **I9** (merge purity) and
   **I10** (node identity preserved), which this phase establishes and which the
   provenance overlay depends on.
4. **`docs/14-testing.md` §§ 2, 4** — the golden set and merge property tests
   are named there and are the definition of done.

---

## 3. What to build

Doc 15's order. Steps 1 and 2 are not separable in practice: the merge is
unusable without provenance, because a grafted trait body resolves its type
names in the wrong namespace and *still parses*.

### 3.1 `parser/structural_merge.py` (§§ 4–4.2)

`merge_structural(target, source, source_scope, overlay)`. Target is the
higher-priority branch. Five rules, and the two that get missed:

- **Opaque data facets.** `example`, `examples` and `default` are **not**
  recursed into. The spec's "objects recurse" rule is about RAML declaration
  structure, not user data; without this, example data containing a key named
  `example` is misread and partial merges produce values that validate against
  nothing.
- **Sequences union by structural equality**, comparing tag+value and ignoring
  positions. This is what yields the spec's `[mac, unix, win]`, and what keeps
  two `is:` entries with different parameters as two items — trait
  deduplication happens later and *by name* (§ 5.2).

Neither input is mutated; new containers are allocated but **child pointers are
reused**, which is what keeps the overlay's identity keys valid.

### 3.2 The provenance overlay (§§ 6.2–6.3)

`ProvenanceOverlay = dict[Node, ParseCtx]`, keyed by identity, sparse. Two
writers — `compile_source_provenance` during substitution, `mark_graft` during
the merge — and both are **set-if-absent**. A node that already carries a mark
defines its own scope domain, so `mark_graft` neither overwrites it nor descends
beneath it. That single rule is what makes all three cases of § 6.1 resolve
correctly; get it wrong and the failure is a type resolving in the wrong file.

Then read it in stage 2 (§ 6.3), which is where `Raml._active_overlay` finally
gets written.

### 3.3 `traits.py` and `resourcetypes.py`

The *definitions*, from both the inline seam and the fragment. The *references*
already exist as `DirectiveRef` in `directives.py` — do not duplicate them.

### 3.4 Application (§ 5)

Resource types (§ 5.1) with the six-step `compile_resource_type`, whose ordering
the spec's own example forces: **filter optional methods before re-collecting
required variables**, or `/queues` is asked for a `<<TextAboutPost>>` that only
appears inside a `post?` it does not have.

Traits (§ 5.2): four priority classes, deduplicated by name, closest wins. The
`traits`/`rt_traits` split on the IR exists solely to keep those classes
distinguishable after the merge has flattened everything else.

Then `resourcePathName` (§§ 5.3, 7.4) and the forbidden-parameter positions.

---

## 4. Decisions already settled — do not re-litigate

1. **The merge is on the YAML tree, not the model** (doc 08 § 2). Phase 5 built
   the IR for this.
2. **Provenance is keyed by node identity**, which is why `Node` has no
   `__eq__`. Keying by `id()` neither keeps the node alive nor stays unique.
3. **Marks are set-if-absent** (§ 6.2).
4. **One scope per shape** (§ 6.4) — the known granularity limit, deliberate.
5. **The index is injective**, unlike go-raml's `idx + i`, whose collision is
   reachable (§ 7.1). `iter_indexed` is already written this way.
6. **Directive resolution runs between P4's two stage loops**, so every subtree
   a template contributes exists before the single decode pass and joins the P7
   worklist.

---

## 5. Reference source

go-raml is at `../go-raml-main`. **Read its implementation, not only its
behaviour** — Phase 8a's worst regression came from probing what thirty seconds
in `validate.go` would have answered.

| Need | Where |
|---|---|
| The merge | grep `mergeStructural` |
| Provenance | grep `provenance`, `markGraft` |
| Resource type compilation | grep `compileResourceType` |
| Trait priority | grep `rtTraits` |

`KNOWN-ISSUES.md` in that checkout records five divergences found so far, three
in the implementation and two in the TCK. Add to it if this phase turns up more;
where a *fixture* is wrong, fix it there (`docs/14` § 1.2).

---

## 6. Definition of done

- The full golden set from `docs/14` § 2, including the three-way provenance
  golden.
- Merge properties under hypothesis (`docs/14` § 4, laws 2–4): identity,
  target-wins, and **purity** — a deep structural snapshot of both inputs
  unchanged afterwards.
- A test that asserts the *location* of every shape a template produces, not
  merely that it resolved. A type resolving in the wrong namespace is this
  phase's characteristic failure and it is invisible to a "does it parse" check.
- `Traits/`, `ResourceTypes/`, `TemplateFunctions/` pass.
- Optional-method filtering: the spec's `corpResource` / `/queues` example.
- The full gate passes.

**Ratchet expectation: large, and genuinely two-directional.** Unlike Phase 5,
where both regressions turned out to be fixture bugs, this phase can produce a
document that parses and is *wrong*. A fixture moving to `pass` is not by itself
evidence the merge is right; the provenance golden is.

Unit tests: `tests/unit/test_structural_merge.py`, `tests/unit/test_traits.py`,
`tests/unit/test_resourcetypes.py`.

---

## 7. Scope boundary

Phase 6 merges and applies templates. It does **not** decode `securitySchemes`
or apply `securedBy` — Phase 7, even though a scheme may arrive through a trait
this phase grafts. Overlays and Extensions are v1.1 and use a *different* merge
(`docs/15` "After v1").

---

## 8. Working method

Branch: `git checkout -b phase-6-templates`. One logical change per commit;
**commit the ratchet separately from the code that moves it.**

If the code must diverge from a document, **amend the document in the same
commit**, and record what this brief got wrong in a section at its top — Phase 5
established that habit and the corrections have been the most useful part of
every brief since.
