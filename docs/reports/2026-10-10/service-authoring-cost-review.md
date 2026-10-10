# Service-authoring cost review

Date: 2026-10-10. This is measured decision evidence, not an accepted redesign.

## 1. Question and conclusion

The decision is whether to repair `refactor/service-authoring` or selectively port
its useful changes to `refactor/service-source-simple`. The user's priority is
the recurring edit-to-parser-diagnostics/occurrences cost: a substantial penalty
on every rebuild is unacceptable, even if later queries get faster.

The current branch fails that priority. Across three different corpora its
measured rebuild sequence is 34–44% slower than the simplified branch. Removing
only the workspace's capture request removes almost all of this difference.
Occurrence construction is not the main cause. The added work is largely
record-backed composition, source publication and collection of the larger
discarded snapshot.

This establishes a recoverable boundary, not an accepted one-line fix. With
capture disabled, the existing fallback makes the first large-file outline
265 ms instead of the simplified branch's 30 ms. Keeping the branch requires
fixing both the mandatory rebuild cost and the source-query construction path.
Selective porting avoids taking that entire representation replacement as a
prerequisite for the useful model/query improvements.

## 2. Revisions, methods and scope

- Parked implementation: `bc4b49d`, `refactor/service-authoring`.
- Simplified implementation: `fb0fb73`, `refactor/service-source-simple`.
- **No-capture**: `bc4b49d` with a diagnostic wrapper around the workspace's
  `parse_lenient` call that replaces only `projection=ProjectionRequest()` with
  `projection=None`. Retention, unwrap, validation, loader and query code stay the
  same. This is an ablation, not a production change or full correctness proof.
- Python 3.12.13, Windows AMD64, libyaml. Timed comparisons run in fresh
  subprocesses, sequentially, over the same generated input per comparison.
- Primary phase observations: three alternating rounds, three repeats per worker.
  Use the fastest repeat per round and the minimum round. Noise is the larger
  side's spread of round minima. Query rows report minima across these runs;
  their exact small differences are not separately accepted speedup claims.
- Coarse stage/composition/publication timers run separately from the primary
  uninstrumented timings. They are nested measurements, not additive profiler
  rankings. Node/accessor counters run in another untimed pass. No `cProfile`
  timings are used.
- Python allocation tracing runs separately from timings. Memory checkpoints
  collect with the current workspace/snapshot alive and discard query answers.
  Values use decimal MB, not MiB. Current Windows working set is measured without
  tracing in separate ten-version runs; it is not the A/B harness's peak RSS.

The driver is [service_cost_probe.py](service_cost_probe.py). Compact numerical
results for eleven runs, including phase minima, same-fastest-run phase breakdowns,
query costs, allocations, counts and RSS observations, are in
[service-cost-metrics.json](service-cost-metrics.json).

These are bounded service-core measurements. They exclude debounce, transport,
protocol position conversion/serialization and real client scheduling. The mixed
request order is representative, not an observed VS Code trace. The normal rebuild
scenario includes occurrences because that is the workload under review; parser
diagnostic publication alone does not force that index. Snapshot-only phase costs
also show the penalty before occurrence construction.

## 3. Inputs and operations

| Corpus | Files | Total bytes | Entry / queried file bytes | Model dimensions |
|---|---:|---:|---:|---|
| `large` | 152 | 576,354 | 3,930 / 3,850 | 7,000 generated library types plus common types; 19,904 registered shapes; 150 libraries and common/root documents |
| `hover` | 1 | 342,406 | 342,406 / 342,406 | 400 families; 7,200 shapes, 400 resources, typed facet/annotation values and unapplied traits |
| `endpoints` | 1 | 286,801 | 286,801 / 286,801 | 500 resources; 12,003 shapes; template and endpoint materialization |

These corpora come from the parked tree's generators. Unlike the simplified
branch's newer general corpora, these general inputs do not inject deprecated
spellings. Each comparison uses byte-identical input on all sides; the comparison
is not against separately generated branch-specific inputs.

