"""The RAML type system — shapes, facets, inheritance, validation.

This package must not import from `pyraml.parser`. Shape construction that needs
YAML takes a `pyraml.yamlnode.Node`, which both layers may import.
See docs/02-architecture.md section 2.
"""
