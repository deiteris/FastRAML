# Diagnostics design history

This note preserves evidence removed from `docs/11-diagnostics.md`. It explains
past design choices but does not define current behavior.

## Error shape

The chain-and-sibling model and the JSON projection were chosen to make
fastRAML's diagnostics comparable with go-raml's stacktrace output during TCK
work. That comparison produced the `traces` and `stack` envelope retained by
`RamlError.to_dict()`.

## Lenient parsing

An early `parse_lenient()` implementation continued into later passes after a
pass failed. The later passes consumed incomplete state and repeated the original
failure instead of finding independent errors. In the measured case, one missing
library referenced by twenty types produced 41 diagnostics instead of one.

The implementation was changed to stop where strict parsing stops and return the
partially built model. Recovering more independent failures would require passes
to skip known-broken entities rather than running unchanged over incomplete
state.

## Provenance diagnostics

Structural merging can place a trait-authored response under an API-authored
operation. Resolving a diagnostic location only from the enclosing operation
therefore attributed errors to the API file. The stage-2 decoders were changed
to consult the provenance overlay at the child node that creates the entity or
diagnostic.