**Normal rebuild.** A persistent workspace is warmed with an initial snapshot and
occurrences, then root-buffer comment edits are prepared outside timing. Each
timed operation changes the buffer, collects the discarded model, rebuilds the
root, reads parser diagnostics without lint, and builds occurrences. It requests
no source/query presentation. All unchanged libraries are still reparsed.

**Query phases.** A new workspace builds a snapshot and occurrences outside query
timing. It then requests an outline, lens enumeration, hints for lines 1–120,
folding, and a field hover; the same query kinds are repeated to observe warm
behavior. On `large`, only the first library is queried and opened as an unchanged
buffer. Query order is significant: the outline can populate source needed by a
later hover. Lens enumeration does not render effective types.

**Mixed sequence.** The new `service-session` workload runs three root versions
in one workspace. Each version requests parser diagnostics, occurrences, an
outline, links, lens enumeration, viewport hints and folding; then three sparse
hovers, a repeated outline/hint/folding request and eight selections. Old snapshot
locals and request answers are released before the next version. The result holds
the latest workspace/model/caches. `service-source-first` adds folding before each
snapshot. Their reach tests protect rebuilding, composition sharing and warm
query reuse, including both the timed and allocation passes.

## 4. The normal rebuild penalty

### 4.1 Uninstrumented total cost

| Corpus | Parked ms | Simplified ms | Parked penalty | Comparison noise | No-capture ms | No-capture vs simplified |
|---|---:|---:|---:|---:|---:|---|
| `large` | 634.6 | 472.3 | +34.4%, +162.3 ms | 13.2% | 467.7 | within noise |
| `hover` | 277.9 | 193.6 | +43.6%, +84.3 ms | 10.6% | 196.6 | within noise |
| `endpoints` | 458.1 | 336.0 | +36.3%, +122.0 ms | 0.9% | 342.7 | +2.0%, beyond 0.9% noise |

The endpoint ablation leaves a small repeatable difference. The experiment does
not attribute that residual to an individual earlier change. It does establish
that the large mandatory capture penalty is not needed to run the model/query
code retained on this branch.

### 4.2 Where the time goes

Coarse measurements below use the phase values from each side's fastest complete
instrumented run, not a sum of independently selected phase minima.

For `hover`:

| Boundary | Parked ms | Simplified ms | No-capture ms |
|---|---:|---:|---:|
| Change | 0.5 | 0.4 | 0.4 |
| Collection | 21.1 | 12.2 | 12.3 |
| Snapshot rebuild | 240.6 | 164.2 | 163.9 |
| Parser diagnostic extraction | <0.01 | <0.01 | <0.01 |
| Occurrence construction | 18.3 | 17.5 | 17.8 |
| Complete rebuild sequence | 280.6 | 194.3 | 194.4 |

Inside that snapshot rebuild:

| Nested boundary | Parked ms | Simplified ms | Interpretation |
|---|---:|---:|---|
| YAML composition itself | 35.8 | 35.3 | Little difference in this native/load portion |
| Composition including conversion | 88.9 | 60.6 | About 28 ms extra on the record-backed construction route |
| P0–P3 decoded stage, including composition | 132.3 | 101.1 | Contains the previous row; do not add both |
| Source publication after the passes | 44.0 | absent | Additional grammar/value-selection and publication work |
| Unwrap | 9.9 | 9.6 | Not the principal difference |
| Validation | 14.7 | 14.6 | Not the principal difference |

The extra composition, publication and collection account for most of the observed
84–86 ms difference on this corpus. This is a boundary-level attribution; it does
not establish a profitable individual accessor optimization.

The multi-file `large` coarse run confirms the same pattern: snapshot 531.5 versus
390.2 ms, source publication 74.6 ms versus absent, collection 41.1 versus 24.2 ms,
occurrences 53.4 versus 55.4 ms. Composition is 183.8 versus 118.0 ms and is nested
inside decoding. The native/load component there also changes with the allocation
schedule; do not interpret it as a change to libyaml's algorithm or add it to the
inclusive composition time.

### 4.3 What repeats, and where

The code boundary is `service/workspace.py:Workspace._parse`, which always supplies
a `ProjectionRequest` for service snapshots. `registry.py:Raml.compose_source`
selects the record-backed converter. `sourcecapture.py:ProjectionBuilder.__call__`
constructs canonical records and parser views. `parser/entry.py:_parse` invokes
`parser/projections.py:finish_projections` after the passes, including semantic
failure exits.

