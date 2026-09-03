"""The shape model's foundations.

See docs/05-type-model.md section 1.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from pyraml.registry import Raml
from pyraml.types.base import (
    ONE_SHAPE,
    PROPERTIES,
    SHAPE_LIST,
    BaseShape,
    PatternProperty,
    Property,
    declaration_facets,
)


def make_base(raml: Raml | None = None, **kwargs) -> BaseShape:
    raml = raml or Raml()
    kwargs.setdefault('location', 'file:///a.raml')
    return BaseShape(id=raml.next_id(), raml=raml, **kwargs)


class TestBaseShape:
    def test_the_kind_is_unset_until_make_shape_attaches_one(self):
        # docs/05 section 1: the kind is not known when the object is created.
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
        # so one facet yields two (docs/05 section 5.1).
        assert PROPERTIES.fields == ('properties', 'pattern_properties')
        assert SHAPE_LIST.fields == ('any_of',)


class TestPropertyRecords:
    def test_a_property_carries_its_requiredness_beside_its_shape(self):
        base = make_base()
        assert Property(name='age', base=base, required=True).base is base

    def test_a_pattern_property_reprs_its_regex(self):
        import re

        prop = PatternProperty(pattern=re.compile('^a'), base=make_base())
        assert repr(prop) == "PatternProperty('^a')"
