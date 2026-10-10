# Lazy semantic, typed-data and outline recovery

Date: 2026-10-10. Experiment branch: `perf/service-shared-indices`. Control:
`39121181e5eaede7aa1d5c6566bd82aed32ff5e5`, the merged model-backed inlay recovery.
Scope and acceptance are written before implementation and profiling.

## 1. Goal and current repeated work

Remove repeated model enumeration for hierarchy/navigation and repeated outline
construction within a snapshot. Keep diagnostics-only rebuilds free of this work.
This is a bounded recovery of the second candidate in the
[recovery plan](../../research/2026-10-10/service-recovery-plan.md), using the
accepted plain-source cache. Record-backed source and accurate section sites are
separate candidates.

Source inspection of the control shows:

- `type_at` and hierarchy-item rebinding linearly enumerate declarations/shapes
  to find an occurrence target. Each subtype request scans all declarations again.
- Workspace symbols, hover subjects and each outline independently enumerate
  declarations; warm outlines reconstruct all symbols and authored-member lists.
- A typed-data definition fallback initializes the entire hover subject/site
  index before querying `DataHover`, even though it needs no formatted hover.
- Typed-data matching already exists in `types/navigation.py` and `DataHover`;
  recover ownership/separation, not another implementation of its rules.

The previous phase evidence measures a warm outline at about 30 ms on the
311,308-byte/13,204-line single-file corpus. New workload timing establishes the
navigation request sequence before implementation; counters check enumeration and
population separately from elapsed-time measurements.

## 2. Workloads and cache lifecycle

The existing snapshot-first and source-first mixed sequences cover two outlines
per version over three edits, plus viewport inlays, sparse hover and structural
requests. Results are released before the next version; the returned workspace
keeps only its current snapshot and caches. These are representative service-core
sequences, not observed client traces (docs/12 § 4.2).

Add `service-navigation/unwrap` over the same 400-family single-file corpus:
three comment-edit versions; diagnostics and occurrences; six fixed sparse type
preparations, supertype and subtype requests; three typed-data definitions before
hover presentation; two outlines and two workspace-symbol searches per version.
Probe selection/text generation is outside timing. Workspace creation, edits,
collection, snapshot construction and requests are inside. Discard request answers
and keep the current workspace. Request count is fixed as input width scales, so
the benchmark does not silently measure every type against every declaration.

Proposed ownership:

- Snapshot semantic object: lazy, with declaration enumeration/file grouping
  populated on demand and shared by symbols, outline and hover. ID lookup is a
  separately lazy map; reverse hierarchy is separately lazy, built once from
  declared `inherits`/`alias` edges. No hierarchy work merely for inlays/outline.
- Snapshot typed-data object: lazy, shared by definition, hover and inlays.
  Navigation may populate it without semantic presentation or source composition.
  Formatting remains owned by hover.
- Snapshot outline cache: canonical model URI → borrowed, read-only symbol list,
  including empty results. Populate on the first outline request for that file.
  Preserve current authored positions/ordering, including partial snapshots.
- All these caches are snapshot-local. Root or dependency changes replace them;
  unchanged source composition can still survive through the workspace owner.
  Several roots have distinct semantic contexts/caches. A held old snapshot keeps
  its own answers; no cache can return another version's outline or bound ID.

Expected work is one enumeration per populated cache boundary, then ID lookup,
direct-parent reads and child/output traversal per query. Reverse-edge construction
is proportional to declared shapes and parent edges, not shape pairs. Data visits
retain the existing finite-value/model traversal. Outlines still do one full
authored construction per requested file/snapshot; warm queries borrow its result.

## 3. Acceptance and verification plan

Protect lazy population, cross-query reuse, aliases/multiple inheritance and
declaration order; typed definitions before hover; empty/partial outlines; root and
dependency edits; old snapshots and separate roots. Meaningful reach tests must
cover both timed and allocation navigation passes and the unchanged mixed orders.

Compare against `3912118`: `service-navigation`, both mixed orders, hover, inlays
and `large/service`. Run linearity for the added workload and changed mixed
scenarios. Measure first and warm requests separately from diagnostics/rebuild,
and report peak/retained cache costs. No repeatable recurring rebuild regression
beyond noise is acceptable. Material cold-query or retained-memory trade-offs
require an explicit decision; a warm-query speedup does not hide them.

