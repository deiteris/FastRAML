r"""Pydantic models -> RAML type declarations, read from the models themselves.

**Not through JSON Schema.** `model_json_schema()` is a projection built for a
different target, and it drops things RAML can carry:

- `Decimal(max_digits=8, decimal_places=2)` becomes
  `anyOf: [number, string with a 60-character regex]`; RAML wants
  `multipleOf: 0.01`, which is right there in `FieldInfo.metadata`.
- `dict[int, str]` becomes a bare `additionalProperties`, losing the key type;
  RAML can say `/^[-+]?\\d+$/: string`.
- A discriminated union's `mapping` keys are stringified, so an integer tag
  arrives as `'1'` and the RAML no longer parses against an `integer` property.

It also adds work of its own -- `$defs`, `$ref`, `anyOf` for nullability -- that
has to be undone to get back to what the annotation already said. Reading
`model_fields` and the annotations is both shorter and more faithful.

What cannot be carried is reported on `Walk.dropped`, never dropped in silence.
Constraints RAML has no facet for -- an exclusive bound, `strict=True`, an
`AfterValidator` -- are named there rather than approximated.

**A model has two shapes when it serialises differently from how it
validates**: a `serialization_alias`, an `exclude=True` field, a
`computed_field`. Inside `Walk.output()` such a model is declared a second time
as `{Name}Output`, which is what a response body refers to; a model that reads
and writes alike is declared once and shared.

The modules, from the one that decides nothing to the one that holds state:

| Module | Holds |
|--------|-------|
| `tables` | which RAML built-in each Python type is, and how a type name is spelled |
| `values` | Python values as the JSON they travel as |
| `introspect` | what a class or annotation says: fields, keys, bases, `Shape` |
| `facets` | constraints as facets on one declaration |
| `unions` | plain and discriminated unions, and the bases the latter share |
| `walk` | `Walk`: the traversal, the registry of declared types, the report |
"""

from __future__ import annotations

from raml_document.from_pydantic.introspect import Shape
from raml_document.from_pydantic.tables import SCALARS
from raml_document.from_pydantic.walk import Walk

__all__ = ['SCALARS', 'Shape', 'Walk']
