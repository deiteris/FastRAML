# 15 — Implementation plan

Nine phases. Each has a definition of done that is checkable, and each leaves the
repository in a state where `pytest` passes and the TCK ratchet has moved forward
or held.

The ordering is dictated by the dependency structure, not by feature importance:
the type system must exist before endpoints, because endpoints are mostly types;
the merge must exist before templates; validation comes last because it validates
everything else.

Each phase below states three things. **Prerequisites** are what must already
exist, named precisely enough to check — a phase whose prerequisites are unmet
cannot be started, only faked. **Build** is an ordered decomposition: the steps
run in dependency order *within* the phase, so a session can stop after any one
of them and leave the tree green. **Done when** is the checkable definition of
done.

A working brief per phase lives in `docs/briefs/`. The brief carries a phase's
settled decisions and the reference line ranges worth opening; this document
stays the map.

---

## Phase 0 — Foundations — **complete**

**Prerequisites:** none.

**Build:**

1. `pyproject.toml` rewritten for pyRAML (the file it replaced described an
   unrelated `authkit` project; only the `[tool.ruff]` and `[tool.mypy]` blocks
   were worth keeping), package skeleton, `py.typed`, CI with ruff + mypy +
   pytest.
2. `positions` and `errors` — everything downstream reports through them.
3. `uris` — the canonical `file://` form every cache key depends on.
4. `loaders` — the only module that touches the filesystem or the network.
5. `yamlnode` — `Node`, the composer adapter, alias expansion, and the depth and
   node-count guards.
6. The TCK harness pointed at an external checkout via `PYRAML_TCK_DIR`, and an
   empty ratchet file.

**Done when:** `pytest tests/unit/test_uris.py tests/unit/test_loaders.py
tests/unit/test_yamlnode.py` passes on Linux and Windows; `SafeFileLoader`
refuses the three escape vectors; the TCK harness discovers all 967 fixtures.

---

## Phase 1 — Fragments and includes — **complete**

**Prerequisites:** Phase 0. Normative: [03](03-yaml-and-io.md) §§ 3–7,
[04](04-fragments-and-namespaces.md). Brief: `docs/briefs/phase-1.md`.

**Build:**

1. `registry.Raml` with the full field set and the `ParseCtx` stack — everything
   later registers into it, so it comes first.
2. `parser/includes.py`: URI resolution for the three argument forms, the
   per-parse node cache, the size limit, `note_include_ref` vs `resolve_include`.
3. `datanode.py`: `DataNode`/`ValueNode`, inline JSON, scalar-cycle detection.
4. `parser/annotations.py`, then `parser/facets.py` — the facet builder needs
   domain extensions for the annotated-scalar form.
5. `parser/references.py`: the last-dot split and the two resolvers.
6. `parser/fragments.py`: kind identification, the nine fragment classes, the
   cache lifecycle, `uses:` resolution. Everything the phase does not own is
   retained as a `_raw_*` node.
7. `parser/entry.py`: `ParseOptions`, both entry points, and the P0–P3 driver
   with P4–P10 marked as no-ops.

**Done when:** a library with `uses:` and `!include`s parses to a fragment graph;
a mutually-importing pair of libraries terminates; a counting loader confirms one
read per file; `Fragments/` and `Libraries/` TCK categories are attempted (many
will still fail on types).

**Result.** TCK baseline recorded: 529 of the 930 fixtures that run behave as
their name promises — `Fragments/` 26 of 41, `Libraries/` 37 of 49. (930, not
967: the Overlay and Extension categories are skipped wholesale.) The rest need
types, templates, or those two v1.1 fragment kinds.

---

## Phase 2 — The type system — **complete**

**Prerequisites:** Phase 1. Specifically: `Raml.put_type` /
`put_annotation_type` / `put_typedef` / `put_shape` / `unresolved_shapes`;
`make_scalar_facet` and `ScalarFacet`; `DataNode`; and the `_raw_types`,
`_raw_annotation_types`, `_raw_base_uri_parameters`, `_raw_declaration` and
`_raw_examples` seams. Normative: [05](05-type-model.md), with
[03](03-yaml-and-io.md) §§ 6–7 for the two value carriers. Brief:
`docs/briefs/phase-2.md`.

