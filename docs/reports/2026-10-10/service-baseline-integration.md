# Service baseline integration record

Date: 2026-10-10. Execution of the
[accepted recovery plan](../../research/2026-10-10/service-recovery-plan.md).

## 1. Measurement-only control

Base: `master` / `origin/master` at `2b6502e`. Branch:
`test/service-workload-baseline`. The measurement work is preserved on the parked
branch in `7c954ba`; this port includes its two mixed scenarios, reach tests,
profiling guidance, plan and dated evidence, without parked production changes.

The portable driver uses each revision's service interface. On original master,
folding and selection call the text-based query functions, as its LSP adapter
does. On newer revisions they use the workspace's source cache. Repeated outlines
are exercised whether or not a revision caches them; cache identity is checked
only where the implementation provides it. The suite has 32 workloads here,
not the parked implementation's 40.

Windows gate: Ruff, formatting, strict mypy and pytest pass, with **5,810 passed,
67 skipped, 1 xfailed**. The TCK is initialized at the original pinned submodule
revision. Local Docker's Linux engine is unavailable; GitHub CI must supply the
Linux/security verification before integration.

Linearity, three repeats, actual `unwrap` mixed scenarios:

| Workload | Full / half ms | Normalized time | Peak | Retained |
|---|---:|---:|---:|---:|
| `service-session` | 2,952.0 / 1,486.6 | 0.993 | 0.991 | 0.997 |
| `service-source-first` | 3,147.7 / 1,596.9 | 0.986 | 0.991 | 0.997 |

This verifies the historical uncached behavior is reached and scales. It does
not establish acceptance of the simplified production candidate.

