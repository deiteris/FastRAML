"""Property declarations, optionality and pattern properties.

Each test names the rule from docs/05-type-model.md sections 5 and 5.1 that it
protects.
"""

from __future__ import annotations

import pytest

from pyraml import RamlError
from pyraml.registry import Raml
from pyraml.types.shape import chomp_optional, make_declarations, make_property
from pyraml.yamlnode import Node, compose, pairs

LOCATION = 'file:///a.raml'


def value_of(text: str) -> Node:
    _key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return value


def declarations(text: str):
    """`properties:` split into its named and its pattern halves."""
    return make_declarations(Raml(), value_of(text), LOCATION)


def one_property(text: str):
    key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return make_property(Raml(), key, value, LOCATION)


class TestChompOptional:
    @pytest.mark.parametrize(
        ('name', 'expected'),
        [('a', ('a', False)), ('a?', ('a', True)), ('a??', ('a?', True)), ('?', ('', True))],
    )
    def test_exactly_one_question_mark_comes_off(self, name: str, expected: tuple[str, bool]):
        assert chomp_optional(name) == expected


class TestTheFourOptionalityCases:
    def test_rule_1_a_question_mark_alone_means_optional(self):
        prop = one_property('name?: string\n')
        assert (prop.name, prop.required) == ('name', False)

    def test_rule_2_no_question_mark_means_required(self):
        prop = one_property('name: string\n')
        assert (prop.name, prop.required) == ('name', True)

    def test_rule_3_an_explicit_required_makes_the_question_mark_part_of_the_name(self):
        # The property is literally called `name?`, and its requiredness is what
        # `required:` says — not what the `?` would have said.
        prop = one_property('name?:\n  type: string\n  required: true\n')
        assert (prop.name, prop.required) == ('name?', True)

    def test_rule_3_holds_when_required_is_false_too(self):
        prop = one_property('name?:\n  type: string\n  required: false\n')
        assert (prop.name, prop.required) == ('name?', False)

    def test_rule_4_a_double_question_mark_is_an_optional_property_named_with_one(self):
        prop = one_property('name??: string\n')
        assert (prop.name, prop.required) == ('name?', False)


class TestPatternProperties:
    def test_a_slash_delimited_key_becomes_a_pattern_property(self):
        properties, patterns = declarations('properties:\n  /^a.*/: string\n  plain: string\n')
        assert list(properties) == ['plain']
        assert list(patterns) == ['^a.*']

    def test_the_empty_pattern_is_a_pattern_property(self):
        # docs/05 section 5.1 names this case explicitly.
        _properties, patterns = declarations('properties:\n  //: string\n')
        assert list(patterns) == ['']

    def test_declaration_order_is_preserved_so_the_first_match_wins(self):
        _properties, patterns = declarations('properties:\n  /b/: string\n  /a/: string\n')
        assert list(patterns) == ['b', 'a']

    def test_a_pattern_property_may_not_be_marked_optional(self):
        with pytest.raises(RamlError) as caught:
            declarations('properties:\n  /a/?: string\n')
        assert "'required' is not supported on a pattern property" in caught.value.messages()[0]

    def test_a_pattern_property_may_not_declare_required(self):
        with pytest.raises(RamlError) as caught:
            declarations('properties:\n  /a/:\n    type: string\n    required: false\n')
        assert "'required' is not supported on a pattern property" in caught.value.messages()[0]

    def test_an_uncompilable_pattern_is_reported_where_it_is_written(self):
        with pytest.raises(RamlError) as caught:
            declarations('properties:\n  /a(/: string\n')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'invalid pattern'
        assert trace.info['pattern'] == 'a('


class TestInlineDeclarations:
    def test_a_property_may_be_a_full_declaration(self):
        prop = one_property('age:\n  type: integer\n  minimum: 0\n')
        assert prop.base.type == 'integer'
        assert prop.base.shape.minimum.value == 0

    def test_a_property_may_be_a_bare_type_name(self):
        assert one_property('age: integer\n').base.type == 'integer'

    def test_a_property_with_no_type_infers_one_from_its_facets(self):
        assert one_property('age:\n  minimum: 0\n').base.type == 'number'