| Per snapshot | `large` | `hover` | `endpoints` |
|---|---:|---:|---:|
| YAML compositions, all three implementations | 152 | 1 | 1 |
| Converted source occurrences | 69,730 | 35,209 | 36,057 |
| Parked retained original-source records | 69,730 | 35,209 | 36,057 |
| Parked `_RecordNode.kind` reads during rebuild | 145,999 | 58,810 | 172,589 |
| Parked `SourceRecord.line` reads during rebuild | 18,759 | 14,804 | 44,031 |

Three root edits repeat the same counts on every version. On `large`, 151 unchanged
file inputs each compose three times across three versions; the root's three
different comment versions compose once each. There is no duplicate physical-file
composition within a single measured parse.

For 200 versus 400 hover families, converted nodes are 17,609 versus 35,209;
kind reads 29,410 versus 58,810; line reads 7,404 versus 14,804. Rebuild time is
141.3 versus 277.9 ms on the parked branch. First outline time is 65.1 versus
131.0 ms. These counts/costs are consistent with linear work at fixed depth.
They do not show a quadratic or per-path explosion in these scenarios. Large
call counts alone are not the defect: the source capability is populated at the
wrong mandatory boundary and repeated over all dependencies after every edit.

## 5. Memory: before queries and after them

### 5.1 Post-collection retained Python allocation

| Corpus/checkpoint | Parked MB | Simplified MB | No-capture MB |
|---|---:|---:|---:|
| `large`, snapshot only | 28.71 | 20.18 | 20.46 |
| `large`, diagnostics + occurrences | 36.85 | 28.33 | 28.60 |
| `large`, automatic queries on one library | 46.38 | 36.29 | 38.27 |
| `hover`, snapshot only | 14.61 | 10.40 | 10.16 |
| `hover`, diagnostics + occurrences | 17.23 | 13.02 | 12.78 |
| `hover`, automatic queries | 28.87 | 33.94 | 31.65 |
| `endpoints`, snapshot only | 22.19 | 17.59 | 17.43 |
| `endpoints`, diagnostics + occurrences | 23.61 | 19.01 | 18.85 |
| `endpoints`, automatic queries | 35.95 | 38.93 | 38.95 |

Three sparse field hovers after those automatic requests add less than 2 KiB at
these checkpoints. They do not construct a newly cold source index in that order.

The memory verdict depends on input shape and queried files. Capturing all 152
files is more expensive when only one small library is queried. On a monolithic
file, the simplified branch eventually retains two full source trees plus its
grammar/token stores, and the compact-record branch becomes smaller.

Peak allocation through automatic queries is 47.53 / 37.36 / 39.42 MB on `large`,
30.06 / 45.60 / 32.83 MB on `hover`, and 37.36 / 50.79 / 40.36 MB on `endpoints`
(parked / simplified / no-capture). These are separate allocation runs, not
instrumented elapsed-time comparisons.

### 5.2 Allocation attribution

A separate eight-frame `tracemalloc` pass over `large` with diagnostics/occurrences
alive attributes 8.144 MB to `views/occurrences.py` on every implementation.
That index is not the retained-memory difference either.

The parked pass attributes 6.618 MB to `sourceprojection.py` and 6.116 MB to
`sourcecapture.py`. This 12.734 MB is not all net extra memory: it replaces some
allocations formerly attributed to plain `yamlnode.py` and detached expressions
in `parser/entry.py`. Total retained allocation is 36.873 MB versus 28.626 MB for
the no-capture ablation, a net capture-associated difference of 8.247 MB.

Attribution means the innermost fastRAML allocation frame, not a proof that one
module exclusively owns every reachable byte. The source record counts and
capture ablation provide the corresponding structural/lifetime evidence.

### 5.3 Untraced current working set and old-version release

Separate ten-version Windows runs request diagnostics/occurrences on versions
1–5, then add outline, viewport hints, folding and sparse hovers on versions 6–10.
Request answers are discarded. Collection occurs before rebuilding and before
each observation; these runs check release/working set, not request latency.

