"""The RAML type system — shapes, facets, inheritance, validation.

At runtime this package imports only `parser/facets.py`, `parser/annotations.py`
and `parser/includes.py` from the parser; `types/shape.py` holds the one deferred
import that breaks the fragment/shape cycle. See docs/02-architecture.md § 2.
"""
