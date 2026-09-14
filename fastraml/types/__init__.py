"""The RAML type system — shapes, facets, inheritance, validation.

This package must not import from `fastraml.parser`. Shape construction that needs
YAML takes a `fastraml.yamlnode.Node`, which both layers may import.
See docs/02-architecture.md section 2.
"""