| Corpus/state | Parked MB | Simplified MB | No-capture MB |
|---|---:|---:|---:|
| `large`, versions 2–5 | 84.1–84.8 | 74.4–77.2 | 74.9–77.4 |
| `large`, versions 7–10 after queries | 90.7–93.6 | 79.6–83.2 | 83.4–84.2 |
| `hover`, versions 2–5 | 68.9–70.4 | 60.1–63.2 | 60.1–61.9 |
| `hover`, versions 7–10 after queries | 78.9–80.2 | 87.7–89.7 | 82.3–84.0 |

All observed versions hold one cached service snapshot and two live `Raml`
objects. The hover run's warmed process baseline holds one `Raml` before a
workspace snapshot exists. Counts do not accumulate across the ten versions.
This bounded check gives no evidence of an accumulating old-model leak. It does
not prove indefinite steady state or explain every allocator/RSS fluctuation.

## 6. First-use queries and cache boundaries

### 6.1 Query costs after a current snapshot exists

The table uses the monolithic `hover` input; these costs exclude snapshot parsing
and occurrence construction.

| Query | Parked ms | Simplified ms | No-capture ms |
|---|---:|---:|---:|
| First outline | 131.0 | 29.8 | 265.2 |
| Lens enumeration | 1.27 | 1.33 | 1.54 |
| First 120-line inlay request | 47.9 | 154.3 | 47.3 |
| First folding request | 14.9 | 80.8 | 15.4 |
| Field hover after automatic requests | 0.044 | 0.014 | 0.041 |
| Warm outline | <0.001 | 30.3 | <0.001 |
| Warm inlay request | 0.089 | 0.093 | 0.088 |
| Warm folding | 13.6 | 13.4 | 13.4 |

Warm source is not free folding: it still traverses the structure and returns
ranges. Warm inlays still construct/return requested hints. The microsecond hover
differences are not the priority next to tens of milliseconds of mandatory work.

On `large`, a first inlay query for one small library still takes approximately
54–57 ms on all sides. The subject and typed-data indices span the semantic model;
a 120-line viewport does not limit their first population to those lines.

The first outline is a separate parked-branch cost. Coarse outline timers show
about 27 ms creating the cursor/start index and about 72 ms in recursive section
placement on the captured input. Those measurements overlap: placement can cause
cursor-index population. The no-capture fallback spends another approximately
124 ms obtaining/composing/publishing the source projection, and section placement
is approximately 84 ms. Moving capture out of parsing simply moves some of this
cost to the first outline.

The outlines do not have identical presentation capability: the parked branch
places real section keys and empty authored sections, while the simplified branch
uses model-only grouping/fallback spans. Thus the entire outline difference is
not an isolated representation regression under identical output requirements.
The accurate-source feature still needs its own acceptable construction path.

### 6.2 Owners, population and invalidation

| Structure | Owner/population | Reuse and invalidation observed |
|---|---|---|
| Parked captured source | Parse-owned `Raml.source_projections`; populated during every original composition and finalized after the passes | Shared by source queries within that snapshot; rebuilt for every dependency when the snapshot is dropped |
| Parked standalone folding/selection source | `Workspace._sources`; first source-only request for current input | A compatible semantic capture replaces it; source-first still composes before parsing, then parsing composes again |
| Parked semantic/authoring indices | Snapshot; semantic consumers and first hover/inlay/typed-data requests | Shared within the snapshot; root edits discard them even when a queried dependency is unchanged |
| Parked outline list | Snapshot + authored URI; first outline | The second outline in the same snapshot reuses the returned list |
| Simplified hover tree, grammar and built-ins | Snapshot's `Hover`, per queried URI; first hover/inlay | Composes and indexes the queried file; discarded with the snapshot |
| Simplified folding/selection tree | Workspace URI + text-object identity; first source-only request | Shares between folding/selection; persists for an unchanged open library across root edits; separate from the hover tree |
| No-capture source fallback | Snapshot's `SourceIndex`; first source-dependent query | Composes only the queried file; published/cached per snapshot; separate from an earlier generic standalone source |

The single-file composition counters verify these boundaries:

