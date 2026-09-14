"""Reading pydantic models directly, and what that recovers.

Each class here pins something `model_json_schema()` gets wrong or loses, so a
change back to converting from JSON Schema fails by name.
"""

from __future__ import annotations

import datetime
import pathlib
import tempfile
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

import pytest
from fastraml import ParseOptions, parse_from_path
from pydantic import BaseModel, ConfigDict, Discriminator, Field, RootModel, Tag

from raml_document import Document
from raml_document.from_pydantic import Walk


def rendered(model: type[BaseModel]) -> tuple[Walk, Any]:
    """Walk `model`, render it, and parse it back. Raises if it is not RAML."""
    walk = Walk()
    walk.model(model)
    document = Document(title='T', types=walk.types)
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / 'api.raml'
        source.write_text(document.to_raml(), encoding='utf-8')
        raml = parse_from_path(source, ParseOptions(unwrap=True, validate=True))
        return walk, raml.types_in(raml.location)[walk.types and model.__name__]


class TestBoolIsNotAnInteger:
    """`bool` is a subclass of `int` in Python. Missing this is a real bug."""

    def test_a_boolean_field_is_a_boolean(self):
        class M(BaseModel):
            flag: bool

        walk = Walk()
        walk.model(M)
        assert walk.types['M'].properties['flag'].type == 'boolean'

    def test_a_datetime_is_not_its_base_date(self):
        class M(BaseModel):
            when: datetime.datetime
            day: datetime.date

        walk = Walk()
        walk.model(M)
        assert walk.types['M'].properties['when'].type == 'datetime'
        assert walk.types['M'].properties['day'].type == 'date-only'

    def test_the_rendered_document_refuses_a_number_for_a_boolean(self):
        class M(BaseModel):
            flag: bool

        _, shape = rendered(M)
        assert shape.validate({'flag': True}) is None
        assert shape.validate({'flag': 1}) is not None


class TestWhatJsonSchemaLoses:
    def test_decimal_precision_becomes_a_step_and_a_bound(self):
        class M(BaseModel):
            money: Annotated[Decimal, Field(max_digits=8, decimal_places=2)]

        walk, shape = rendered(M)
        decl = walk.types['M'].properties['money']
        assert (decl.type, decl.multiple_of, decl.maximum) == ('number', 0.01, 999999.99)
        assert shape.validate({'money': 1.25}) is None
        assert shape.validate({'money': 1.234}) is not None
        assert shape.validate({'money': 1000000}) is not None

    def test_a_dict_key_type_narrows_the_pattern_property(self):
        class M(BaseModel):
            by_id: dict[int, str]

        walk, shape = rendered(M)
        assert list(walk.types['M'].properties['by_id'].properties) == [r'/^[-+]?\d+$/']
        assert shape.validate({'by_id': {'7': 'x'}}) is None
        assert shape.validate({'by_id': {'-7': 'x'}}) is None
        assert shape.validate({'by_id': {'abc': 'x'}}) is not None

    def test_a_string_keyed_dict_is_the_bare_pattern_any(self):
        class M(BaseModel):
            tags: dict[str, str]

        walk, shape = rendered(M)
        assert list(walk.types['M'].properties['tags'].properties) == ['//']
        assert shape.validate({'tags': {'anything': 'x'}}) is None
        assert shape.validate({'tags': {'anything': 1}}) is not None

    def test_an_integer_tag_stays_an_integer(self):
        """A JSON Schema `discriminator.mapping` stringifies its keys.

        Rendered from there, `discriminatorValue: '1'` meets an `integer`
        property and the document does not parse at all.
        """

        class A(BaseModel):
            kind: Literal[1] = 1
            a: str

        class B(BaseModel):
            kind: Literal[2] = 2
            b: str

        class M(BaseModel):
            x: Annotated[A | B, Field(discriminator='kind')]

        walk, shape = rendered(M)
        assert walk.types['A'].discriminator_value == 1
        assert walk.types['KindBase' if 'KindBase' in walk.types else 'XBase'].properties['kind'].type == 'integer'
        assert shape.validate({'x': {'kind': 1, 'a': 's'}}) is None
        assert shape.validate({'x': {'kind': 3}}) is not None