All GitHub CI checks subsequently passed, including Ubuntu 3.12/3.13, TCK,
optional extras, pure-Python YAML and the benchmark gate. Measurement-only
[PR #1](https://github.com/deiteris/FastRAML/pull/1) merged into master at
`d2b8a02fc7b3e7cb462906bd998d15b649296348`.

## 2. Simplified candidate

Starting candidate: `fb0fb73`, with the measurement-only commit integrated locally.
The production implementation is unchanged in this first comparison. Control:
`549af67` (the production-equivalent measurement parent of merged master
`d2b8a02`). Three alternating rounds, best of three, identical candidate-generated
corpora:

The mixed corpus is one **311,308-byte (304.012 KiB), 13,204-line** RAML file:
400 families, 2,000 named types, 400 annotation types and 400 resources. Each
reported time covers **three complete edit/query cycles**, not one parse or one
editor request. Each cycle includes two outlines, two viewport inlay requests,
two folding requests, three hovers, eight selection positions, links, lens
enumeration, diagnostics and occurrences. The source-first case adds one folding
request before each snapshot. These request counts are a cache/reuse scenario,
not a measured client request trace.

Original master recomposes for both folds and every selection: 11 compositions
per version, 33 over the snapshot-first sequence. The simpler candidate composes
three times per version (parse, hover/inlays and workspace source), nine overall.
Thus the aggregate is deliberately sensitive to repeated uncached source work;
it must not be quoted as individual interactive-request latency.

| Workload (`unwrap`) | Control ms | Candidate ms | Time/noise | Peak | Kept |
|---|---:|---:|---|---:|---:|
| `service-session` | 3,108.7 | 1,560.2 | -49.8%, noise 4.4% | +2.1% | +26.8% |
| `service-source-first` | 3,304.7 | 1,561.5 | -52.7%, noise 18.9% | -10.9% | +26.9% |

Kept allocation is 24.519 versus 31.097 MB on the first order. The mixed sequence
therefore does not yet accept the simpler candidate's memory ownership: the
hover index and workspace each retain a full tree of the same current input.
The local gate passes after measurement integration: 5,824 passed, 68 skipped,
1 xfailed.

### 2.1 Bounded acceptance fix: one composed-source owner

Before further ports, share a queried file's plain `Node` tree between the
workspace's folding/selection requests and snapshot hover/inlay consumers, when
URI, normalized input, depth limit and YAML backend agree. Failed composition is
also a cached result. Retained full-source snapshots can offer their original
tree on demand; text-only snapshots still parse without retaining all producer
trees or building authoring records.

The workspace owns the source cache. Snapshots borrow it weakly, so holding an old
snapshot does not keep the entire workspace/cache alive. Snapshot-local hover
nodes retain the requested subtree they need. Input invalidation advances a cache
generation; an old snapshot may read an identical ready entry but must not publish
a stale cache miss into the current workspace. This needs no per-file registry
of unused authoring structures during parsing.

Tests must protect both request orders, failed composition, limits/backend
compatibility, unchanged dependencies, old snapshots and source-cache release.
Measure mixed requests and normal rebuilds separately after the fix. Remaining
first-use trade-offs still require their own acceptance decision.

### 2.2 Shared-source results and acceptance

The shared-source implementation supplies one on-demand plain tree to both
consumer groups. Invalidations track changes only for read/cached URIs, so an
unrelated watched file does not prevent a current snapshot from publishing a
cache miss. Policy changes invalidate publication separately. Fourteen focused
cases protect the named ownership/failure/compatibility decisions; the mixed
reach test requires six compositions over three versions in either order
(one semantic and one query composition per version).

Isolated A/B against the measurement-integrated candidate `94ebc60`:

| Workload | Time/noise | Peak | Kept |
|---|---|---:|---:|
| `service-session` | -12.5%, noise 2.8% | -26.5% | -16.9% |
| `service-source-first` | within 22.0% noise | -22.7% | -16.9% |

Final master control is `d2b8a02`. Three rounds/best of three, identical inputs:

| Workload/configuration | Master ms | Shared-source candidate ms | Time/noise | Peak | Kept |
|---|---:|---:|---|---:|---:|
| `large/service` | 451.0 | 454.9 | within 12.0% noise | -20.1% | -24.0% |
| `service-session/unwrap` | 3,078.7 | 1,307.7 | -57.5%, noise 8.3% | -25.0% | +5.4% |
| `service-source-first/unwrap` | 3,352.6 | 1,377.8 | -58.9%, noise 8.3% | -31.1% | +5.4% |
| `hover/unwrap` | 268.1 | 331.6 | +23.7%, noise 2.4% | +16.6% | +4.1% |
| `inlays/unwrap` | 279.3 | 343.7 | +23.0%, noise 12.3% | +13.0% | +3.4% |
| `effective-types/unwrap` | 209.2 | 208.9 | within 2.8% noise | +0.0% | -27.4% |

The mixed candidate keeps 25.851 MB versus master's 24.521 MB after requests,
instead of the original candidate's 31.097 MB. There is no longer a second retained
full tree for these consumers. The remaining allocation difference is not claimed
to have been attributed exclusively to one model field or position store.

`large` and `endpoints`, on `parse`, `unwrap+validate` and `unwrap+lint`, remain
within time noise (0.8–3.4%). Ordinary model-retaining rows add 0.5–0.8% peak and
0.7–0.8% kept allocation; full lint peaks add 0.1–0.2%, with findings-only retained
allocation within noise. These corpora include the candidate's deprecated
spellings on both sides.

The Windows gate passes: **5,838 passed, 68 skipped, 1 xfailed**. Linearity passes
for `source-structure`, `service-session` and `service-source-first`: normalized
time 1.008 / 1.118 / 1.004, peak 0.998 / 0.993 / 0.984, kept 1.000 / 0.998 / 0.998.

After being shown the remaining cold-query cost and +5.4% post-query retained
allocation, the user explicitly selected **“Merge, then recover”**. Acceptance is
therefore against the primary rebuild goal, improved complete mixed sequences,
lower diagnostic/peak memory and corrected ownership, with the lazy first-use
trade-off recorded rather than hidden. Linux/CI verification is still required
before the production merge. Model-backed inlays are the first recovery experiment
against that accepted baseline, without accepting record capture.

## 3. Selective recovery

Pending acceptance of the production baseline. The first planned experiment is
model-backed declaration inlays and `type_written`, without record capture.