- Parked semantic-first: one composition per version, shared by later queries.
- Simplified semantic-first: three per version: parse, hover/inlay source and
  folding/selection source.
- No-capture semantic-first: two per version: parse and query fallback.
- Parked source-first: two per version, pinned by the reach test: standalone and
  original semantic capture. Replacement releases the old workspace owner, but
  does not reuse it as parser input.
- On the unchanged queried library across three root edits, simplified source
  requests add four compositions total: one workspace source population and one
  hover population per snapshot. No-capture adds three query-fallback compositions.
  The shared parse itself still composes all 152 files on every version.

On one unchanged broken buffer, each implementation's semantic parse attempts
composition once. Three subsequent `Workspace.source` requests compose zero
additional times on parked, three on simplified, and once on no-capture. The
simplified workspace's success-only source cache does not memoize failure.

## 7. Complete mixed sequences and unaffected paths

### 7.1 Mixed request results

The lower-noise diagnostic A/B over the tested scenario yields:

| Three-version sequence | Parked ms | Simplified ms | No-capture ms | Larger parked/simple time noise |
|---|---:|---:|---:|---:|
| Semantic snapshot first | 1,483.5 | 1,558.6 | 1,652.0 | 2.0% |
| Folding before each snapshot | 1,836.7 | 1,603.2 | 2,010.6 | 1.8% |

Parked is 4.8% faster on the first order and 14.6% slower on the second, both
outside their reported noise. No-capture is 6.0% slower than simplified on the
first order and 25.4% slower on the second. It is not an accepted mixed-use fix.

Retained allocation for semantic-first is 28.881 / 33.948 / 31.655 MB; peaks
30.066 / 45.602 / 32.840 MB. Source-first keeps 28.881 / 33.944 / 38.743 MB; peaks
30.185 / 39.948 / 39.929 MB. In the ablation, a generic standalone source can
remain beside the snapshot's expression-capable fallback.

An earlier standard `bench ab fb0fb73` run measured semantic-first 1,526.7 versus
1,596.6 ms inside 18.2% noise, and source-first 1,898.3 versus 1,713.5 ms (+10.8%,
noise 3.8%). The subsequent lower-noise run confirms the direction but does not
erase the earlier uncertainty. Both comparisons execute the same service scenario.

Both new workloads pass their actual `unwrap` linearity gate: normalized time
0.988 and 0.982, peak 0.998 and 0.999, retained 0.997 and 0.997, respectively.
This checks scaling, not acceptance of the rebuild penalty.

### 7.2 Ordinary parsing and full-retention consumers

Standard A/B against `fb0fb73`, three rounds/best of three, using the same corpus:

| Workload | Simplified ms | Parked ms | Time verdict/noise | Peak delta | Kept delta |
|---|---:|---:|---|---:|---:|
| `large/parse` | 316.3 | 319.1 | within 2.7% noise | +1.4% | +1.5% |
| `large/unwrap+validate` | 395.5 | 400.6 | within 2.1% noise | +1.4% | +1.5% |
| `large/unwrap+lint` | 619.1 | 739.2 | +19.4%, noise 2.5% | +2.7% | within noise |
| `endpoints/parse` | 265.9 | 270.8 | within 3.1% noise | +0.4% | +0.8% |
| `endpoints/unwrap+validate` | 297.2 | 304.0 | +2.3%, noise 2.1% | +0.4% | +0.7% |
| `endpoints/unwrap+lint` | 633.7 | 739.7 | within 23.2% noise | +1.1% | within noise |

The ordinary no-source path does not pay the large capture penalty. Full retention
does use the new record/read-facade machinery even without a projection request.
The bench's `unwrap+lint` configuration explicitly retains full source; it is not
evidence that default service lint needs that retention. Its result retains lint
findings rather than the complete model, so its small kept total does not measure
full-source storage.

Earlier direct bulk comparisons from this investigation measured parked versus
simplified: cold hover 363.1 / 332.6 ms (noise 1.7%), whole-file inlays 316.4 /
350.8 ms (noise 3.4%), effective rendering 261.6 / 205.8 ms (noise 4.8%), and
navigation 305.9 / 417.6 ms (noise 2.2%). These compare entire branch bundles;
they do not attribute navigation's gain solely to one shared-index change.

