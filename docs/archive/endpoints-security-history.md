# Endpoint and security design rationale

This file is non-normative. Current behavior belongs in [08](../08-templates-and-endpoints.md)
and [09](../09-security-and-annotations.md).

## Two-stage endpoint construction

Template application is specified over declaration structure. Retaining YAML
until resource-type and trait merging provides one structural algorithm instead
of endpoint-model-specific merge logic, and lets stage 2 decode each final
declaration once.

## Provenance overlay

Merging moves nodes across lexical namespaces. The sparse node-identity overlay
keeps static template content in its declaration scope while caller-provided
template values resolve in the caller scope. Identity preservation during merge
is therefore a correctness requirement, not an optimization.

## Security bindings

Security references survive template application as model bindings, unlike
traits and resource types, which disappear into merged YAML. Keeping the
reference separate from its definition permits per-application OAuth scope
narrowing without changing the shared declaration.
