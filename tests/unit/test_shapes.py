"""The shape model's foundations.

See docs/05-type-model.md § 1.
"""

from __future__ import annotations

import dataclasses
import re
from typing import ClassVar

import pytest

from fastraml import RamlError
from fastraml.registry import Raml
from fastraml.types.base import (
    ONE_SHAPE,
    PROPERTIES,
    SHAPE_LIST,
    BaseShape,
    PatternProperty,
    Property,
    TypeExprRef,
    declaration_facets,
)
from fastraml.types.xml import decode_xml_serialization
from fastraml.yamlnode import Node, compose, pairs

LOCATION = 'file:///a.raml'


def value_of(text: str) -> Node:
    """The value node of a one-key document."""
    _key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return value


def make_base(raml: Raml | None = None, **kwargs) -> BaseShape:
    raml = raml or Raml()
    kwargs.setdefault('location', LOCATION)
    return BaseShape(id=raml.next_id(), raml=raml, **kwargs)


class TestBaseShape:
    def test_the_kind_is_unset_until_make_shape_attaches_one(self):
        # docs/05 § 1: the kind is not known when the object is created.
        base = make_base(name='Foo')
        assert (base.type, base.shape) == ('', None)

    def test_every_container_field_starts_empty_rather_than_none(self):
        base = make_base()
        assert (base.inherits, base.custom_facets, base.custom_facet_defs) == ([], {}, {})
        assert (base.annotations, base.type_expr_refs) == ({}, [])

    def test_two_shapes_never_compare_equal_by_value(self):
        # A generated __eq__ on a recursive model is a correctness hazard, and
        # the provenance overlay keys on identity (CLAUDE.md).
        raml = Raml()
        first, second = make_base(raml, name='Foo'), make_base(raml, name='Foo')
        assert first != second
        assert len({first, second}) == 2

    def test_no_instance_dict(self):
        with pytest.raises(AttributeError):
            make_base().__dict__  # noqa: B018 - the access is the assertion

    def test_ids_are_unique_within_one_parse(self):
        raml = Raml()
        assert make_base(raml).id != make_base(raml).id


class TestTypeExprRef:
    """docs/06 § 3 — one record per name inside a type expression.

    Nothing in the parser reads these; P7 emits them so a future LSP can offer
    go-to-definition without re-lexing.
    """

    def test_a_primitive_ref_names_only_the_keyword(self):
        ref = TypeExprRef(line=3, column=9, builtin='string')
        assert (ref.resolved, ref.library_link, ref.library_alias) == (None, None, None)

    def test_a_reference_ref_names_only_the_declaration(self):
        target = make_base(name='Person')
        ref = TypeExprRef(line=3, column=9, resolved=target)
        assert ref.resolved is target
        assert ref.builtin is None

    def test_refs_do_not_compare_by_value(self):
        # `resolved` points into a recursive model, so a generated __eq__ would
        # walk it (CLAUDE.md).
        first = TypeExprRef(line=1, column=1, builtin='string')
        second = TypeExprRef(line=1, column=1, builtin='string')
        assert first != second

    def test_a_ref_is_frozen_and_has_no_instance_dict(self):
        ref = TypeExprRef(line=1, column=1)
        with pytest.raises(AttributeError):
            ref.__dict__  # noqa: B018 - the access is the assertion
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.column = 2


class TestDeclarationFacets:
    def test_a_kind_without_declarations_reports_none(self):
        class StringShapeLike:
            pass

        assert declaration_facets(StringShapeLike) == {}  # type: ignore[arg-type]

    def test_a_kind_publishes_its_table(self):
        class ArrayShapeLike:
            DECLARATION_FACETS: ClassVar = {'items': ONE_SHAPE}

        assert declaration_facets(ArrayShapeLike)['items'].fields == ('items',)  # type: ignore[arg-type]

    def test_properties_fills_two_constructor_keywords(self):
        # `/regex/` keys inside `properties:` are routed to pattern properties,
        # so one facet yields two (docs/05 § 4).
        assert PROPERTIES.fields == ('properties', 'pattern_properties')
        assert SHAPE_LIST.fields == ('any_of',)


class TestPropertyRecords:
    def test_a_property_carries_its_requiredness_beside_its_shape(self):
        base = make_base()
        assert Property(name='age', base=base, required=True).base is base

    def test_a_pattern_property_reprs_its_regex(self):
        prop = PatternProperty(pattern=re.compile('^a'), base=make_base())
        assert repr(prop) == "PatternProperty('^a')"


class TestXmlSerialization:
    def test_all_five_keys_are_read(self):
        text = 'xml:\n  attribute: true\n  wrapped: false\n  name: n\n  namespace: ns\n  prefix: p\n'
        xml = decode_xml_serialization(Raml(), value_of(text), LOCATION)
        assert (xml.attribute.value, xml.wrapped.value) == (True, False)
        assert (xml.name.value, xml.namespace.value, xml.prefix.value) == ('n', 'ns', 'p')

    def test_an_unknown_key_is_an_error_so_a_typo_is_caught(self):
        with pytest.raises(RamlError) as caught:
            decode_xml_serialization(Raml(), value_of('xml:\n  wraped: true\n'), LOCATION)
        trace = next(iter(caught.value.chains()))[-1]
        assert (trace.message, trace.info) == ('unknown xml property', {'property': 'wraped'})

    def test_a_non_mapping_is_rejected(self):
        with pytest.raises(RamlError) as caught:
            decode_xml_serialization(Raml(), value_of('xml: true\n'), LOCATION)
        assert next(iter(caught.value.chains()))[-1].message == 'xml must be a mapping'