## 8. Decision criteria

**Reject the current mandatory capture path as-is.** Its cost recurs on every
service rebuild, is observable even without source queries, and makes the
multi-file case larger both before and after querying one library. A favorable
bulk/mixed row does not satisfy the user's separate diagnostic-latency requirement.

**Keeping and fixing the branch is technically plausible**, because an existing
option boundary restores rebuild performance close to simplified without throwing
away the semantic/query code. Acceptance would still require:

1. Recover the normal rebuild path against the simplified and original relevant
   controls; investigate the endpoint residual rather than declaring all costs
   eliminated. Preserve ordinary no-source parsing and full-value consumers.
2. Give source its appropriate file/input lifetime and eliminate duplicate
   generic/semantic source ownership where compatible. Merely moving all capture
   to the first query is insufficient.
3. Fix or redesign first-outline/source-site construction while preserving the
   accurate authored spans that the feature adds. The observed scaling permits
   constant-factor work, but only after the ownership/population boundary is right.
4. Measure both request orders, library/dependency changes and multiple roots.
   Maintain separate readiness budgets for diagnostics, outline and hints instead
   of accepting only a sum that trades one delay for another.

**Selective porting is the lower-scope route** if repairing that representation
and query boundary is not a product goal now. Candidate bundles include:

- `type_written` and model-backed declaration inlays, avoiding a source grammar
  index merely to determine whether a type was explicit;
- shared semantic/reverse-hierarchy indices, cached outlines, and typed-data
  definitions independent of hover presentation;
- cursor-local source-only primitive lookup and text-offset checks, with their
  receiving-context and opaque-data correctness cases;
- accurate authored section ranges as a separate feature with its own workload,
  rather than making the record-backed parser a prerequisite.

Some representation-independent work is already on simplified: typed-value
navigation, syntax-alias facts, visible names and postparse include handling.
Do not port it twice. Port candidates need isolated A/B evidence; the whole-branch
navigation/inlay improvements do not guarantee each candidate has the same gain.

The measurements support making mandatory capture optional/redesigning its
boundary or selecting smaller bundles. They do not support trying to rescue the
current acceptance result by optimizing an arbitrary high-call accessor.

## 9. Reproduction and verification

Run from the parked worktree with its environment. Set `TEMP` to an existing
scratch directory; corpora and workers are disposable. For each corpus, use:

```powershell
uv run python docs/reports/2026-10-10/service_cost_probe.py --corpus large --rounds 3 --repeat 3 --tree 'parked=C:\Sources\pyRAML' --tree 'simple=C:\Sources\pyRAML\.claude\worktrees\service-source-simple' --tree 'no-capture=C:\Sources\pyRAML' --output large.json
```

Replace `large` with `hover` or `endpoints`. Add `--detail` for the separate coarse
timers, `--scale 0.5` for the half-size observation, `--ownership --rounds 1` for
allocation attribution, or `--rss-edits 10 --rounds 1` for the untraced retention
probe. `--mixed` selects the tested three-version sequence; `--source-first` changes
its request order. Summarize raw artifacts with `--summarize FILE... --output FILE`.

Standard benchmark commands:

```powershell
uv run python -m bench ab fb0fb73 --bench service-session --bench service-source-first --config unwrap --rounds 3 --repeat 3
uv run python -m bench linearity --bench service-session --bench service-source-first --repeat 3
uv run python -m bench ab fb0fb73 --bench large --bench endpoints --config parse --config unwrap+validate --config unwrap+lint --rounds 3 --repeat 3
```

The Windows gate passed after adding the scenarios and reach tests: Ruff,
formatting, strict mypy and pytest, **6,202 passed, 48 skipped, 1 xfailed**.
Production parser/service behavior was not changed by this investigation.

Remaining evidence limits: no observed client trace, multi-root or dependency-edit
timing, join/postparse full-value workload, or Linux timing/RSS measurements. The
capture ablation's query assertions cover these corpora, not the full public API,
TCK and error-recovery contract under a permanent option change. Noise limits
individual phase/query comparisons; no phase minima are summed to manufacture
an end-to-end result.
