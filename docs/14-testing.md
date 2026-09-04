# 14 — Testing strategy

Five layers, each answering a question the others cannot.

| Layer | Question | Size |
|-------|----------|------|
| Unit | does this function do what the doc says? | many, fast |
| Golden | does the whole model come out right for this input? | ~100 |
| TCK | do we agree with the spec's own compliance kit? | 967 fixtures |
| Property | do the algebraic laws hold on generated input? | ~10 properties |
| Benchmark | is it linear, and how fast? | 5 benches |

Runner: `pytest` for layers 1–4. Layer 5 is a standalone `bench/` package driven
by `python -m bench`, for the reason § 5 gives.

## 1. The TCK is the primary compliance gate

`raml-tck` is the RAML organisation's compliance kit. The reference
implementation vendors a lightly-customised copy, at
`C:\Sources\go-raml-main\raml-tck`.

pyRAML runs against **that same copy** rather than a second one, so a
disagreement between the two parsers is always a pyRAML bug or a documented
deviation, never a fixture difference. The harness locates it through
`PYRAML_TCK_DIR`, falling back to a sibling go-raml checkout; when neither is
present the TCK tests skip. Vendoring is deferred to Phase 9, when the
repository needs to build without a sibling checkout.

Convention (from its README):

- `*valid*.raml` → must parse, unwrap and validate without error;
- `*invalid*.raml` → must produce at least one error.

Current inventory: **496 valid** and **471 invalid** fixtures, 967 in total,
across Annotations, EdgeCases, Examples, Fragments, Libraries, MethodResponses,
Methods, Overlays, Resources, ResourceTypes, Responses, Root, SecuritySchemes,
TemplateFunctions, Types and spec-examples.

Note when counting these yourself: `invalid` contains `valid` as a substring, so
a `*valid*` glob returns all 967. The harness filters the negative fixtures out
of the positive set, and a test asserts the two sets do not overlap.

### 1.1 Harness

```python
@pytest.mark.parametrize("fixture", collect_tck("valid"), ids=str)
def test_tck_valid(fixture: Path) -> None:
    opts = TCK_SPECIAL_CASES.get(fixture.as_posix(), DEFAULT_OPTS)
    parse_from_path(fixture, opts)  # must not raise
```

```python
@pytest.mark.parametrize("fixture", collect_tck("invalid"), ids=str)
def test_tck_invalid(fixture: Path) -> None:
    with pytest.raises(RamlError):
        parse_from_path(fixture, DEFAULT_OPTS)
```

`DEFAULT_OPTS` is `ParseOptions(unwrap=True, validate=True)` — an invalid fixture
frequently only fails at validation.

Two facilities the reference harness needs and so will pyRAML:

- **Offline network fixtures.** `Root/include-02/valid-https.raml` includes a
  resource-type fragment over HTTPS. It is run with a stub HTTP client that
  serves a fixed body for that one URL and 404s everything else, so the HTTPS
  code path is exercised without network access.
- **A skip list with reasons.** Fixtures for unimplemented areas
  (`Overlays/`, `Extensions/`, XSD external types) are skipped **by category
  prefix**, with the reason in the skip message. Every skip is a line item in
  [15-implementation-plan.md](15-implementation-plan.md); the list only shrinks.

### 1.2 The ratchet

A JSON file records the currently-expected outcome per fixture. CI fails when:

- a fixture that passed now fails (regression), **or**
- a fixture that was expected to fail now passes and the file was not updated
  (progress must be recorded).

The ratchet makes coverage progress verifiable rather than asserted, and it lets
the full TCK run from the first commit instead of waiting for the parser to be
complete.

**A `fail` entry means work outstanding, and nothing else.** The list is not a
place to park a fixture we disagree with: a disagreement is either a bug in the
suite, which gets fixed there, or a bug here. Three cases have come up, and each
was fixed at its cause rather than recorded:

- `Annotations/target-locations/valid-response.raml` declared
  `allowedTargets: Method` while applying the annotation under a `200:` key —
  a copy of `valid-method.raml` with the site changed and the declaration left
  behind. The spec's Target Locations table defines `Response` as "a declaration
  of the responses node, whose key is an HTTP status code". **Fixed in the
  fixture.**
- `Fragments/namedexample-01/examples/{invalid-one-example,valid-multiple-examples}.raml`
  are `!include` targets, not documents; the first is invalid only in the
  context of its parent. Named by the `*valid*` convention, a name-driven
  harness picked them up as entry points. **Renamed in the suite**, includers
  updated.
- `EdgeCases/overlay-overrides-resources/valid.raml` is an Overlay filed outside
  `Overlays/`. **Fixed here**: `skip_reason` now also matches on the RAML header,
  so an Overlay or Extension is skipped wherever it sits. That caught ten more,
  four of which had been *passing* — an unsupported-fragment-kind rejection
  scoring as a correct rejection of an `invalid-` fixture, which is credit for
  the wrong reason.

Skipping by header rather than by path is why the corpus is 918 rather than 930.

### 1.3 Cross-checking against go-raml

