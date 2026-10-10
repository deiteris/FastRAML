# Service baseline and selective recovery plan

Status: accepted approach; execution started on 2026-10-10.

## 1. Objective and authority

This sequence follows the
[cost review](../../reports/2026-10-10/service-authoring-cost-review.md): land the
measurement infrastructure, validate and merge the simpler service branch if it
meets the acceptance goals, then recover parked changes in independently measured
bundles. The first deliverable is this plan so later work keeps the same goals.

The primary goal is normal edit-to-parser-diagnostics/occurrences readiness.
Authoring features must not impose a substantial recurring capture penalty on
every semantic rebuild. Source/query latency and retained memory are separate
acceptance decisions, not a weighted total that hides that penalty.

Numbered documents remain the current contracts. This plan records execution and
decisions; measured results belong in dated reports. The parked branch and its
original SHA remain available as evidence.

## 2. Starting state

- `master` and `origin/master`: `2b6502e` at the initial inspection.
- Simplified candidate: `refactor/service-source-simple`, `fb0fb73`, seven
  commits on the original master. Its handoff is untracked scratch and must not
  enter a commit.
- Parked representation replacement: `refactor/service-authoring`, `bc4b49d`.
- At the initial inspection, measurement/instruction work was uncommitted on the parked worktree.
  It contains two mixed workloads, reach tests, profiling instructions, the cost
  report and its numerical artifacts/driver. Production behavior is unchanged.

Recheck remote state before each integration. Preserve unrelated worktrees and
the parked implementation. Do not merge the parked representation replacement
into master as part of landing measurements.

## 3. Stage A: portable measurements first

1. Preserve the measurement work in its own logical commit and prepare an
   integration branch based on master, without the parked production changes.
2. Make historical drivers behavior-equivalent where public APIs differ. Master
   has text-based folding/selection; newer branches have workspace source caches.
   Exercise the real branch behavior rather than making an unavailable interface
   look like absent functionality. Reach tests must tolerate an uncached historical
   implementation while still proving the ordered requests and rebuilds occur.
3. Keep the report and no-capture ablation labeled as diagnostic evidence. No
   production parser option changes are part of this stage.
4. Reconcile the benchmark count and descriptions with the target tree; do not
   import parked-only contracts or obsolete source workloads into master.
5. Run the gate and mixed-workload linearity. Review the staged diff and all PR
   commits. Merge only this independent measurement/instruction change.

## 4. Stage B: accept the simpler production baseline

Integrate the measurement commit with the simplified branch and compare its
production changes against the measurement-only master control on identical
corpora. Record revision IDs and commands in a new dated acceptance report.

Required observations:

- Normal root edits: parse, collection, parser diagnostics and occurrences.
- Ordinary no-source parse/unwrap/validation, and relevant lint consumers.
- Both mixed orders: snapshot-first and source-before-snapshot, over multiple
  versions with request answers released before the next version.
- First outline, viewport hints and folding; warm requests; memory after
  diagnostics and after automatic queries, not just idle snapshots.
- Cache sharing, invalidation and failed composition, including an unchanged
  queried dependency and compatibility of snapshot text with current text.

Acceptance:

1. The routine rebuild path has no repeatable time regression beyond measured
   noise against the original relevant baseline. Diagnostic-only retained memory
   must preserve the candidate's improvement.
2. The new mixed workloads must demonstrate the benefit of the source cache and
   expose any newly retained duplicate owner. A favorable aggregate time does not
   silently accept a new first-use or memory regression.
3. Resolve bounded correctness/ownership blockers separately, with tests and
   measured effects. Each fix gets its own logical commit and owning-document
   update. Do not fold unrelated parked features into baseline acceptance.
4. If a material trade-off remains after those fixes, document it and stop at the
   decision rather than treating the old handoff as an acceptance record.
5. Pass the full gate and the relevant platform checks, including Linux-only
   loader/security cases through CI or the documented Docker run. Do not change
   fixtures or TCK expectations to obtain a green run.

After acceptance, merge the simpler branch through the repository's normal GitHub
flow. Record the accepted master SHA; it becomes the recovery comparison control.

## 5. Stage C: recover small bundles, not a blind bisect

The parked representation work is substantially bundled in one WIP commit.
Ordinary `git bisect` is not the primary method. Use controlled options and
isolated ports that answer a named question, preserving the original branch.

Start an experiment branch from the accepted baseline. Candidate order:

1. `type_written` and model-backed declaration inlays: avoid whole-file source
   grammar construction merely to identify explicit types.
2. Shared semantic/reverse-hierarchy indices, typed-data definitions independent
   of hover presentation, and outline caching.
3. Cursor-local primitive lookup and source text-offset checks, retaining include
   contexts and opaque-data correctness.
4. Accurate authored section ranges as a separately measured feature.

Check each candidate against changes already on simplified; do not duplicate
navigation, compatibility facts, visible names or postparse include handling.
Document expected work/calls and cache boundaries before profiling. Compare each
bundle against the accepted baseline, with reach, correctness and scaling checks.

Retaining record backing itself is a separate architectural decision. It must
earn its cost against the real scenarios, including full-value consumers, and
cannot become a prerequisite for unrelated useful features.

## 6. Stopping and execution record

Stop an experiment when its stated goal passes, or when new evidence requires a
new architectural/product decision. Reject a bundle that restores substantial
mandatory rebuild overhead, even if bulk hovers or one mixed order improves.
Do not optimize a high-call accessor before explaining its multiplicity.

Current execution:

- Plan written before integration or production changes.
- Stage A: measurement work preserved as `7c954ba` on the parked branch; the
  master-based `test/service-workload-baseline` port passes the Windows gate
  (5,810 passed, 67 skipped, 1 xfailed) and both mixed-workload linearity checks.
  All GitHub checks passed, including Ubuntu, TCK and benchmarks; PR #1 merged
  into master as `e81d361`.
- Stage B: all CI checks passed and PR #2 merged into master at `15aed45`.
  The remaining first-use and +5.4% post-query allocation trade-off is recorded
  in the integration report; the parked representation has not been merged.
- Stage C: `perf/service-model-inlays` starts from `15aed45`, with the first
   model-backed inlay/explicit-type experiment documented before implementation.
  The bounded port now has correctness, reach, A/B and scaling evidence in the
  [inlay report](../../reports/2026-10-10/service-model-inlays.md). Its snapshot-first
  mixed allocation peak adds 10.0%, an accepted construction-order trade-off.
  All CI checks passed; PR #3 merged at `3912118`.
- Stage C, second bundle: `perf/service-shared-indices` starts at `3912118`.
  Its lazy semantic/hierarchy, typed-data ownership and outline-cache scope is
  documented before implementation in the
  [shared-index report](../../reports/2026-10-10/service-shared-indices.md).
  The implemented bundle passes local correctness/reach/scaling checks and improves
  sparse navigation 17.0%; its 7.5% mixed retained-allocation increase is attributed
  mainly to the requested-file outline cache. The full bundle is accepted with
  that retention trade-off, subject to the normal CI/platform gate.
  Remaining candidates stay separate experiments against the integrated baseline.

Update these entries as each stage completes. Keep measurements in the dated
report, current pending work in docs/15, and execution history here.
