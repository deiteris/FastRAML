"""Default-type inference.

Each test names the rule from docs/05-type-model.md section 4.2 that it
protects.
"""

from __future__ import annotations

import pytest

from pyraml import RamlError
from pyraml.types.inference import identify_shape_type
from pyraml.yamlnode import Node, compose, pairs

LOCATION = 'file:///a.raml'


def facets(text: str) -> list[Node]:
    """The flat `[k0, v0, k1, v1, …]` list `make_shape` hands to inference."""
    flat: list[Node] = []
    for key, value in pairs(compose(text, uri=LOCATION)):
        flat.append(key)
        flat.append(value)
    return flat


def detect(text: str, default: str = 'string') -> str:
    return identify_shape_type(facets(text), default, LOCATION)


class TestRule1UniqueFacetSettlesTheType:
    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            ('minLength: 2\n', 'string'),
            ('pattern: ^a\n', 'string'),
            ('minimum: 1\n', 'number'),
            ('multipleOf: 2\n', 'number'),
            ('minItems: 1\n', 'array'),
            ('items: string\n', 'array'),
            ('properties:\n  a: string\n', 'object'),
            ('discriminator: kind\n', 'object'),
            ('fileTypes: [image/png]\n', 'file'),
        ],
    )
    def test_one_hinting_facet_is_enough(self, source: str, expected: str):
        assert detect(source) == expected

    def test_agreeing_facets_agree(self):
        assert detect('minItems: 1\nmaxItems: 3\nuniqueItems: true\n') == 'array'


class TestRule2ConflictingHintsAreAnError:
    def test_two_types_in_one_declaration_are_rejected(self):
        with pytest.raises(RamlError) as caught:
            detect('minItems: 1\nproperties:\n  a: string\n')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'detected types by facets are not equal'
        assert trace.info == {'detected': 'array', 'conflicting': 'object', 'facet': 'properties'}

    def test_the_error_points_at_the_facet_that_conflicts(self):
        with pytest.raises(RamlError) as caught:
            detect('minItems: 1\nminProperties: 2\n')
        assert next(iter(caught.value.chains()))[-1].position.line == 2


class TestRule3StringAndFileReconcileToFile:
    # minLength and maxLength belong to both types, so seeing one of them
    # alongside fileTypes is not a conflict.
    def test_string_then_file(self):
        assert detect('minLength: 2\nfileTypes: [image/png]\n') == 'file'

    def test_file_then_string(self):
        assert detect('fileTypes: [image/png]\nmaxLength: 9\n') == 'file'

    def test_a_pattern_before_file_types_blocks_the_reconciliation(self):
        # `pattern` is string-only: a file has no pattern.
        with pytest.raises(RamlError) as caught:
            detect('pattern: ^a\nfileTypes: [image/png]\n')
        assert next(iter(caught.value.chains()))[-1].message == 'detected types by facets are not equal'

    def test_a_pattern_after_file_types_blocks_it_too(self):
        with pytest.raises(RamlError) as caught:
            detect('fileTypes: [image/png]\npattern: ^a\n')
        assert next(iter(caught.value.chains()))[-1].message == 'detected types by facets are not equal'

    def test_a_pattern_poisons_a_later_length_facet_as_well(self):
        # Once string-only is set it holds for the rest of the declaration, so
        # minLength can no longer be reconciled towards file.
        with pytest.raises(RamlError) as caught:
            detect('pattern: ^a\nminLength: 2\nfileTypes: [image/png]\n')
        assert next(iter(caught.value.chains()))[-1].info['detected'] == 'string'


class TestRule4NothingInferredTakesTheCallersDefault:
    def test_an_empty_facet_list_takes_the_default(self):
        assert identify_shape_type([], 'string', LOCATION) == 'string'

    def test_a_body_declaration_defaults_to_any(self):
        assert detect('displayName: X\n', default='any') == 'any'

    def test_facets_that_hint_at_nothing_are_ignored(self):
        # discriminatorValue is object-only in practice but is not a hint: the
        # table in docs/05 section 4.2 does not list it.
        assert detect('discriminatorValue: cat\ndescription: d\n', default='any') == 'any'
