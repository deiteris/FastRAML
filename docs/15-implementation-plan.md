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

`fastraml join` ([20](20-join.md)) accepts API documents only; Overlays,
Extensions and Libraries as inputs, and renaming to resolve a conflict, are not
covered (docs/20 § 11).

## 3. Potential future work

Potential tooling includes more editor recovery in `parse_lenient` and
improved remote-include latency. These are not commitments and must not change
parser rules without an owning design document and tests.

The language service and its LSP are built ([21](21-language-service.md)).
The ordered plan for the rest, completion, editing features and MCP, is
[research/language-service-plan.md](research/language-service-plan.md): M1 to
M4 are done, and completion (M5) comes next. Its design records are
[archive/language-server.md](archive/language-server.md) and
[archive/language-service-architecture.md](archive/language-service-architecture.md);
its parser prerequisite, a trustworthy `parse_lenient` model, is analysed in
[research/partial-models.md](research/partial-models.md).

**Deprecated aliases as a model fact (proposed).** `deprecated-schemas` is on
by default and reads `source_info`, so every lint-enabled parse, the language
service's included, retains each declaration's YAML subtree: +7.66 MB on
`large`, against +0.56 MB for the text alone. It needs only where a `schema:`
or `schemas:` key was accepted, which the decoder knows. Recording those
(file, position, key) on the model would let the default rule set run on
`retain_text`; the three off-by-default rules that inspect how a declaration
was written keep `retain_source`.

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