class TestDiscriminatorSpellings:
    """Two spellings land in two places; reading one renders the other as a plain union."""

    class A(BaseModel):
        kind: Literal['a'] = 'a'
        a: str

    class B(BaseModel):
        kind: Literal['b'] = 'b'
        b: str

    def test_field_discriminator(self):
        class M(BaseModel):
            x: Annotated[TestDiscriminatorSpellings.A | TestDiscriminatorSpellings.B, Field(discriminator='kind')]

        walk = Walk()
        walk.model(M)
        assert any(name.endswith('Base') for name in walk.types)
        assert not walk.dropped

    def test_annotated_discriminator(self):
        class M(BaseModel):
            x: Annotated[TestDiscriminatorSpellings.A | TestDiscriminatorSpellings.B, Discriminator('kind')]

        walk = Walk()
        walk.model(M)
        assert any(name.endswith('Base') for name in walk.types), 'a Discriminator in metadata was missed'
        assert not walk.dropped

    def test_a_callable_discriminator_falls_back_and_says_so(self):
        def pick(value: Any) -> str:
            return 'a'

        class M(BaseModel):
            x: Annotated[
                Annotated[TestDiscriminatorSpellings.A, Tag('a')] | Annotated[TestDiscriminatorSpellings.B, Tag('b')],
                Discriminator(pick),
            ]

        walk = Walk()
        walk.model(M)
        assert walk.types['M'].properties['x'].type == 'A | B'
        assert any('callable discriminator' in message for message in walk.dropped)


class TestStructure:
    def test_a_recursive_model_refers_to_itself_by_name(self):
        class Node(BaseModel):
            name: str
            children: list[Node] = []

        walk, shape = rendered(Node)
        assert walk.types['Node'].properties['children'].type == 'Node[]'
        assert shape.validate({'name': 'r', 'children': [{'name': 'c'}]}) is None

    def test_a_root_model_is_its_single_field(self):
        class Headers(RootModel[dict[str, str]]):
            pass

        walk = Walk()
        walk.model(Headers)
        assert list(walk.types['Headers'].properties) == ['//']

    def test_extra_forbid_becomes_additional_properties(self):
        class M(BaseModel):
            model_config = ConfigDict(extra='forbid')
            a: str

        walk, shape = rendered(M)
        assert walk.types['M'].additional_properties is False
        assert shape.validate({'a': 'x', 'b': 1}) is not None

    def test_an_enum_carries_its_members(self):
        class Status(StrEnum):
            draft = 'draft'
            live = 'live'

        class M(BaseModel):
            status: Status

        walk, shape = rendered(M)
        assert walk.types['M'].properties['status'].enum == ['draft', 'live']
        assert shape.validate({'status': 'gone'}) is not None

    def test_a_set_is_a_unique_array(self):
        class M(BaseModel):
            tags: set[str]

        walk, shape = rendered(M)
        assert walk.types['M'].properties['tags'].unique_items is True
        assert shape.validate({'tags': ['a', 'a']}) is not None

    def test_a_uuid_key_has_a_pattern(self):
        class M(BaseModel):
            by_id: dict[uuid.UUID, str]

        walk = Walk()
        walk.model(M)
        pattern = next(iter(walk.types['M'].properties['by_id'].properties))
        assert pattern.startswith('/^[0-9a-fA-F]{8}')


class TestWhatIsReported:
    @pytest.mark.parametrize(
        ('field_type', 'expected'),
        [
            (Annotated[int, Field(gt=0)], 'exclusive minimum'),
            (Annotated[int, Field(lt=9)], 'exclusive maximum'),
            (Annotated[datetime.date, Field(ge=datetime.date(2020, 1, 1))], 'has no RAML facet'),
        ],
    )
    def test_a_constraint_with_no_facet_is_named(self, field_type, expected):
        class M(BaseModel):
            x: field_type  # type: ignore[valid-type]

        walk = Walk()
        walk.model(M)
        assert any(expected in message for message in walk.dropped), walk.dropped

    def test_a_fixed_length_tuple_is_reported(self):
        class M(BaseModel):
            pair: tuple[int, str]

        walk = Walk()
        walk.model(M)
        assert any('tuple' in message for message in walk.dropped)
