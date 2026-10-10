# 15 - Status and roadmap

This document is non-normative. Current parser behavior is defined by docs 01-14
and 16-18. The completed implementation plan is retained in
[archive/implementation-history.md](archive/implementation-history.md).

## 1. Status

The RAML 1.0 parser pipeline, type system, endpoint construction, template merge,
security, validation, JSON Schema support, CLI, benchmarks, and view layer are
implemented. Current supported behavior and deliberate deviations are recorded in
[01](01-scope-and-coverage.md).

## 2. Deferred work

Overlays and Extensions are implemented for an entry document
([19](19-overlays-and-extensions.md)). Applying several extension documents
that each extend the same master remains deferred (docs/19 § 7).

XML Schema external types are unsupported ([01](01-scope-and-coverage.md) § 3).

**Service baseline acceptance and recovery.** The simpler source-cache branch
passes the master-controlled rebuild and mixed-request checks after sharing its
query source owner. Its documented first-use trade-off is accepted;
all CI checks passed and the baseline is merged. The first isolated recovery
experiment implements model-backed declaration inlays against that accepted
control. Its correctness, scaling and A/B results are recorded in the
[inlay recovery report](reports/2026-10-10/service-model-inlays.md), including its
accepted snapshot-first mixed allocation-peak trade-off.
The shared-index/outline bundle is the next isolated recovery against merged
`3912118`. Correctness, reach and scaling pass; sparse navigation is 17.0% faster,
but mixed retained allocation rises 7.5%, chiefly the cached outline. An explicit
acceptance of that new retention trade-off is recorded; the scope, measurements and
ownership evidence are in the [shared-index report](reports/2026-10-10/service-shared-indices.md).
Cursor-local source lookup replaces hover's whole-file key index
([cursor hover report](reports/2026-10-11/service-cursor-hover.md)). Accurate
authored section ranges remain, measured independently.
The record-backed representation replacement remains parked; its recurring
rebuild and first-use costs are recorded in the
[service cost review](reports/2026-10-10/service-authoring-cost-review.md).
The accepted [baseline and recovery plan](research/2026-10-10/service-recovery-plan.md)
lands portable measurements first, checks the simpler branch against master, and
then evaluates isolated ports against the accepted baseline.

`fastraml join` ([20](20-join.md)) accepts API documents only; Overlays,
Extensions and Libraries as inputs, and renaming to resolve a conflict, are not
covered (docs/20 § 11).

**Description links ([16](16-graph.md) § 11).** Resolved for lint, the tree,
the viewer and the Sphinx extension. Deferred:

- Language service navigation from a link in a description: definition, hover
  and references through a new occurrence role. It needs a link's exact span,
  and the composer keeps no scalar style, so an offset into a block, folded or
  escaped scalar cannot be mapped to a column yet (docs/11 § 3).
- Responses, properties, traits, resource types and built-in types as
  targets. The viewer has no page for most of them.
- `fastraml convert openapi` copies a description verbatim, links included.

**Unwrap results depend on declaration order within a type cycle (known
bug).** P9 walks types depth-first, so inside a cycle it can reach a type
whose merge has not finished, and an alias, subtype or union member reached
then takes that type's fields as they stand. With `Base {maxProperties: 2,
b}`, `Parent {type: Base, child?: Child}` and `Child {type: Parent}`, whether
`Child` gets `b` and `maxProperties` depends on which is declared first. It is
present since P9 was written, and predates the union-variant fold. Pinned by
the strict xfail `test_a_type_cycle_unwraps_alike_in_every_declaration_order`
in `tests/unit/test_unwrap.py`. To be redesigned: merge in topological order
of inheritance and alias edges, with property, item and member types handled
as references rather than walked into, so a type is merged only after
everything it inherits from (docs/07 § 6).

**Accepted benchmark regressions (to revisit).** Measured with `bench ab`
against `aab7a4a` when the JSON Schema module split, the model-memory work,
the media-type fixes and the type-walk work landed together:

- `large/validate`: peak +2.4% and time +3.8%. P10 memoizes `check()` per
  shape for the whole pass (`checks_memoized`); clearing it per declaration
  is the likely fix.
- `endpoints`: memory kept after the parse +1.8%. Likely the `WrittenScalar`
  record that replaces `type_expr` after P7, kept beside YAML nodes the
  endpoint build still holds; skip the swap where the node is retained anyway.
- `validate`: time up to +4.9% on some configurations in a 3-round run; a
  5-round rerun of `unwrap+validate` was within noise.
- Text-only service snapshots pay an on-demand composition for a queried input
  unless a compatible source entry is already ready (docs/21 § 4). The shared
  owner removes the old duplicate hover/structural tree. The accepted baseline's
  remaining first-use cost and post-query allocation trade-off are recorded in the
  [integration report](reports/2026-10-10/service-baseline-integration.md).
  Model-backed declaration inlays remove that first-use work from hints alone;
  their accepted request-order peak trade-off is recorded in the inlay recovery
  report. Hover reads source keys along the cursor's path and keeps no index of
  them.

## 3. Potential future work

Potential tooling includes more editor recovery in `parse_lenient` and
improved remote-include latency. These are not commitments and must not change
parser rules without an owning design document and tests.

The language service and its LSP are built ([21](21-language-service.md)).
The ordered plan for the rest, completion, editing features and MCP, is
[research/language-service-plan.md](research/language-service-plan.md): M1 to
M4 are done, and completion (M5) comes next, with its prerequisites in: the
resolver's visible-names enumeration (docs/04 § 2) and the workspace's
composed source cache (docs/21 § 4). Its design records are
[archive/language-server.md](archive/language-server.md) and
[archive/language-service-architecture.md](archive/language-service-architecture.md);
its parser prerequisite, a trustworthy `parse_lenient` model, is analysed in
[research/partial-models.md](research/partial-models.md).

**Sampled examples in the tree (undecided).** The viewer's request and response
panel (parked on `feat/viewer-request-samples`) needs a working body for each
payload, and only Python can produce a validated one. The candidate design:
`build_tree(raml, samples=True)` (`fastraml tree --samples`), off by default,
adds a `sample(...)` value ([16](16-graph.md) § 8.1) as an ordinary example to
each payload shape that has none of its own. It adds no key and no marker,
and writes nothing to the model. Open questions:

- whether to synthesize leaves, which makes every body runnable, or only
  compose declared data, which is what the Sphinx extension does and leaves
  gaps;
- the `api.json` size cost on large definitions, which should be measured on
  the `large` and `endpoints` bench corpora before deciding. Documents whose
  authors gave every payload an example cost nothing extra, because only gaps
  are filled.