**Build:**

1. `types/base.py`: `BaseShape` with its full `__slots__`, `Property`,
   `PatternProperty`, and the `Shape` protocol.
2. `types/inference.py`: `identify_shape_type` and `FACET_TYPE_HINT`. Small,
   self-contained, and every later step calls it.
3. `types/xml.py` and `types/examples.py`: two leaf records with decoders, needed
   by step 5's common-facet walk.
4. `types/scalars.py` and `types/complex_.py`: the fourteen kinds plus
   `UnknownShape`, `JsonShape` and `RecursiveShape`. Only `decode_facets` has a
   body; `inherit`, `alias_to`, `check`, `validate` and `clone` raise
   `NotImplementedError` naming the phase that fills them. `ObjectShape`,
   `ArrayShape` and `UnionShape` each declare a `DECLARATION_FACETS` table and
   take those children through `__init__`; no kind imports `shape.py`
   ([02](02-architecture.md) § 2).
5. `types/shape.py`: `make_shape`, `make_body_shape`, `make_property`,
   `make_pattern_property`, and the kind dispatch — which reads
   `DECLARATION_FACETS` off the class it is about to construct, builds those
   children, and passes them in. This is the step that has to be right; the rest
   of the phase serves it.
6. Wire the seams: `unmarshal_types(..., is_annotation=)` per
   [04](04-fragments-and-namespaces.md) § 5.1, called from the `_raw_*`
   attributes, and the example builder called from `NamedExample`.

Shapes are created but not resolved; everything unresolvable is an
`UnknownShape`.

**Done when:** the `Types/` TCK category parses (not yet validates) except where
it needs expressions or inheritance; unit tests cover every inference rule and
both property-optionality corner cases; a test asserts invariant I4 over a parsed
corpus.

**Outcome.** TCK 529 → 558 of 930, no regressions. I4, I1 and declaration order
are asserted over 472 parsed fixtures and 1167 shapes
(`tests/tck/test_invariants.py`).

Wiring the seams exposed three defects the unit tests had not: an empty
declaration block (`properties:` with nothing under it) read as an error; a JSON
include with a pointer (`schema.json#/definitions/User`) was not recognised as
JSON; and a scalar whose tag would not convert crashed instead of keeping its
text. The third led to the YAML layer being wrong more broadly — PyYAML resolves
YAML 1.1, RAML is YAML 1.2 — which is now fixed and pinned by a differential
oracle ([03](03-yaml-and-io.md) § 2.2, [14](14-testing.md) § 1.4).

---

## Phase 3 — Type expressions and resolution

**Prerequisites:** Phase 2 — a populated `unresolved_shapes`, and `type_expr`
retained on every shape whose kind could not be settled. The RDT tokenizer and
parser already exist (`types/expressions/lexer.py`, `parser.py`), built ahead of
the critical path; this phase consumes them rather than writing them. Normative:
[06](06-type-expressions.md), [07](07-resolution-and-inheritance.md) §§ 1–2.

**Build:**

1. `types/resolve.py`: `resolve_shapes` and its worklist drain (P7) **and** the
   AST → shape visitor, which are mutually recursive and so share a module
   ([02](02-architecture.md) § 2). Each `UnknownShape` becomes a concrete kind
   **in place**, so references already taken stay valid.
2. The reference positions (`TypeExprRef`) that make go-to-definition work
   inside an expression, and the two lookups they need
   ([06](06-type-expressions.md) § 3.2, [04](04-fragments-and-namespaces.md)
   § 4.2).
3. The expression cache, keyed by text into `Raml.expr_cache` — one parse per
   distinct expression, not per occurrence.
4. Alias-versus-inheritance discrimination ([06](06-type-expressions.md) § 3.1) —
   the distinction the remaining resolution rules hang off. It has a decode half
   too: the form of the declaration must be recorded while the value node is
   still in hand.