Run the complete Ruff/format/mypy/pytest gate and platform CI before integration.
Stop when this bounded goal is met; further source lookup work is a separate
experiment. Results and the acceptance decision will be appended below.

## 4. Implementation and correctness results

The semantic owner has separately lazy declaration/file, ID and reverse-parent
caches. Outlines, workspace symbols and hover share declaration enumeration.
Typed-data root enumeration moved to `datahover.py`; the snapshot owns one lazy
`DataHover` shared by definitions, hover and inlays. Definitions no longer create
hover presentation. Outlines cache complete read-only borrowed results by URI,
including empty results, without adding source-site capture.

A multiple-inheritance test exposed a pre-existing hierarchy limitation: scalar
parent wrappers were returned as list-token sites, and reverse queries missed the
named declarations they alias. The hierarchy reader now follows the wrapper's
bound alias; a genuinely declared alias remains a named item. This reads model
edges and does not resolve names or add a parser rule. Tests preserve direct
parent order and child declaration order, including aliases and multiple parents.

The sparse navigation reach test proves one declaration enumeration, one data
population and one outline construction per snapshot in both measurement passes.
It forbids hover presentation/source construction. Separate cases cover idle
diagnostics, cross-query data reuse, empty outlines, root/dependency invalidation,
old snapshots and distinct roots reading one library. The existing partial-model,
outline/TCK and inlay tests still pass.

Windows gate: Ruff, formatting, strict mypy and **5,876 passed, 69 skipped,
1 xfailed**, with the pinned TCK initialized. The added linearity test accounts
for the additional ordinary-suite skip when benchmarks are not enabled.
Focused cache, typed-data, inlay and navigation/mixed-reach suites also pass with
the forced pure-Python YAML plugin: **75 passed**.

## 5. Controlled costs and scaling

Control: `3912118`. Windows AMD64, Python 3.12, libyaml, identical generated
inputs. Three alternating A/B rounds, best of three, except the final mixed
comparison uses best of nine after substantial timing spread in the first run.
The navigation scenario's production-unchanged preimplementation run was 865.5 ms;
the controlled A/B is the acceptance measurement. Times cover three edit/query
cycles in the navigation and mixed rows, not one request.

| Workload/configuration | Control ms | Candidate ms | Time/noise | Peak MB, control → candidate | Kept MB, control → candidate |
|---|---:|---:|---|---:|---:|
| `service-navigation/unwrap` | 835.2 | 692.9 | -17.0%, noise 4.1% | 17.041 → 16.400 (-3.8%) | 14.770 → 15.191 (+2.9%) |
| `service-session/unwrap`, repeat 9 | 1326.5 | 1216.9 | -8.3%, noise 6.9% | 33.686 → 35.620 (+5.7%) | 25.874 → 27.808 (+7.5%) |
| `service-source-first/unwrap`, repeat 9 | 1350.0 | 1254.0 | within 19.4% noise | 28.132 → 29.094 (+3.4%) | 25.874 → 27.808 (+7.5%) |
| `hover/unwrap` | 330.5 | 345.7 | within 39.7% noise | 30.121 → 30.294 (+0.6%) | 26.425 → 26.599 (+0.7%) |
| `inlays/unwrap` | 253.9 | 258.0 | within 6.9% noise | 19.570 → 19.743 (+0.9%) | 18.132 → 18.305 (+1.0%) |
| `large/parse` | 339.5 | 339.4 | within 18.4% noise | 20.231 → 20.232 (within spread) | 19.766 → 19.767 (within spread) |
| `large/unwrap+validate` | 395.3 | 395.5 | within 4.7% noise | 20.814 → 20.814 | 19.873 → 19.873 |
| `large/service` | 550.2 | 512.1 | measured -6.9%, noise 4.3%; see limit below | 35.803 → 35.804 (+0.0%) | 28.623 → 28.624 (+0.0%) |

