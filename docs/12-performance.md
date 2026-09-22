# 12 - Performance

## 1. Performance contracts

The parser must remain linear in the size of its input graph. In particular:

- A source file is composed at most once and a fragment is decoded at most once
  per parse. Caches use canonical URIs, so equivalent paths share an entry.
- Endpoint templates are structurally merged as YAML nodes and materialized once.
  The merge does not deep-copy child nodes and preserves their identity for the
  provenance overlay.
- Shapes awaiting resolution are recorded in a worklist. Passes use registry
  indices rather than repeatedly traversing the completed model to rediscover
  declarations, annotations, or includes.
- `copy.deepcopy` is not permitted. `BaseShape.clone(memo)` preserves graph
  structure; `clone_detached()` is for an isolated mutable copy.
- Declaration order uses ordinary `dict` insertion order. Do not add an ordered
  mapping wrapper.

These are correctness properties as well as performance properties. A cache miss
can duplicate declarations; copying a node can lose its provenance.

## 2. Hot-path rules

- Every model class is slotted. Dataclasses use `slots=True, eq=False` where
  applicable; identity-based nodes must not acquire generated equality.
- YAML mapping content is stored as a flat alternating list. Hot decoders should
  index it directly instead of allocating tuples or temporary dictionaries.
- Prefer compiled regular expressions and C-level string operations to
  per-character Python loops.
- Numeric validation keeps integer comparisons on the integer path and converts
  decimal values through text before using `Fraction`.
- Optional state stays optional: source retention, JSON Schema compilation,
  uncommon dependencies, and the pluralization dictionary are created only when
  requested.
- Regexes compiled by fastRAML use the selected `re` or `re2` engine. JSON Schema
  validation is external and is not controlled by `ParseOptions.regex_engine`.

## 3. Input depth

`ParseOptions.max_depth` is the one configurable ceiling for recursion driven by
user input. It is carried by `Raml.max_depth` and used by document composition,
type unwrap and recursion marking, and JSON Schema processing. Each guarded path
reports its own positioned diagnostic and includes the configured limit.

Do not replace a guard with reliance on Python's recursion limit. A user document
must receive a parser diagnostic rather than `RecursionError`.

## 4. Benchmark suite

`bench/` generates deterministic corpora and measures five workloads:

| Bench | Primary coverage |
|---|---|
| `small` | fixed parser overhead |
| `large` | large type/library graph and cache canonicalization |
| `endpoints` | template and endpoint construction |
| `validate` | declaration and example validation |
| `jsonschema` | shared JSON Schema references |

Each workload supports `parse`, `unwrap`, `validate`, `unwrap+validate`,
`unwrap+graph`, and `unwrap+lint`. Corpus generation is outside the timed region.
The small corpus-validity tests run in the ordinary test suite.

```bash
python -m bench run
python -m bench run --bench large --scale 0.5
python -m bench baseline
python -m bench compare
python -m bench linearity
python -m bench startup
```

Measurements run in fresh subprocesses. Wall time is the best of repeated
untraced runs; allocation tracing runs separately; RSS is a process high-water
mark. `bench/baselines.json` is fingerprinted by Python version, platform, and
YAML backend. A comparison across fingerprints is intentionally not a result.

## 5. Gates and local policy

CI does not compare absolute benchmark times or committed baselines. Its `bench`
job runs `tests/bench` with `FASTRAML_BENCH=1`, which asserts that `bench_large`
is within 15 percent of linear against a half-size corpus. Linearity is portable;
absolute duration and RSS are not.

Before and after a hot-path change, run `python -m bench compare` on the same
machine and inspect the result. Record a meaningful measured delta in the commit
message. Profile before optimizing: use `cProfile` for call counts, `tracemalloc`
for allocation attribution, and a wall-clock profiler for elapsed-time evidence.
