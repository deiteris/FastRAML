# 15 — Implementation plan

Nine phases. Each has a definition of done that is checkable, and each leaves the
repository in a state where `pytest` passes and the TCK ratchet has moved forward
or held.

The ordering is dictated by the dependency structure, not by feature importance:
the type system must exist before endpoints, because endpoints are mostly types;
the merge must exist before templates; validation comes last because it validates
everything else.

---

## Phase 0 — Foundations

**Build:** `pyproject.toml` rewritten for pyRAML (the current file describes an
unrelated `authkit` project — only the `[tool.ruff]` and `[tool.mypy]` blocks are
worth keeping). Package skeleton, `py.typed`, CI with ruff + mypy + pytest, the
TCK harness pointed at an external checkout via `PYRAML_TCK_DIR`, and an empty
ratchet file.

**Modules:** `positions`, `errors`, `uris`, `loaders`, `yamlnode`.

**Done when:** `pytest tests/unit/test_uris.py tests/unit/test_loaders.py
tests/unit/test_yamlnode.py` passes on Linux and Windows; `SafeFileLoader`
refuses the three escape vectors; the TCK harness discovers all 967 fixtures.

---

## Phase 1 — Fragments and includes

**Build:** `Raml` registry, `ParseCtx` stack, fragment identification, the
`!include` machinery with its cache and limits, `Library`/`DataType`/`API` root
decoding down to (but not including) type declarations, `uses:` resolution, the
two reference resolvers, `DataNode`/`ValueNode`, the annotated-scalar form.

**Done when:** a library with `uses:` and `!include`s parses to a fragment graph;
a mutually-importing pair of libraries terminates; a counting loader confirms one
read per file; `Fragments/` and `Libraries/` TCK categories are attempted (many
will still fail on types).

---

## Phase 2 — The type system

**Build:** `BaseShape`, `ScalarFacet`, all fourteen concrete shapes with their
facet decoding, `Property`/`PatternProperty` with the `?` rules, default-type
inference, examples, custom facet declarations, `xml:`.

Shapes are created but not resolved; everything unresolvable is an
`UnknownShape`.

**Done when:** the `Types/` TCK category parses (not yet validates) except where
it needs expressions or inheritance; unit tests cover every inference rule and
both property-optionality corner cases.

---

## Phase 3 — Type expressions and resolution

**Build:** the RDT tokenizer, parser, AST cache and shape builder; `resolve_shapes`
and its worklist; alias-vs-inherit discrimination; cyclic-reference detection.

**Done when:** every line of `rdt/examples.txt` builds the expected shape;
`Types/` fixtures that use expressions parse; no reachable `UnknownShape` remains
after a parse (invariant I5 asserted by a test helper).

---

## Phase 4 — Inheritance and unwrap

**Build:** per-kind `inherit`, `alias_to`, multiple inheritance with the synthetic
shape, union expansion, `unwrap_shape` and its drivers, recursion marking, the
three clone operations.

**Done when:** the golden cases for multiple inheritance, union × inheritance and
recursion marking pass; a test proves a parent's `properties` dict is not mutated
when two children inherit from it; `unwrap(unwrap(x)) == unwrap(x)` holds under
hypothesis.

---

## Phase 5 — Endpoints (stage 1 and 2, no templates)

**Build:** `SourceEndPoint`/`SourceOperation` IR, stage-2 materialization,
`Operation`/`Request`/`Response`/`Body`, headers/query parameters/query string,
media-type handling and defaults, URI template parsing, parameter synthesis and
propagation, duplicate-URI detection.

Directive resolution is a no-op stub in this phase — `type:` and `is:` are parsed
and stored, not applied.

**Done when:** `Resources/`, `Methods/`, `Responses/`, `MethodResponses/` TCK
categories pass except for fixtures using traits or resource types.

---

## Phase 6 — The structural merge and templates

**Build:** `merge_structural` with the provenance overlay, `mark_graft`, the
template variable index and substitution, the ten transform functions,
`TraitDefinition`/`ResourceTypeDefinition`, application with the four priority
classes, optional-method filtering, resource-type chaining.

This is the phase with the highest defect risk. It lands with the full golden set
from [14](14-testing.md) § 2 and the merge property tests from § 4.

**Done when:** `Traits/`, `ResourceTypes/`, `TemplateFunctions/` pass; the
three-way provenance golden is green; merge purity and target-wins hold under
hypothesis.

---

## Phase 7 — Security and annotations

**Build:** security scheme definitions and all six settings variants,
`securedBy` inheritance with `explicit_secured_by`, the null scheme, OAuth 2.0
scope narrowing; domain extensions, their resolution, `DomainLocation` tagging at
every application site, `allowedTargets` enforcement.

**Done when:** `SecuritySchemes/` and `Annotations/` pass, including the
`allowedTargets` fixtures that the reference implementation skips.

---

## Phase 8 — Validation and JSON Schema

**Build:** `check()` per kind, instance `validate()` per kind, example/default/enum
validation, custom facet validation, the shared JSON Schema registry, `JsonShape`
and its restrictions, the schema→shape projection.

**Done when:** the full TCK runs with `unwrap=True, validate=True`; every
`*invalid*` fixture outside the skip list produces an error; every `*valid*`
fixture outside the skip list does not.

---

## Phase 9 — Hardening and release

**Build:** the benchmark suite and baselines, the CLI, the public API docs and
docstrings, `parse_lenient`, `re2` support, depth guards, the README.

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
2. **Multi-parent custom facet chain walk** — the known limitation in
   [10](10-validation.md) § 4.
3. **Union `enum` semantics** — spec § Union Type's enum rules, which the
   reference implementation also defers.
4. **Finer provenance granularity** — [08](08-templates-and-endpoints.md) § 6.4.
5. **Downstream packages** — LSP server, JSON Schema / OpenAPI converters,
   middleware. All are consumers of the model, not changes to it; `retain_source`
   and the `TypeExprRef`/`IncludeRef` indices exist so none of them requires a
   parser change.

## Risk register

| Risk | Where | Mitigation |
|------|-------|------------|
| Provenance is subtly wrong; types resolve in the wrong namespace | Phase 6 | The three-way golden; a test that asserts the *location* of every shape produced by a template, not just that it resolved |
| The two index walks (`collect_variables_index` / substitution) drift apart | Phase 6 | One shared walker; a fixture with nested sequences asserts they agree |
| Parent-shape mutation during multiple inheritance | Phase 4 | Explicit test: two children inherit one parent, assert the parent is byte-identical after |
| `RecursionError` on deep user input | Phases 4, 6, 8 | Depth guard + hypothesis property 10 |
| Performance regressions creep in unnoticed | all | Benchmarks in CI from Phase 9, baselines committed |
| Divergence from the reference on an ambiguous spec point | all | The cross-check script ([14](14-testing.md) § 1.3); every divergence resolved into a documented deviation or a bug |