The last row establishes no rebuild regression in this run. It is not attributed
to semantic/query caching: the reach case proves those caches remain unpopulated
through diagnostics/occurrences, and parsing itself is unchanged. The first mixed
run had 71.4%/15.2% time spread and showed no accepted time delta; its allocation
deltas matched the final run. Timing spread also prevents a cold hover speed claim.

Final linearity, best of nine:

| Workload | Full / half ms | Normalized time | Peak | Retained |
|---|---:|---:|---:|---:|
| `service-navigation` | 698.0 / 353.0 | 0.989 | 0.994 | 0.996 |
| `service-session` | 1207.2 / 604.1 | 0.999 | 0.991 | 0.998 |
| `service-source-first` | 1238.3 / 633.1 | 0.978 | 0.989 | 0.998 |

Reproduce the controlled comparisons and scaling:

```powershell
uv run python -m bench ab 3912118 --bench service-navigation --bench service-session --bench service-source-first --config unwrap --rounds 3 --repeat 3
uv run python -m bench ab 3912118 --bench service-session --bench service-source-first --config unwrap --rounds 3 --repeat 9
uv run python -m bench ab 3912118 --bench hover --bench inlays --config unwrap --rounds 3 --repeat 3
uv run python -m bench ab 3912118 --bench large --config parse --config unwrap+validate --config service --rounds 3 --repeat 3
uv run python -m bench linearity --bench service-navigation --bench service-session --bench service-source-first --repeat 9
```

## 6. First-use and retained ownership

[Raw phase evidence](service-shared-indices-phase-metrics.json) comes from the
existing driver, alternating three rounds/best of three. The baseline worktree
is `perf/service-model-inlays` at `8e0ef3b`, production-identical to merged
`3912118`. Both retain zero authoring records and compose once semantically and
once for shared source per version. Timing spread is 60.2%/67.8%, so these phase
minima are diagnostic boundaries, not independent accepted A/B improvements.

First outline remains about 31.7/32.0 ms; warm outline changes from about 34.0 ms
to 0.0011 ms because it borrows the cached tree. Other first-use work remains lazy:
inlays populate model/data presentation, folding composes shared source, hover
populates grammar. The snapshot-first allocation peak still occurs during source
composition, now with the retained outline as well as inlay/data caches alive.

Post-collection phase-driver retention:

| Boundary | Control MB | Candidate MB |
|---|---:|---:|
| Snapshot | 9.683 | 9.681 |
| Diagnostics/occurrences | 12.041 | 12.038 |
| Automatic queries | 23.235 | 25.167 |
| Sparse hovers afterward | 25.862 | 27.793 |

The additional mixed retention is about 1.93 MB. A separate ownership probe
keeps the current workspace but releases only its cached outline after collection:
27.814 MB → 26.074 MB, a **1.740 MB outline-owned difference**. The remaining
increase is chiefly shared declaration/file metadata. This is genuine snapshot
retention, unlike the previous inlay experiment's temporary construction overlap.
It is released when the snapshot is no longer retained; distinct root contexts
may each retain an outline. The measured single-file scenarios do not establish
multi-root RSS or dependency-edit latency.

Reproduce phase evidence and the ownership check:

```powershell
uv run python docs/reports/2026-10-10/service_cost_probe.py --corpus hover --tree "baseline=../service-model-inlays" --tree "shared-indices=." --rounds 3 --repeat 3 --output docs/reports/2026-10-10/service-shared-indices-phase-metrics.json
uv run python docs/reports/2026-10-10/service_index_allocation_probe.py
```

## 7. Decision boundary

Correctness, reach and scaling pass. The sparse semantic workflow improves 17.0%
and snapshot-first mixed time improves 8.3%, while diagnostics-only retained cost
is effectively unchanged. The complete bundle costs **7.5% more retained mixed
allocation**, about 1.93 MB per current snapshot on this corpus, primarily its
requested-file outline tree. This is a retained-memory cost, separate from the
previous inlay experiment's temporary construction peak.

The full lazy semantic/hierarchy, typed-data and outline recovery is accepted
with the documented retention
trade-off. Normal CI/platform checks remain the merge gate. No unmeasured eviction
policy or source-record architecture is proposed to hide the cost.
