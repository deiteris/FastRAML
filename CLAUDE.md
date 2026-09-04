# pyRAML — project rules

A RAML 1.0 parser for Python 3.12+, modelled on go-raml. **`docs/` is normative.**
Start at `docs/README.md`; each document owns one area and states the decisions
that area has already settled.

## Before changing anything

1. Read the document that owns the area. `docs/02-architecture.md` maps areas to
   documents and to modules.
2. If the code must differ from a document, **amend the document in the same
   commit**. A doc that lies is worse than no doc.
3. Follow `docs/15-implementation-plan.md` phase order. It is dictated by
   dependencies, not by importance: the type system precedes endpoints, the merge
   precedes templates, validation comes last.

**Current state: Phases 0 to 8c complete; Phase 9 (hardening and release) is
next.** Phase 0: positions, errors, uris, loaders, yamlnode. Phase 1:
registry, fragments, includes, namespaces, datanode, facets, references,
annotations, the entry points and the pass driver (P0–P3).
Phase 2: the whole `types/` package — `BaseShape`, the seventeen kinds,
inference, examples, xml, and `make_shape`, wired into `types:`,
`annotationTypes:`, `baseUriParameters:` and the typed fragments. Phase 3:
`types/resolve.py` (P7) — the worklist drain and the type-expression visitor,
which share a module because they are mutually recursive. Phase 4:
`types/inherit.py` and `types/unwrap.py` (P9) — the clone operations, the
per-kind merge rules, and unwrap with recursion marking, behind
`ParseOptions(unwrap=True)`. Phase 4b: `resolve_domain_extensions` (P8) and
`DomainLocation`.

Phase 8a: `types/values.py` and `types/validate.py` (P10) — `check` and
`validate` on all seventeen kinds, examples, defaults, custom facets and
annotation values, behind `ParseOptions(validate=True)`. Phase 5: `directives`,
`source_ir`, `endpoints`, `source_decode`, `endpoint_build` (P4, P6) — the
two-stage endpoint build and URI parameter propagation. Phase 6:
`structural_merge`, `traits`, `resourcetypes` and the provenance overlay — the
spec's merging algorithm, the four trait priority classes, optional-method
filtering and resource-type chaining, with stage 2 decoding each merged body
under the scope its nodes were authored in. Phase 7: `security` (P5) — the six
scheme types and their settings, `describedBy` through the operation decoders,
`securedBy` inheritance and OAuth 2.0 scope narrowing.

Phase 8b: `types/jsonschema_.py` — `JsonShape`, the per-parse `SchemaRegistry`,
eager `$ref` resolution through `ResourceLoader`, and the projection of a
compiled schema onto the nearest RAML shape; plus the last eight conformance
rules the corpus was still measuring.

Phase 8c: facets written beside `type: A | B`, distributed to the members at P9
— the last conformance gap the corpus measured.

**Every pass P0–P10 runs, every RAML construct is decoded, and the TCK stands at
916 of 916** — every fixture outside the skip list does what its name promises.
No `_raw_*` attribute is a deferred seam any more; the two that remain are
working buffers within a single decode. What is left is Phase 9: benchmarks,
depth guards, `re2`, `parse_lenient`, the CLI and the public-API export list. A
brief per phase lives in `docs/briefs/`.

**A TCK `fail` entry means work outstanding and nothing else** (`docs/14` § 1.2).
Where a fixture is wrong, fix it in the suite — three have been, on branches in
the go-raml checkout, alongside `KNOWN-ISSUES.md` recording what that
implementation gets wrong. Never park a disagreement in the ratchet.

## The gate

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy pyraml/ && uv run pytest -q
```

All four must pass before any phase is reported done. `mypy` is strict for
`pyraml.*` and lenient for tests, by design.

## Invariants — breaking one is a bug, not a diagnostic

- Every `location` is a `file://` or `http(s)://` URI. OS paths exist only inside
  `loaders.py`.
- A file is composed at most once, and decoded at most once, per parse.
- Structural merge never mutates either input, and preserves node identity.
- After resolution no reachable shape is an `UnknownShape`; after unwrap every
  reachable shape has `unwrapped is True` and `link is None`.
- Declaration order is preserved everywhere the model is exposed.

Full list with the pass that establishes each: `docs/02-architecture.md` § 4.

## Rules that look like style but are load-bearing

- **`Node` defines no `__eq__`/`__hash__`.** The provenance overlay is
  `dict[Node, ParseCtx]` keyed by object identity. Do not add equality, and do
  not key overlays by `id()` — that neither keeps the node alive nor stays unique.
- **`__slots__` on every model class**; `@dataclass(slots=True, eq=False)` when a
  dataclass suits. A generated `__eq__` on a recursive model is a correctness
  hazard as well as a cost.
