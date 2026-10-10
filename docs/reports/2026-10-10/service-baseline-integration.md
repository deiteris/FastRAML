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

## 3. Selective recovery

Pending acceptance of the production baseline. The first planned experiment is
model-backed declaration inlays and `type_written`, without record capture.