5. Cyclic-reference detection, so a self-referential expression is a diagnostic
   rather than a hang.

**Done when:** every line of `rdt/examples.txt` builds the expected shape;
`Types/` fixtures that use expressions parse; no reachable `UnknownShape` remains
after a parse (invariant I5 asserted by a test helper).

**Outcome.** TCK 558 → 584 of 930, no regressions. All twenty-six are *invalid*
fixtures now rejected: resolution only ever adds diagnostics, so the informative
half of the result is that no valid fixture moved in either direction.
I5 is asserted over the corpus alongside a reachability walk that ties it back
to I4 (`tests/tck/test_invariants.py`).

Two things the plan had in the wrong place. The visitor and the driver are
mutually recursive — a reference's target may itself be unresolved — so they
share `types/resolve.py` rather than splitting across `expressions/build.py`,
and the driver is a free function because `registry.py` may not import `types/`
at runtime ([02](02-architecture.md) § 2, [07](07-resolution-and-inheritance.md)
§ 1). Step 4 also turned out to have a decode half: the form of a declaration
decides alias versus inheritance, and had to be recorded while the value node
was still in hand ([06](06-type-expressions.md) § 3.1).

---

## Phase 4 — Inheritance and unwrap

**Prerequisites:** Phase 3 — unwrap merges a shape with its parents, and a shape
whose kind is still `UnknownShape` cannot be merged (the P7-before-P9 ordering in
[02](02-architecture.md) § 1). Normative: [07](07-resolution-and-inheritance.md)
§§ 3–5.

**Build:**

1. The three clone operations ([07](07-resolution-and-inheritance.md) § 5).
   Unwrap is defined in terms of them and `copy.deepcopy` is forbidden, so they
   come first.
2. Per-kind `inherit` and `alias_to` — the rule sets in § 3.5.
3. Multiple inheritance via the synthetic shape (§ 3.3), then the union
   interaction (§ 3.4), which is the case that breaks naive implementations.
4. `unwrap_shape` and its drivers (§§ 3.1–3.2), iterating `fragment_typedefs`
   rather than traversing the graph.
5. Recursion marking (§ 4): `RecursiveShape` where a cycle closes.

**Done when:** the golden cases for multiple inheritance, union × inheritance and
recursion marking pass; a test proves a parent's `properties` dict is not mutated
when two children inherit from it; `unwrap(unwrap(x)) == unwrap(x)` holds under
hypothesis.

**Outcome.** TCK 584 → 594 of 930, no regressions. 109 unit tests across
`test_clone.py`, `test_inherit.py` and `test_unwrap.py`; **I6** asserted over the
corpus from its own `unwrap=True` fixture, kept separate because I4 and I5 are
about the model *before* flattening.

Four things [07](07-resolution-and-inheritance.md) did not say, now amended
there. A union target that declares no members takes the parent's outright —
not an edge case, since `T: {type: SomeUnion, …}` gets the union kind from P7
but no `anyOf`, and its absence regressed a valid fixture. `Raml.shapes` is
rebuilt rather than appended to. There is no `clone_shallow`: it was listed for
"swapping a shape's kind", which P7 does by building a fresh kind object, and
go-raml's own is called from nowhere. And § 3.6 now states both halves of what
an alias is — own identity, *shared* contents — because only the first was
written down, and the second is what a reader has to know before touching
recursion marking.

The one defect worth remembering: marking must not descend into an alias whose
referent is on the walk. Miss it and the walk iterates the dict it is already
inside, and its substitution lands in the referent's — which is how `Node.kids`
stops being an array. The first fix attempted was to copy the alias's
containers, which hides that bug and silently breaks the propagation an alias
exists for.

---

## Phase 4b — Domain extensions (P8) — **complete**

Numbered `4b` rather than renumbering what follows: code comments name phases by
number, and the re-binding step depends on Phase 4's unwrap.

