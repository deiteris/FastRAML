# Testing history

This is non-normative context retained from the completed parser implementation.
The current policy is [14](../14-testing.md).

## TCK provenance

The TCK submodule replaced an earlier CI arrangement that fetched a moving
go-raml checkout. Pinning the fixture suite made local and CI ratchets refer to
the same inputs. The suite is a submodule rather than a vendored copy because the
archived upstream material states no license; the sdist excludes it.

The harness also corrected classification of Overlay and Extension fixtures by
checking their headers, not only their paths. This remains implemented as current
skip policy.

## Test-suite development

Whole-model golden projections, corpus invariants, and cross-language bindings
were added because unit tests alone could not detect omissions from a projection
or a drift between generated contracts. The current suite retains the resulting
guards; historical fixture-by-fixture investigations and abandoned comparison
scripts are intentionally omitted.
