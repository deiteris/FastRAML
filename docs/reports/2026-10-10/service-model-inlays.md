# Model-backed declaration inlay experiment

Date: 2026-10-10. Branch: `perf/service-model-inlays`, starting at the accepted
baseline `bb6941fce593082a4b38a33f4b346dc35268a8d9`. Candidate measurements use
the working-tree changes on that branch. This is the first isolated experiment
in the [recovery plan](../../research/2026-10-10/service-recovery-plan.md).

## 1. Goal, workload and cache boundaries

The goal is to remove source composition and whole-file grammar construction from
declaration inlays while preserving normal rebuild cost. The planned scope and
acceptance criteria preceded implementation in the
[integration record](service-baseline-integration.md#31-model-backed-declaration-inlays).

The focused workload is a cold workspace and validated/unwrapped snapshot followed
by a whole-file inlay request. Its generated input is one 311,308-byte,
13,204-line file: 400 families, 2,000 named types, 400 annotation types,
400 resources and 7,200 shapes. The snapshot and hints remain live for retained
allocation. This establishes total cold inlay cost, not one already-parsed viewport
request. The two mixed workloads use the same input over three comment edits and
the ordered request sequence in docs/12 § 4.2. Their times cover all three cycles;
they are representative service-core scenarios, not observed editor traces.

The decoder records `BaseShape.type_written`; clones preserve it. Empty/null
declarations infer their kind, while an explicit `type:` or accepted `schema:`
field counts as authored even with a null value. The flag is an additional boolean
slot on every shape, with no source-tree owner or query cache populated by parsing.

On the first hover-index request, the existing authored-site population also keeps
per-URI subject references, selecting the first materialization at each site.
One placement check per shape detects expanded YAML aliases. A temporary visited-ID
set excludes their owned declaration descendants from inlays, without following
named-reference edges or suppressing the actual declaration at an anchor. This
walk only occurs for borrowed children and releases its set after construction.

The first inlay request for a URI builds and sorts declaration hints once. Later
ranges use the existing anchor index. The typed-data index still populates once
per snapshot on demand. Both caches die with that snapshot; a root edit rebuilds
them. Inlays compose no source and populate no source grammar/built-in-token index.
Folding or hover still populates the shared source owner on its own first use;
hover's grammar remains snapshot-local. The sharing/invalidation policy of the
accepted source cache is unchanged.

## 2. Correctness and reach

Tests cover explicit and inferred syntax, bare and explicit null/empty values,
multiple-inheritance sequences, both clone operations, included fragments,
synthetic fragment keys, keyless inline bodies and template materializations.
YAML aliases originating in opaque data are checked through nested object and
array declarations; a separate case preserves hints at a real declaration anchor.

The inlay benchmark reach test fails if `Hover._node` or `Hover._source_keys` is
called. It checks both timed and allocation passes at two corpus sizes, while
requiring inferred, typed-data and inherited-constraint labels. The mixed reach
tests still require one semantic and one shared query composition per version.
Removing inlay composition moves the query composition to folding; it does not
remove source work from a sequence that also asks for folding/hover.

The final Windows gate passes: Ruff checks and formatting, strict mypy, and
**5,861 passed, 68 skipped, 1 xfailed**, with the pinned TCK submodule initialized.
The focused inlay/decoder/corpus/mixed-reach suites also pass with the forced
pure-Python YAML plugin: **305 passed**. The accompanying pull request tracks
platform/CI verification, required before integration.

## 3. Controlled A/B results

Windows AMD64, Python 3.12, libyaml. `bench ab` used identical candidate-generated
inputs, three alternating rounds and the best of three untraced repetitions per
round. Allocation ran separately with the harness collector policy. A time change
within the reported round spread is not treated as an improvement or regression.

| Workload/configuration | Baseline ms | Candidate ms | Time/noise | Peak MB, baseline → candidate | Kept MB, baseline → candidate |
|---|---:|---:|---|---:|---:|
| `inlays/unwrap` | 347.1 | 251.9 | -27.4%, noise 4.9% | 30.022 → 19.569 (-34.8%) | 25.987 → 18.131 (-30.2%) |
| `service-session/unwrap` | 1320.9 | 1314.2 | within 4.5% noise | 30.621 → 33.691 (+10.0%) | 25.856 → 25.879 (+0.1%) |
| `service-source-first/unwrap` | 1379.3 | 1346.6 | within 4.9% noise | 28.115 → 28.137 (+0.1%) | 25.856 → 25.880 (+0.1%) |
| `large/parse` | 342.9 | 339.5 | within 9.2% noise | 20.073 → 20.233 (+0.8%, within allocation spread) | 19.608 → 19.767 (+0.8%, within allocation spread) |
| `large/unwrap+validate` | 391.6 | 407.7 | within 7.3% noise | 20.655 → 20.814 (+0.8%) | 19.714 → 19.873 (+0.8%) |
| `large/service` | 479.0 | 488.0 | within 13.0% noise | 35.643 → 35.803 (+0.4%) | 28.464 → 28.623 (+0.6%) |

The ordinary model-retaining rows expose about 159 KB of additional retained
allocation from adding the flag to shapes and their copies. In the inlay-only
scenario, avoiding source and grammar dominates that cost. When all mixed queries
eventually populate their indices, final retention is effectively unchanged.

Commands, from this worktree, with `TEMP` set to the approved temporary directory:

```powershell
$env:TEMP = 'C:\Users\Snek\AppData\Local\Temp\opencode'
uv run python -m bench ab bb6941f --bench inlays --bench service-session --bench service-source-first --config unwrap --rounds 3 --repeat 3
uv run python -m bench ab bb6941f --bench large --config parse --config unwrap+validate --config service --rounds 3 --repeat 3
uv run python -m bench linearity --bench inlays --bench service-session --bench service-source-first --repeat 9
```

## 4. First-use costs and the mixed peak

The existing unprofiled phase driver alternated three rounds of three requests.
Its baseline worktree is `refactor/service-source-simple` at `42c434e`, whose
`fastraml/` and `bench/` have an empty diff from `bb6941f`. Raw rounds, input
dimensions and counters are in [service-model-inlays-phase-metrics.json](service-model-inlays-phase-metrics.json).
The following are per-phase minima, potentially from different repetitions;
they must not be added into a claimed total or interpreted as an A/B noise test.

| Boundary in the snapshot-first sequence | Baseline ms | Candidate ms |
|---|---:|---:|
| Recurring edit through diagnostics/occurrences, best complete run | 184.9 | 189.8 |
| First 120-line viewport inlays, after outline/lenses | 142.0 | 50.3 |
| First fold, after inlays | 14.9 | 74.1 |
| First hover, after inlays/folding | 0.013 | 32.7 |
| Warm viewport inlays | 0.092 | 0.095 |
| Warm fold | 11.5 | 11.7 |
| Warm hover | 0.010 | 0.010 |

The best recurring totals differ by 2.7%, within the probe's 5.6% round spread.
The candidate's earlier inlay response leaves source composition to folding and
grammar construction to hover. A fast inlay alone is not a mixed-sequence speedup;
the controlled mixed A/B time remains within noise.

The allocation probe [service_inlay_allocation_probe.py](service_inlay_allocation_probe.py)
wraps the same mixed driver and records request-phase traced peaks with the
collector paused. It confirmed that the snapshot-first peak moves from the first
inlay request (30.61 MB baseline) to the first source/folding composition (33.68 MB
candidate). That composition begins with 17.68 MB already allocated for the
model, occurrences and lazy inlay/data caches, and peaks while the composer and
converted query tree coexist. In the baseline, composition precedes creation of
those inlay/data caches. This is a request-order allocation overlap, not a second
retained source owner. The probe's own rows add small diagnostic allocations, so
the ordinary A/B figures are the acceptance numbers.

The phase driver's post-collection retention after automatic queries is 25.832 MB
baseline versus 23.237 MB candidate; after sparse hovers it is 25.833 versus
25.864 MB. Its automatic-query boundary includes folding but excludes hover,
so the candidate has not yet populated grammar at that point. Source-first
requests compose before inlays, and their controlled peak is essentially unchanged.

Reproduce phases and attribution:

```powershell
uv run python docs/reports/2026-10-10/service_cost_probe.py --corpus hover --tree "baseline=C:\Sources\pyRAML\.claude\worktrees\service-source-simple" --tree "model-inlays=C:\Sources\pyRAML\.claude\worktrees\service-model-inlays" --rounds 3 --repeat 3 --output docs/reports/2026-10-10/service-model-inlays-phase-metrics.json
# Run this absolute driver path once with each comparison worktree as cwd.
uv run python C:/Sources/pyRAML/.claude/worktrees/service-model-inlays/docs/reports/2026-10-10/service_inlay_allocation_probe.py
```

## 5. Scaling and decision

Final linearity, best of nine, full versus half size:

| Workload | Full / half ms | Normalized time | Peak | Retained |
|---|---:|---:|---:|---:|
| `inlays` | 245.9 / 125.1 | 0.983 | 0.985 | 0.997 |
| `service-session` | 1296.5 / 662.6 | 0.978 | 0.991 | 0.998 |
| `service-source-first` | 1364.7 / 693.2 | 0.984 | 0.988 | 0.998 |

Earlier source-first runs failed with normalized time 1.876 at three repeats and
0.459 at five repeats, while allocation ratios stayed stable. Full/half timings
swung in opposite directions; the final higher-repeat run passes all three.
These failures remain part of the measurement record rather than being presented
as an uninterrupted green run.

Decision: retain this bounded recovery. It meets the model-only inlay goal,
improves cold inlay time/allocation, and does not show a repeatable recurring
rebuild penalty. After the shared-snapshot ownership and construction-order
explanation, the user explicitly accepted the **+10.0% snapshot-first mixed
peak**, about 3.07 MB on this corpus, and instructed integration on 2026-10-10:
“Ok, let's say this is acceptable, proceed”. This is acceptance of the measured
peak with the collector paused, not a claim of 10% higher steady editor memory.
Normal platform/CI verification remains the merge gate. No further optimization
is proposed here.