- **Never `copy.deepcopy`.** Use `clone(memo)` or `clone_detached()`
  (`docs/07-resolution-and-inheritance.md` § 5). A test asserts that no module
  in `pyraml/` imports the `copy` module at all.
- **A `facets:` block declares what *subtypes* must supply.** The chain walk in
  P10 starts at `inherits[0]`, so the declaring type neither has to satisfy its
  own required facets nor may supply a value for one — the latter is `unknown
  facet` (`docs/10` § 4). Nobody guesses this; it cost fifteen valid fixtures.
- **Where an annotation was applied rides `ParseCtx`, not a parameter.** A
  decoder that establishes a new site wraps itself in `Raml.target_scope(...)`;
  everything inside reads it, including the annotated-scalar form four dozen
  facet builders down (`docs/09` § B5). A missing scope is silent — it records
  the enclosing site — so a new application site needs a test that names it.
- **A shape's `location` and its `anchor`'s location may differ, and that is
  not a bug.** `location` is the file a node was authored in; the anchor is the
  namespace its *names* resolve in. A resource type in a library whose body
  reads `type: <<item>>` produces a shape located in the library and anchored at
  the applying document, because `<<item>>`'s value is a name the caller wrote
  (`docs/08` § 6.3). A corpus test that asserts they agree is the wrong test.
- **A template's variable index is keyed by node identity, never by position.**
  Optional-method filtering removes subtrees from the body between the scan and
  its use, so any numbering is stale by the time it is read. go-raml's is, and
  it fails the spec's own `corpResource`/`/queues` example both ways
  (`docs/08` § 7.1).
- **An alias shares its referent's containers on purpose**
  (`docs/07-resolution-and-inheritance.md` § 3.6) — one type under two names.
  An *inheritance* merge sharing the same containers is a corruption (§ 3.3).
  Do not "fix" the first into the second; the propagation is the feature.
- **Accumulate errors; do not fail fast** — except for an unreadable entry file,
  a missing or unrecognised RAML header, a non-mapping root, and a fragment whose
  kind does not match its context.
- **Numbers never pass through `float`, on either side of a comparison.** A
  facet's `Fraction` is built from the raw scalar text; a *value* is converted
  through its decimal text too (`Fraction(repr(v))`), because the YAML decoder
  already made it a float and `as_integer_ratio()` would recover the binary
  approximation. `multipleOf: 1.1` must accept `2.2`, and that is the test.
- **A `pattern:` facet is `fullmatch`; a `/regex/` property name is `search`.**
  The two look like one rule and are not: a facet *describes* a value, a pattern
  property is matched *against* a key it does not own, and `/^x/` is how those
  are written. Unifying them either way breaks a fixture
  (`docs/10-validation.md` § 5.4).
- **A discriminator is inherited, so the inline-declaration rule runs before
  P9.** After unwrap every subtype of a discriminated type carries one and looks
  inline. go-raml has the same rule as a `FIXME` for this reason. The check on
  *values* runs in P10 and outside the `strict` gate, because naming a type that
  does not exist is not a conformance failure an author may waive
  (`docs/05` § 9).
- **Read `Examples.entries()`, never `Examples.values`.** With `examples:
  !include e.raml` the examples live on the fragment and `values` is empty — so
  reading it directly does not fail, it silently sees nothing.
- **No per-character Python loops** where a compiled regex or a C-level string
  method will do. go-raml's byte loops are correct in Go and slow here
  (`docs/12-performance.md` § 12).
- Single quotes; `docs/` is excluded from ruff so illustrative code keeps its
  density.

## Working with the reference implementation

go-raml lives at `../go-raml-main`. The design documents already capture its
decisions, so **read targeted line ranges when you need a detail, not whole
files**. Reading it wholesale is what consumes a session's context.

Go is installed. When the question is what go-raml *does* rather than how it is
built, **run it** — `go test -run <name> .` against a throwaway `zz_*_test.go`
in that checkout, deleted afterwards. A trace of the code is a hypothesis, and
its comments have been wrong about its own behaviour.

## TCK

Fixtures are not vendored. They are found via `PYRAML_TCK_DIR`, falling back to
`../go-raml-main/raml-tck`; without either, TCK tests skip.

```bash
PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck -q
```

`tests/tck/ratchet.json` records the expected outcome per fixture. CI fails on
drift in either direction — a regression, or unrecorded progress. Regenerate with
`--update-ratchet` and read the diff before committing it.

## Testing

Pin decisions, not incidental behaviour. Assert on a diagnostic's message key and
`info` dict, never on assembled message text. Every documented corner case in
`docs/14-testing.md` § 2–3 gets a test that names the rule it protects.

Note that symlink-escape tests skip on Windows without Developer Mode. They are
the security-critical ones; trust CI's Linux job, not a local green run.

## Commits

One logical change per commit, present-tense subject with a `type:` prefix
(`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`). Work on a branch per
phase (`phase-1-fragments`); `master` holds completed phases.