**Prerequisites:** Phase 2 for annotation types, which are shapes, and Phase 4
for the re-binding. Normative: [09](09-security-and-annotations.md) Part B.
Brief: `docs/briefs/phase-4b.md`.

**Why it moved.** P8 was Phase 7's build step 4, grouped there because that phase
is titled "Security and annotations". Nothing in it touches security: it binds an
annotation application to the annotation *type* it names. It was also the only
gap left in the P0–P9 chain, and Phase 8's `_validate_domain_extensions` cannot
be written without it.

**Build:**

1. `DomainLocation` in `pyraml/domains.py`, a leaf module because `registry.py`
   carries it on `ParseCtx` and `parser/annotations.py` reads it
   ([02](02-architecture.md) § 2).
2. `ParseCtx.target` and `Raml.target_scope`, plus the per-`FragmentKind` default
   and the four narrower scopes.
3. `allowedTargets` decoded onto `BaseShape.allowed_targets`, `None` and `[]`
   kept distinct.
4. `resolve_domain_extensions` — the pass — wired unconditionally between P7 and
   P9.
5. `defined_by` re-bound through `walk.done` at the end of unwrap.

**Done when:** each of the six reachable sites records itself; a qualified name
binds through `uses:` and an undeclared one is an accumulated error; both
`allowedTargets` forms round-trip and a bad entry is positioned at that entry;
a binding still points at a live shape after unwrap, asserted over the corpus.

**Outcome.** TCK 594 → 595 of 930, no regressions — as predicted, since the only
thing P8 rejects by itself is an undeclared annotation name. 30 unit tests in
`test_domain_extensions.py`, plus two corpus checks over the 52 annotation
applications the TCK's valid fixtures contain.

Twelve existing unit-test fixtures applied annotations they never declared, and
were corrected rather than the rule relaxed. That is the shape of this pass's
risk: it only ever adds diagnostics, so everything it can do to a document that
used to parse is reject it.

The design changed under contact. An explicit `target` parameter on
`unmarshal_domain_extension` was the plan; the annotated-scalar form goes
through `make_scalar_facet`, whose four dozen call sites would each have had to
thread one, and § B5's trait subtlety needs a value the decode site does not
have. The `ParseCtx` stack already solved exactly this problem for the anchor.

---

## Phase 5 — Endpoints (stage 1 and 2, no templates)

**Prerequisites:** Phase 2 — a body, a header and a query parameter are all
shapes, so `make_shape` and `make_body_shape` must exist. **Not** Phase 3 or 4:
endpoints are built at P4, *before* shapes resolve at P7, precisely so that
template-contributed subtrees join the same resolution batch.
`parser/uritemplates.py` already exists. `APIFragment._raw_endpoints` holds the
input. Normative: [08](08-templates-and-endpoints.md) §§ 3, 8.

**Build:**

1. `parser/source_ir.py`: `SourceEndPoint` / `SourceOperation`, decoding only the
   directives (`type:`, `is:`, `securedBy:`) and keeping everything type-bearing
   as its YAML subtree.
2. `parser/endpoints.py`: `EndPoint`, `Operation`, `Request`, `Response`, `Body`.
3. `parser/source_decode.py`: stage-2 materialization of the IR into those
   classes, decoding each tree exactly once.
4. Headers, query parameters and the query string — all properties declarations,
   so they reuse `make_property`.
5. Media-type handling and defaults, against `Raml.global_media_types`.
6. URI template parsing, parameter synthesis, and downward propagation (P6).
7. Duplicate-URI detection through `Raml.endpoints`.

Directive resolution is a no-op stub in this phase — `type:` and `is:` are parsed
and stored, not applied.

**Done when:** `Resources/`, `Methods/`, `Responses/`, `MethodResponses/` TCK
categories pass except for fixtures using traits or resource types.

---

## Phase 6 — The structural merge and templates