A developer-only script runs `raml validate --json` from the reference
implementation and `pyraml validate --json` over the same fixture, then diffs the
trace chains. Each disagreement is triaged as a pyRAML bug, a go-raml bug, or a
documented deviation ([01](01-scope-and-coverage.md) § 4).

The script needs a Go toolchain, so it does not run in CI. It is the fastest way
to diagnose a TCK failure.

### 1.4 Differential conformance: the YAML 1.2 oracle

`tests/conformance` answers a question the TCK cannot: **does our YAML layer read
scalars the way YAML 1.2 says?** The TCK scores whether a document parses, not
what it parses to, so `example: 12:30:00` becoming the integer 45000 is invisible
to it — the fixture still passes.

The oracle is `ruamel.yaml` in YAML 1.2 mode (`typ='safe', pure=True`). Each
input is composed twice and the two node trees are compared on shape, tag and
text:

- a table of scalar forms in four syntactic positions — plain value, quoted
  value, mapping key, flow-sequence item — so a resolver change cannot fix one
  position and break another;
- structural documents covering includes, anchors, block scalars, flow
  collections and complex keys;
- **every** `.raml`/`.yaml`/`.yml` file in the TCK corpus, not only the entry
  documents: an included library is exactly where an odd scalar hides.

Rules that keep it honest:

- `KNOWN_DIVERGENCES` is keyed by reason, so an exception cannot be added without
  writing down why.
- `MAX_UNCOMPARABLE` bounds the documents neither side can compose. The TCK ships
  deliberately malformed fixtures, so it is not zero — but a change that quietly
  stopped comparing most of the corpus would otherwise look like a pass.
- Ruamel is a **dev dependency**. It never ships, and nothing outside this suite
  imports it. The pure-Python loader is required, not incidental: ruamel's C
  extension carries a pre-0.2.2 libyaml scanner that rejects
  `[ http://example.com ]`.

This suite covers resolution. The scanner half has no oracle; see
[12](12-performance.md) § 19 for the one known backend divergence.

## 2. Golden tests

For inputs where "no error" is too weak an assertion. Each case is a directory:

```
tests/golden/traits-merge-order/
  api.raml
  traits/paged.raml
  expected.json          # the serialised model
```

The model is serialised by a test-only walker that emits a stable, position-free
JSON projection (positions live in a separate `expected.pos.json` for the cases
that test positions specifically — otherwise a one-line edit churns every file).

`pytest --update-golden` regenerates. A regenerated golden must be read in the
diff before it is committed; the contributing guide says so.

The cases worth pinning as goldens are exactly the ones this design set argues
about:

- trait/resource-type merge order and the four priority classes;
- the provenance three-way example from [08](08-templates-and-endpoints.md) § 6.1;
- optional-method filtering and the deferred required-variable check;
- collection merge (`enum` union) producing `[mac, unix, win]`;
- unwrap of multiple inheritance, including the synthetic-shape aliasing hazard;
- union × inheritance expansion;
- recursion marking on self-referential objects, arrays and unions;
- default-type inference for every rule in spec § Determine Default Types;
- the `preference?` / `preference??` property-name corner cases;
- `securedBy: [null]` overriding an inherited scheme.

**Not built yet.** There is no `tests/golden/` and no `--update-golden`; the
harness lands with Phase 9. Until then the cases above are pinned as unit tests
where the phase that owns them has run — the trait/resource-type priority
classes, optional-method filtering, the collection merge, and the three-way
provenance example are in `tests/unit/test_traits.py`,
`test_resourcetypes.py` and `test_structural_merge.py`. A unit test asserts the
one thing it names; a golden asserts everything at once, which is why these
cases are still listed here.

## 3. Unit tests

Per-module, exercising the documented contract. The ones that matter most,
because they encode decisions rather than behaviour:

| Module | Pinned behaviour |
|--------|------------------|
| `yamlnode` | flat mapping content; identity hashing; 1-based positions; `!!timestamp` keeps raw text; duplicate-key detection; alias expansion budget |
| `uris` | Windows drive URIs; idempotent `path_to_file_uri`; backslash normalisation in references; `a/b/../c` == `a/c` |
| `loaders` | `SafeFileLoader` refuses `../` escape, symlink escape, non-regular files |
| `includes` | node cache hit count == 1 for N references; size limit off-by-one; scalar include for non-YAML extensions; circular scalar include |
| `references` | last-dot split; no namespace chaining; annotation→type fallback |
| `inference` | every rule and every conflict in § 4.2 of doc 05 |
| `expressions` | the full `rdt/examples.txt` corpus; cache identity; alias-vs-inherit discrimination |
| `structural_merge` | inputs unmutated; node identity preserved; opaque data facets not recursed; sequence dedup |
| `security` | one test per rejection rule of doc 09 § A2; the three inheritance levels; `securedBy: [null]` removing an inherited scheme; scope narrowing without touching the shared definition |
| `traits` / `resourcetypes` | the four priority classes; deduplication by name; optional-method filtering in both directions; which namespace a merged node resolves in |
| `templates` | the index survives a subtree being filtered out; all ten actions; the 618-row pluralization parity table generated from go-raml; unclosed `<<`; a substituted node is marked caller-scoped and a static one is not |
| `jsonschema` | a relative `$ref` resolves against the RAML file; an offline parse refuses a remote `$ref`; a shared `$ref` target is read once; a `$ref` inside a `default` is data; one test per error row of doc 10 § 6.3 |
| `inherit` | one test per row of the table in doc 07 § 3.5, both directions |
| `validate` | `bool` rejected as `integer`; `Fraction` exactness for `multipleOf: 1.1`; `uniqueItems` at n=20 and n=21 |
| `errors` | wrap/append composition; `to_dict()` shape matches the reference's |

