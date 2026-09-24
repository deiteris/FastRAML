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

`fastraml join` is designed in [20](20-join.md) and not yet implemented.

## 3. Potential future work

Potential consumers and tooling include an LSP, more editor recovery in
`parse_lenient`, and improved remote-include latency. These are not commitments
and must not change parser rules without an owning design document and tests.