**Prerequisites:** Phase 5 — the stage-1 IR is what the merge operates on, and
the stage-2 decoder is what reads the overlay. `parser/templates.py` (the
variable index, substitution, the ten actions) already exists, built ahead of the
critical path, and `Raml._active_overlay` is already declared. The `_raw_traits`
and `_raw_resource_types` seams, plus the `TraitFragment` and
`ResourceTypeFragment` bodies, hold the input. Normative:
[08](08-templates-and-endpoints.md) §§ 4–7.

**Build:**

1. `parser/structural_merge.py`: `merge_structural` over mappings and sequences
   (§§ 4.1–4.2), mutating neither input and preserving node identity.
2. The provenance overlay and `mark_graft` (§ 6.2), then reading it in stage 2
   (§ 6.3). The merge is unusable without this: a grafted trait body would
   resolve its type names in the wrong namespace.
3. `parser/traits.py` and `parser/resourcetypes.py`: the definitions and their
   reference forms, from both the inline and the fragment seam.
4. Application with the four priority classes, optional-method filtering, and
   resource-type chaining (§ 5).
5. `resourcePathName` and the parameter rules (§§ 5.3, 7.4).

This is the phase with the highest defect risk. It lands with the full golden set
from [14](14-testing.md) § 2 and the merge property tests from § 4.

**Done when:** `Traits/`, `ResourceTypes/`, `TemplateFunctions/` pass; the
three-way provenance golden is green; merge purity and target-wins hold under
hypothesis.

---

## Phase 7 — Security and annotations

**Prerequisites:** Phase 5 for the operations schemes attach to, and Phase 6 if a
scheme arrives through a trait. The `_raw_security_schemes` and `_raw_secured_by`
seams and the `SecuritySchemeFragment` body hold the input. Normative:
[09](09-security-and-annotations.md) Part A.

**Build:**

1. `parser/security.py`: `SecuritySchemeDefinition` and the six settings
   variants, from both the inline and the fragment seam.
2. `securedBy` resolution and inheritance with `explicit_secured_by`, the null
   scheme, and OAuth 2.0 scope narrowing.
3. Fill `Raml.global_secured_by` from `APIFragment._raw_secured_by` — the
   pre-pass that harvests it already runs in the right order.
4. Push a `DomainLocation` scope at each application site this phase creates:
   `SECURITY_SCHEME` and `SECURITY_SCHEME_SETTINGS`. The enum and the mechanism
   are Phase 4b's; Phases 5 and 6 owe the same for their own sites.

P8 itself moved to Phase 4b, which also decodes `allowedTargets`. Enforcing it
is Phase 8's, with the rest of validation.

**Done when:** `SecuritySchemes/` passes, and the `Annotations/` fixtures whose
application site is a security scheme.

---

## Phase 8 — Validation and JSON Schema

**Prerequisites:** Phase 4 — validation runs against unwrapped shapes, and
`validate=True` without `unwrap=True` unwraps a private copy. Phase 4b for
`defined_by`, without which annotation values have nothing to validate against.
**Not** Phases 5–7. `_validate_types` iterates `fragment_typedefs`, which the
endpoint decoders register into through the same helpers types use, so endpoints
feed the validator without the validator changing. Phases 5–7 widen its input,
they do not change its shape. Normative: [10](10-validation.md).

**Build:**

1. `check()` per kind — is the declaration self-consistent? It needs no instance
   data and catches the largest class of fixtures.
2. Instance `validate()` per kind, including the object validation order,
   `uniqueItems`, and the numeric comparison fast path (§§ 5.1–5.3).
3. Example, default and enum validation (§ 3), on top of steps 1 and 2.
4. Custom facet validation against the `facets:` declarations found in the
   inheritance chain (§ 4).
5. Annotation values against `defined_by`, and `allowedTargets` against the
   recorded site — the six locations reachable before Phase 5, all seventeen
   after Phase 7.
6. The shared JSON Schema registry, `JsonShape` and its restrictions, and the
   schema → shape projection (§ 6).

Steps 1–5 stand alone and are worth taking before Phases 5–7: 133 of the 336
fixtures the ratchet still records as failing declare no resource and no
template, and 325 of those 336 are *invalid* fixtures the parser fails to
reject. Validation is the largest single lever in the corpus and it is on the
critical path for the endpoint categories too, most of which need it before
their invalid fixtures can fail.

