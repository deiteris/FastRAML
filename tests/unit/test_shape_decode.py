"""Decoding a declaration: the walk, the kinds, and the facets.

See docs/05-type-model.md section 4. `test_shapes.py` covers the model classes
themselves; this file covers `make_shape`.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pyraml import RamlError
from pyraml.registry import Raml
from pyraml.types.complex_ import UnknownShape
from pyraml.types.shape import make_shape
from pyraml.yamlnode import compose, pairs

LOCATION = 'file:///a.raml'

JSON_TYPE = '{"type": "object"}'


def shape(text: str, default: str = 'string', raml: Raml | None = None):
    """Build one declaration from a `Name: <declaration>` document."""
    raml = raml or Raml()
    key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return make_shape(raml, key, value, LOCATION, default)


def first_trace(caught):
    return next(iter(caught.value.chains()))[-1]


class TestKindDispatch:
    @pytest.mark.parametrize(
        'expected',
        [
            'string',
            'integer',
            'number',
            'boolean',
            'nil',
            'any',
            'file',
            'datetime',
            'date-only',
            'time-only',
            'datetime-only',
            'object',
            'array',
        ],
    )
    def test_a_built_in_name_reaches_its_kind(self, expected: str):
        base = shape(f'T: {expected}\n')
        assert base.type == expected
        assert base.shape is not None

    def test_a_null_type_is_a_string(self):
        # Spec section Determine Default Types.
        assert shape('T:\n  type:\n').type == 'string'

    def test_a_body_declaration_defaults_to_any(self):
        assert shape('body:\n  description: d\n', default='any').type == 'any'

    def test_an_unknown_name_is_left_for_p7(self):
        raml = Raml()
        base = shape('T: Person\n', raml=raml)
        assert isinstance(base.shape, UnknownShape)
        assert list(raml.unresolved_shapes) == [base]
        assert base.type_expr.value == 'Person', 'the expression is kept for P7 to parse'


class TestInvariantI4:
    def test_every_shape_is_registered_and_only_unknowns_are_on_the_worklist(self):
        raml = Raml()
        shape('T:\n  properties:\n    a: string\n    b: Person\n', raml=raml)
        assert len(raml.shapes) == 3, 'the object and its two properties'
        unresolved = set(raml.unresolved_shapes)
        for base in raml.shapes:
            assert (base in unresolved) is isinstance(base.shape, UnknownShape)


class TestCommonFacets:
    def test_the_common_facets_land_on_the_base(self):
        base = shape('T:\n  type: string\n  displayName: D\n  description: Text.\n  (ann): 1\n')
        assert (base.display_name.value, base.description.value) == ('D', 'Text.')
        assert base.annotations['ann'].value.raw == 1

    def test_enum_members_are_data_nodes(self):
        base = shape('T:\n  type: string\n  enum: [a, b]\n')
        assert [member.raw for member in base.enum] == ['a', 'b']

    def test_default_is_a_data_node(self):
        assert shape('T:\n  type: string\n  default: x\n').default.raw == 'x'

    def test_xml_is_parsed(self):
        assert shape('T:\n  type: string\n  xml:\n    attribute: true\n').xml.attribute.value is True

    def test_example_and_examples_cannot_be_written_together(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: string\n  example: a\n  examples:\n    one: b\n')
        assert 'example and examples cannot be defined together' in caught.value.messages()[0]

    def test_type_and_schema_are_mutually_exclusive(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: string\n  schema: string\n')
        assert '`type` and `schema` are mutually exclusive' in caught.value.messages()[0]

    def test_the_declaration_keeps_its_position_and_file(self):
        base = shape('T:\n  type: string\n')
        assert base.key_pos.line == 1
        assert base.location == LOCATION


class TestScalarFacetDecoding:
    def test_string_facets(self):
        string = shape('T:\n  type: string\n  minLength: 2\n  maxLength: 5\n  pattern: ^a\n').shape
        assert (string.min_length.value, string.max_length.value) == (2, 5)
        assert string.pattern.value.match('abc') is not None

    def test_integer_bounds_are_integers(self):
        integer = shape('T:\n  type: integer\n  minimum: 0\n  maximum: 10\n  format: int8\n').shape
        assert (integer.minimum.value, integer.maximum.value) == (0, 10)
        assert integer.format.value == 'int8'

    def test_number_bounds_are_exact_and_never_pass_through_float(self):
        # Fraction(1.1) would be 2476979795053773/2251799813685248, and
        # `multipleOf: 1.1` would then reject 2.2.
        number = shape('T:\n  type: number\n  minimum: 1.1\n  multipleOf: 1.1\n').shape
        assert number.minimum.value == Fraction(11, 10)
        assert number.multiple_of.value == Fraction(11, 10)

    def test_an_integers_multiple_of_is_still_exact(self):
        assert shape('T:\n  type: integer\n  multipleOf: 0.5\n').shape.multiple_of.value == Fraction(1, 2)

    def test_a_bound_may_not_be_infinite(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: number\n  minimum: .inf\n')
        assert first_trace(caught).message == 'expected a finite number value'

    def test_file_types_are_a_list_of_positioned_strings(self):
        file_shape = shape('T:\n  type: file\n  fileTypes: [image/png, image/jpeg]\n').shape
        assert [facet.value for facet in file_shape.file_types] == ['image/png', 'image/jpeg']

    def test_a_bad_facet_value_is_reported_where_it_is_written(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: string\n  minLength: two\n')
        trace = first_trace(caught)
        assert trace.message == 'expected an integer value'
        assert trace.position.line == 3


class TestDeclarationFacetsAreBuiltByShapePy:
    def test_object_properties_arrive_built(self):
        obj = shape('T:\n  type: object\n  properties:\n    a: string\n    /b/: integer\n').shape
        assert list(obj.properties) == ['a']
        assert list(obj.pattern_properties) == ['b']
        assert obj.properties['a'].base.type == 'string'

    def test_array_items_arrive_built(self):
        array = shape('T:\n  type: array\n  items: string\n  minItems: 1\n').shape
        assert array.items.type == 'string'
        assert array.min_items.value == 1

    def test_union_members_arrive_built(self):
        union = shape('T:\n  type: union\n  anyOf: [string, integer]\n').shape
        assert [member.type for member in union.any_of] == ['string', 'integer']

    def test_a_declaration_facet_never_reaches_decode_facets(self):
        obj = shape('T:\n  type: object\n  properties:\n    a: string\n').shape
        assert obj.base.custom_facets == {}, 'properties: is consumed, not filed as a custom facet'

    def test_an_unknown_shape_keeps_its_declaration_facets_undigested(self):
        # The kind is not known, so which keys are facets is not known either.
        base = shape('T:\n  type: Person\n  properties:\n    a: string\n')
        assert isinstance(base.shape, UnknownShape)
        assert [node.value for node in base.shape.facets[::2]] == ['properties']


class TestCustomFacets:
    def test_an_unrecognised_key_becomes_a_custom_facet_value(self):
        base = shape('T:\n  type: string\n  amountOfFingers: 5\n')
        assert base.custom_facets['amountOfFingers'].raw == 5

    def test_a_facets_declaration_reuses_the_property_rules(self):
        base = shape('T:\n  type: string\n  facets:\n    onlyFutureDates?: boolean\n')
        declaration = base.custom_facet_defs['onlyFutureDates']
        assert (declaration.required, declaration.base.type) == (False, 'boolean')

    def test_a_facet_name_may_not_look_like_an_annotation(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: string\n  facets:\n    (x): boolean\n')
        assert "facet name must not begin with '('" in caught.value.messages()[0]

    def test_a_facet_may_not_shadow_a_built_in_one_of_its_kind(self):
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: string\n  facets:\n    minLength: integer\n')
        trace = first_trace(caught)
        assert trace.message == 'cannot redefine built-in facet'
        assert trace.info == {'facet': 'minLength', 'type': 'string'}

    def test_the_same_name_is_fine_on_a_kind_that_has_no_such_facet(self):
        # minLength belongs to string and file, not to object.
        base = shape('T:\n  type: object\n  facets:\n    minLength: integer\n')
        assert 'minLength' in base.custom_facet_defs


class TestJsonSchemaTypes:
    def test_an_inline_json_schema_becomes_a_json_shape(self):
        base = shape(f'T: {JSON_TYPE!r}\n')
        assert (base.type, base.shape.raw) == ('json', JSON_TYPE)

    def test_a_sibling_facet_is_rejected(self):
        # Spec section Using XML and JSON Schemas: such a type must not
        # participate in inheritance or specialization.
        with pytest.raises(RamlError) as caught:
            shape(f'T:\n  type: {JSON_TYPE!r}\n  minLength: 2\n')
        assert 'cannot define facets on a JSON schema type' in caught.value.messages()[0]

    def test_the_wrapper_facets_the_spec_allows_still_work(self):
        base = shape(f'T:\n  type: {JSON_TYPE!r}\n  description: d\n  example: {{}}\n')
        assert base.description.value == 'd'
        assert base.example is not None


class TestUnionDiscriminator:
    def test_a_discriminator_on_a_union_is_rejected_at_decode_time(self):
        # The one discriminator rule not deferred to P10: a union has no
        # properties, so this can never become valid (docs/05 section 9).
        with pytest.raises(RamlError) as caught:
            shape('T:\n  type: union\n  anyOf: [string]\n  discriminator: kind\n')
        assert first_trace(caught).message == 'discriminator cannot be used with union type'

    def test_a_discriminator_on_an_object_is_kept_for_p10(self):
        obj = shape('T:\n  type: object\n  discriminator: kind\n  discriminatorValue: cat\n').shape
        assert obj.discriminator.value == 'kind'
        assert obj.discriminator_value.raw == 'cat'


class TestMultipleInheritance:
    def test_a_sequence_of_parents_becomes_composite(self):
        base = shape('T: [Cat, Dog]\n')
        assert base.type == 'composite'
        assert [parent.type_expr.value for parent in base.inherits] == ['Cat', 'Dog']

    def test_an_include_may_not_be_a_parent(self):
        with pytest.raises(RamlError) as caught:
            shape('T: [!include a.raml]\n')
        assert first_trace(caught).message == '!include is not allowed in multiple inheritance'
