# 14 - Testing strategy

## 1. Test policy

Tests pin documented decisions rather than incidental implementation details.
Assert a diagnostic's message key and `info` data, not assembled prose. A change
to a documented corner case needs a named regression test.

The ordinary local gate is:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy fastraml/
uv run pytest -q
```

The test suite combines focused unit tests, generated-property tests, golden
projections, corpus invariants, YAML conformance tests, binding conformance, and
benchmark-corpus validity checks.

## 2. TCK

The RAML Test Compliance Kit is a submodule at `tests/tck/raml-tck`, from
`deiteris/raml-tck`. Initialize it with:

```bash
git submodule update --init
uv run pytest tests/tck -q
```

`FASTRAML_TCK_DIR` may point to another checkout. If neither location has a
populated suite, TCK tests skip.

The ratchet at `tests/tck/ratchet.json` records the expected outcome for every
evaluated fixture. CI rejects regressions and unrecorded progress. Regenerate an
intentional change with `uv run pytest tests/tck --update-ratchet`, then inspect
the ratchet diff. A `fail` outcome is reserved for work outstanding, not a suite
disagreement.

The current ratchet has 984 evaluated fixtures, all passing, Overlays and
Extensions included. Three named fixtures are skipped: two that require
network access, and one that conflicts with the documented JSON Schema
expression policy.

Each fixture is parsed with its case directory, `<category>/<case>/`, as the
workspace root. An Overlay in a subdirectory may extend `../base.raml`, which
the default root, the entry's own directory, would refuse
([19](19-overlays-and-extensions.md) § 2).

## 3. Test layers

| Layer | What it protects |
|---|---|
| Unit | Decoder, loader, resolver, type, validation, CLI, and view contracts. |
| Property | Structural merge laws and generated inheritance/validation laws. |
| Golden | Whole effective-model projections, including positions where relevant, and the rendered compatibility report for `examples/compatibility`. |
| Corpus invariants | Cache canonicalization, positions, determinism, unwrap invariants, rendering, graph/tree validity, and binding contract coverage over the TCK. |
| YAML conformance | YAML 1.2 scalar and structure agreement against `ruamel.yaml`. |
| Bindings | TypeScript, Python, and Go tree-contract conformance. |
| Benchmark tests | Generated corpus validity and the optional linearity assertion. |

Golden cases live in `tests/golden/cases/`, and rendered reports in
`tests/golden/reports/`. Regenerate only with
`pytest tests/golden --update-golden`, and review every resulting diff.

The YAML oracle is a development-only dependency. It compares composed node
structure, tags, and scalar text, including TCK files when the submodule exists.
Known scanner limitations belong in the scope-and-deviation document, not in the
exception list silently.

## 4. Invariants and mutation resistance

Tests assert the architecture invariants: URI locations, one compose/decode per
file, worklist resolution, no reachable unknown shape after resolution, flattened
unwrapped shapes, declaration order, merge purity, and node identity.

Tests that inspect a corpus must prove they inspected meaningful input. Where a
law could pass because its loop was empty or its comparison was vacuous, include a
test that exercises both sides or a targeted mutation check. Golden and projection
tests must include the object their case claims to protect.

The view boundary is also tested: parser and type modules do not import
`fastraml.views`, and outside that package only the CLI may do so. Binding tests
reject a projection key that is not declared in the shared tree contract.

`tests/unit/test_doc_refs.py` checks that every `docs/NN § S` reference in a
tracked file names an existing document and heading number, and that links
between numbered documents name an existing heading anchor.

## 5. Benchmarks

`bench/` is described in [12](12-performance.md). Small generated corpora are
validated in the ordinary suite. The machine-independent performance assertion is
linearity:

```bash
FASTRAML_BENCH=1 uv run pytest tests/bench -q
python -m bench linearity
```

Absolute time, allocations, and RSS are measured locally with the benchmark
harness. They are not pytest assertions and are not CI baseline gates.

## 6. CI coverage

GitHub Actions currently runs:

- ruff formatting/linting and strict mypy;
- the test suite on Linux and Windows with Python 3.12 and 3.13;
- the full suite forced to the pure-Python YAML backend;
- the full suite with optional extras, including `re2`, RDF/SPARQL, and the
  viewer;
- Go and TypeScript binding conformance with skips treated as failure;
- the linearity benchmark test;
- each independent consumer distribution and the viewer's own gate; and
- a dedicated submodule-backed TCK ratchet job.

The Linux sandbox tests are security-critical. Windows enables symlink creation
for its equivalent coverage, but Linux remains the environment that exercises
non-regular-file handling.