## 4. Property-based tests

`hypothesis`, over a small generator of RAML documents. The laws:

1. **Idempotence** — `unwrap(unwrap(x)) == unwrap(x)`.
2. **Merge identity** — `merge(t, None) is t` and `merge(None, s) is s`.
3. **Merge target-wins** — every key in the target survives the merge with its
   own value or a merge of it; no target key is lost. Note what this does *not*
   say: a target mapping recurses and a target sequence unions, so only a target
   *scalar* keeps its value untouched.
4. **Merge purity** — a deep structural snapshot of both inputs is unchanged
   afterwards. Alongside it, **node identity**: every scalar in the result is one
   of the inputs' own objects, because a copied one would silently drop its
   provenance mark and the merge would still look correct.

Laws 2 to 4 are implemented in `tests/property/test_merge_laws.py`.
5. **Order preservation** — declaration order of properties, types, endpoints,
   methods and responses round-trips.
6. **Cache soundness** — parsing with a counting loader reads each file exactly
   once.
7. **Inheritance narrowing** — if `child` inherits `parent` and a value validates
   against `child`, it validates against `parent`. (Generated over scalar shapes
   with random compatible constraints.)
8. **Position sanity** — every entity's `key_pos` is within the file and
   `key_pos <= value_pos`.
9. **Determinism** — two parses of the same input produce identical golden
   projections.
10. **No `RecursionError`** — generated nesting up to `max_type_depth + 50`
    produces a positioned diagnostic, never a `RecursionError`.

## 5. Benchmarks

As specified in [12](12-performance.md) § Part 4, and built as `bench/`. They run
in CI on every PR with a generous threshold (fail at >25 % regression against the
stored baseline) and nightly with the full corpus and RSS measurement.

`bench_large`'s 7000-type corpus is **generated** by a script in the repo, not
vendored, so it stays a few kilobytes of source and can be regenerated at other
sizes to check linearity.

**Not `pytest-benchmark`.** Two of the three measurement decisions in
[12](12-performance.md) Part 4 are outside what it does: peak RSS needs a fresh
process per measurement, and corpus generation has to sit outside the timed
region rather than inside a fixture. A standalone runner does both and needs no
dev dependency.

Two things nevertheless run under `pytest`, because they are assertions rather
than measurements:

| File | Gate | When |
|------|------|------|
| `tests/bench/test_corpus.py` | every generated corpus is valid RAML in **all four** configurations, generation is deterministic, and `bench_large`'s diamond really does reach one `common.raml` | always; tiny scale, milliseconds |
| `tests/bench/test_linearity.py` | `bench_large` within 15 % of linear against a half-size corpus | `PYRAML_BENCH=1` only |

The first of those is not ceremony. The first draft of `write_small` had a
required property its own example omitted: `parse` and `unwrap` were happy, and
the two configurations that exercise P10 were quietly measuring an exception.

Absolute wall-clock is **not** asserted in any test. It is a property of the
machine; it is recorded in `bench/baselines.json` against a fingerprint
(interpreter, platform, YAML backend), and `python -m bench compare` declines to
compare across a fingerprint change rather than reporting a "regression" that is
really a different computer. CI records its own baseline per matrix cell.
Superlinearity is the exception, and is asserted: it means a cache is being
missed, which is a bug on every machine.

## 6. CI matrix

| Axis | Values |
|------|--------|
| Python | 3.12, 3.13 |
| OS | Linux, Windows (path/URI handling differs materially) |
| YAML backend | libyaml **and** pure-Python. Not optional: the two scanners already disagree on `title:<TAB>value` ([12](12-performance.md) § 19), so a libyaml-only run would ship that divergence. |
| Regex engine | `re` always; `re2` in one job |

Plus, on every PR: `ruff check`, `ruff format --check`, `mypy --strict pyraml/`,
and the TCK ratchet.

## 7. What is deliberately not tested

- The `xml:` facet beyond "it parses and round-trips" — nothing consumes it.
- Exact error *message text* outside the `errors` module; tests assert the
  message key and the `info` dict, so rewording a message does not break 50
  tests ([11](11-diagnostics.md) § 6).
- Third-party JSON Schema draft conformance — that is the schema library's
  test suite, not ours. pyRAML tests only the integration: shared registry,
  `$ref` through the sandboxed loader, and the shape projection.
