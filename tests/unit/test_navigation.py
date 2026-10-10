"""Typed-value descent (docs/05 § 6)."""

from __future__ import annotations

from fastraml.parser.entry import ParseOptions
from fastraml.types.navigation import children


def test_typed_navigation_keeps_ambiguous_and_invalid_field_candidates(workspace):
    text = (
        '#%RAML 1.0\ntitle: T\ntypes:\n'
        '  A: {properties: {name: string}}\n'
        '  B: {properties: {name: integer}}\n'
        '  Both:\n    type: A | B\n    example: {name: false, extra: 1}\n'
    )
    raml = workspace.document(text, ParseOptions(unwrap=True))
    base = raml.types_in(raml.location)['Both']
    found = list(children(base, base.example.data.value))
    assert [(each.name, each.base.type) for each in found] == [('name', 'string'), ('name', 'integer')]
    assert all(each.key_pos.is_known for each in found)
