# Views and consumers rationale

This archive records durable design reasons that are useful when changing the
view layer or its consumers. Current behavior and commands belong in docs 16,
17, and 18.

## Projection boundary

The parser owns RAML semantics. Views and consumers read completed models so
they do not duplicate resolution, inheritance, validation, or template rules.
Keeping that direction one-way makes parser behavior available consistently to
every consumer.

## Graph and tree

The graph and tree serve different needs. Graph traversal needs addressed
identity and reference edges. Reading and rendering need containment and leaf
data. A shared address walk lets their outputs join without forcing either form
to carry the other's representation.

## Generated tree bindings

The tree is a cross-language wire contract. Generated bindings prevent a tree
key or its structural value kind from being independently redefined in each
consumer. Hand-written target-language runtime code remains separate because it
does not vary with the tree schema.

## Linting

Lint is policy over valid, effective RAML rather than another parser pass. This
keeps language conformance in one pipeline while allowing opt-in security, style,
and organisation rules to use the same effective model.

## Shared fixtures

The worked fixture belongs at the repository root because it is shared by
multiple independent consumers. Keeping it outside a consumer avoids making one
downstream project the owner of another project's input.
