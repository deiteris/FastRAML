# Parser foundations history

This file is non-normative. It retains only durable context for surprising
parser-foundation invariants. Current behavior is defined by docs 01 through 04.

## Typed-fragment anchors

Typed fragments use their own lexical namespace so a cached fragment has one
meaning at every inclusion site. Allowing an includer's declarations to resolve
an unqualified name would make one cached fragment depend on where it was used.

## Endpoint source merge

Traits and resource types merge source nodes before endpoint materialization.
This follows the RAML merge operation and permits one merge implementation for
all endpoint fields. The provenance overlay is required because a merged tree can
contain nodes authored in several lexical namespaces.

## Node identity and aliases

The provenance overlay is keyed by `Node` object identity. Nodes therefore have
identity equality, structural merge preserves node identity, and YAML aliases are
expanded into independent nodes so one source node cannot represent multiple
logical locations or scopes.

## Loader sandbox

The default loader protects against document-controlled traversal and symlink
escapes, but Python's portable filesystem APIs cannot make every check atomic
against a concurrent local attacker. The documented loader contract intentionally
states this boundary.
