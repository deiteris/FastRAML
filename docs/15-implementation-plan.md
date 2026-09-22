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

The current deferred language features are Overlays and Extensions. Their support
requires their own merge rules and overlay-specific conformance checks; existing
structural merge code is not itself a specification for them.

Known limitations are documented beside the rules they affect:

- custom-facet validation follows the first inheritance parent rather than a
  complete multi-parent facet chain;
- union `enum` semantics remain deferred; and
- XML Schema external types are unsupported.

## 3. Potential future work

Potential consumers and tooling include an LSP, more editor recovery in
`parse_lenient`, and improved remote-include latency. These are not commitments
and must not change parser rules without an owning design document and tests.