Doc 10 § 6.2's "no schema in query parameters, query string, URI parameters or
headers" needs three decoders that do not exist until Phase 5. That check lands
with them.

**Done when:** every `*invalid*` fixture outside the skip list that declares no
resource and no template produces an error, and no `*valid*` fixture regresses —
the whole-corpus form of that criterion needs Phase 7 and is Phase 8's second
half.

---

## Phase 9 — Hardening and release

**Prerequisites:** Phases 0–8. A benchmark is meaningless against a parser that
does not yet do all the work. Normative: [12](12-performance.md),
[13](13-public-api.md).

**Build:**

1. The benchmark suite and its committed baselines ([14](14-testing.md) § 5).
2. Depth guards and `re2` support — the two hardening items that change
   behaviour, so they land before the API is frozen.
3. `parse_lenient`, which needs every pass to accumulate rather than raise.
4. The CLI ([13](13-public-api.md) § 8).
5. Public API docs, docstrings and the README; reconcile the deviation list in
   [01](01-scope-and-coverage.md) § 4 against what was actually built.

**Done when:** `bench_large` is within 15 % of linear against a half-size corpus;
peak RSS under 400 MB; `mypy --strict` clean; the deviation list in
[01](01-scope-and-coverage.md) § 4 matches reality; the skip list contains only
Overlays, Extensions and XSD.

---

## After v1

In rough priority order:

1. **Overlays and Extensions** (spec § Overlays and Extensions). The merging
   algorithm is specified in detail in spec § Merging Rules and maps onto the
   structural merge already built in Phase 6 — with different rules (extension
   wins over master, arrays append, conflicting properties are removed) and an
   additional post-merge behaviour-invariance check for overlays. The `extends`
   chain, the "all overlays share one master" constraint, and the allowed-
   differences table are the work. Estimated: one phase.
2. **Facets on a union declaration** ([01](01-scope-and-coverage.md) § 3.7).
   Parsed and not enforced today, which is silent: a document that looks
   constrained is not. The work is in P7/P9 — `UnionShape` retains the
   undigested nodes and the merge decodes them against each member, cloning
   first ([07](07-resolution-and-inheritance.md) § 3.4). The reference
   implementation discards them outright; written up in its `KNOWN-ISSUES.md`.
3. **Multi-parent custom facet chain walk** — the known limitation in
   [10](10-validation.md) § 4.
4. **Union `enum` semantics** — spec § Union Type's enum rules, which the
   reference implementation also defers.
5. **Finer provenance granularity** — [08](08-templates-and-endpoints.md) § 6.4.
6. **Downstream packages** — LSP server, JSON Schema / OpenAPI converters,
   middleware. All are consumers of the model, not changes to it; `retain_source`
   and the `TypeExprRef`/`IncludeRef` indices exist so none of them requires a
   parser change.

## Risk register

| Risk | Where | Mitigation |
|------|-------|------------|
| Provenance is subtly wrong; types resolve in the wrong namespace | Phase 6 | The three-way golden; a test that asserts the *location* of every shape produced by a template, not just that it resolved |
| The two index walks (`collect_variables_index` / substitution) drift apart | Phase 6 | **Closed.** One shared walker (`iter_indexed`); a fixture with nested sequences asserts they agree. The index was also made injective — go-raml's `idx + i` rule is not, and the collision is reachable (docs/08 § 7.1) |
| Parent-shape mutation during multiple inheritance | Phase 4 | Explicit test: two children inherit one parent, assert the parent is byte-identical after |
| `RecursionError` on deep user input | Phases 4, 6, 8 | Depth guard + hypothesis property 10 |
| Performance regressions creep in unnoticed | all | Benchmarks in CI from Phase 9, baselines committed |
| Divergence from the reference on an ambiguous spec point | all | The cross-check script ([14](14-testing.md) § 1.3); every divergence resolved into a documented deviation or a bug |
