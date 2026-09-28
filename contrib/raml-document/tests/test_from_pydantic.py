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
from typing import Annotated, Any, Generic, Literal, TypeVar

import pytest
from fastraml import ParseOptions, parse_from_path
from pydantic import BaseModel, ConfigDict, Discriminator, Field, RootModel, Tag, computed_field

from raml_document import Document, TypeDecl
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


class TestInheritance:
    """Python subclassing becomes RAML subtyping, so a subtype says what it adds."""

    def test_a_subclass_names_its_base_and_adds_only_its_own_properties(self):
        class Vehicle(BaseModel):
            wheels: int

        class Car(Vehicle):
            doors: int

        walk = Walk()
        walk.model(Car)
        assert walk.types['Car'].render() == {'type': 'Vehicle', 'properties': {'doors': 'integer'}}
        assert walk.types['Vehicle'].render() == {'type': 'object', 'properties': {'wheels': 'integer'}}
        assert walk.dropped == []

    def test_two_bases_become_ramls_multiple_inheritance(self):
        """`type: [A, B]` is *both*, which is a different node from `A | B`."""

        class Left(BaseModel):
            a: int

        class Right(BaseModel):
            b: int

        class Both(Left, Right):
            c: int

        walk = Walk()
        walk.model(Both)
        assert walk.types['Both'].render() == {'type': ['Left', 'Right'], 'properties': {'c': 'integer'}}

    def test_a_list_type_keeps_its_key_rather_than_collapsing(self):
        """`{type: [A, B]}` has no shorthand; only a single name has one."""
        assert TypeDecl(type=['A', 'B']).render() == {'type': ['A', 'B']}
        assert TypeDecl(type='A').render() == 'A'

    def test_an_inherited_additional_properties_is_not_repeated(self):
        """RAML inherits the facet, so restating it says twice what one says."""

        class Closed(BaseModel):
            model_config = ConfigDict(extra='forbid')
            a: int

        class Child(Closed):
            b: int

        walk = Walk()
        walk.model(Child)
        assert walk.types['Closed'].additional_properties is False
        assert walk.types['Child'].additional_properties is None

    def test_an_overridden_property_is_written_again(self):
        """A subtype restricting a property has to state the restriction."""

        class Loose(BaseModel):
            code: str

        class Tight(Loose):
            code: Annotated[str, Field(max_length=4)]

        walk = Walk()
        walk.model(Tight)
        assert walk.types['Tight'].render()['properties']['code']['maxLength'] == 4

    def test_a_tagged_union_member_keeps_the_base_it_already_had(self):
        """The synthesised base is added to a real one, never over it."""

        class Animal(BaseModel):
            name: str

        class Cat(Animal):
            kind: Literal['cat'] = 'cat'
            meows: bool

        class Dog(Animal):
            kind: Literal['dog'] = 'dog'
            barks: bool

        class Owner(BaseModel):
            pet: Annotated[Cat | Dog, Field(discriminator='kind')]

        walk = Walk()
        walk.model(Owner)
        assert walk.types['Cat'].type == ['Animal', 'PetBase']
        assert walk.types['Cat'].discriminator_value == 'cat'
        assert 'name' not in walk.types['Cat'].properties

    def test_a_tagged_union_member_with_no_real_base_inherits_only_the_synthesised_one(self):
        class Cat(BaseModel):
            kind: Literal['cat'] = 'cat'
            meows: bool

        class Dog(BaseModel):
            kind: Literal['dog'] = 'dog'
            barks: bool

        class Owner(BaseModel):
            pet: Annotated[Cat | Dog, Field(discriminator='kind')]

        walk = Walk()
        walk.model(Owner)
        assert walk.types['Cat'].type == 'PetBase'

    def test_a_root_model_is_its_field_and_not_a_subtype_of_root_model(self):
        class Headers(RootModel[dict[str, str]]):
            pass

        walk = Walk()
        walk.model(Headers)
        assert walk.types['Headers'].render() == {'type': 'object', 'properties': {'//': 'string'}}

    def test_a_parametrised_generic_base_is_not_a_raml_name(self):
        """`Page[Book]` is a class pydantic built; no document can declare it."""
        T = TypeVar('T')

        class Page(BaseModel, Generic[T]):
            items: list[T]

        class Books(Page[int]):
            pass

        walk = Walk()
        walk.model(Books)
        assert walk.types['Books'].render() == {'type': 'object', 'properties': {'items': 'integer[]'}}


def parsed_types(walk: Walk) -> Any:
    """Every type `walk` declared, through fastraml. Raises if it is not RAML."""
    document = Document(title='T', types=walk.types)
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / 'api.raml'
        source.write_text(document.to_raml(), encoding='utf-8')
        raml = parse_from_path(source, ParseOptions(unwrap=True, validate=True))
        return raml.types_in(raml.location)


def _model(name: str, module: str, **fields: Any) -> type[BaseModel]:
    """A model called `name` that claims to live in `module`."""
    return type(name, (BaseModel,), {'__annotations__': fields, '__module__': module})


class TestNames:
    """Every model gets a RAML name of its own, and a name RAML can read."""

    def test_a_parametrised_generic_gets_a_name_a_parser_reads(self):
        """`Page[Item]` is a type expression -- an array suffix -- to a RAML parser."""
        T = TypeVar('T')

        class Page(BaseModel, Generic[T]):
            items: list[T]

        class M(BaseModel):
            page: Page[int]

        walk = Walk()
        walk.model(M)
        assert walk.types['M'].properties['page'].type == 'Page_int_'
        assert parsed_types(walk)['M'].validate({'page': {'items': [1]}}) is None

    def test_same_named_models_in_same_named_modules_stay_apart(self):
        """Two packages each keeping a `User` in `models.py` agree on the last segment."""
        users = _model('User', 'app.users.models', a=int)
        admins = _model('User', 'app.admin.models', b=str)
        third = _model('User', 'other.models', c=bool)
        walk = Walk()
        names = [walk.model(model) for model in (users, admins, third)]
        assert len(set(names)) == 3
        assert [list(walk.types[name].properties) for name in names] == [['a'], ['b'], ['c']]
        assert len(walk.dropped) == 2

    def test_two_classes_from_one_factory_are_numbered_rather_than_merged(self):
        first, second, third = (_model('Item', 'here', a=int) for _ in range(3))
        walk = Walk()
        names = [walk.model(model) for model in (first, second, third)]
        assert len(set(names)) == 3

    def test_a_synthesised_base_does_not_overwrite_a_model_of_that_name(self):
        class PetBase(BaseModel):
            real: int

        class Cat(BaseModel):
            kind: Literal['cat']

        class Dog(BaseModel):
            kind: Literal['dog']

        class M(BaseModel):
            owned: PetBase
            pet: Annotated[Cat | Dog, Field(discriminator='kind')]

        walk = Walk()
        walk.model(M)
        assert list(walk.types['PetBase'].properties) == ['real']
        assert walk.types['Cat'].type != 'PetBase'
        parsed_types(walk)


class _Cat(BaseModel):
    kind: Literal['cat']
    species: Literal['c']


class _Dog(BaseModel):
    kind: Literal['dog']
    species: Literal['d']


class TestTaggedUnionsShareTheirBase:
    def test_two_fields_selecting_by_one_property_share_one_base(self):
        """A base per use would give each member two bases naming one discriminator."""

        class M(BaseModel):
            pet: Annotated[_Cat | _Dog, Field(discriminator='kind')]
            animal: Annotated[_Cat | _Dog, Field(discriminator='kind')]

        walk = Walk()
        walk.model(M)
        assert walk.types['_Cat'].type == 'PetBase'
        assert 'AnimalBase' not in walk.types
        shape = parsed_types(walk)['M']
        assert shape.validate({'pet': {'kind': 'cat', 'species': 'c'}, 'animal': {'kind': 'dog', 'species': 'd'}}) is None

    def test_a_member_already_selected_by_another_property_leaves_the_union_plain(self):
        """RAML gives a type one `discriminatorValue`; overwriting it breaks the first union."""

        class M(BaseModel):
            pet: Annotated[_Cat | _Dog, Field(discriminator='kind')]
            zoo: Annotated[_Cat | _Dog, Field(discriminator='species')]

        walk = Walk()
        walk.model(M)
        assert walk.types['_Cat'].discriminator_value == 'cat'
        assert walk.types['PetBase'].discriminator == 'kind'
        assert any('already selected by' in message for message in walk.dropped)
        parsed_types(walk)

    def test_a_list_of_tagged_unions_keeps_its_discriminator(self):
        """`list[Annotated[A | B, Field(discriminator=...)]]`: pydantic lifts nothing."""

        class M(BaseModel):
            pets: list[Annotated[_Cat | _Dog, Field(discriminator='kind')]]

        walk = Walk()
        walk.model(M)
        assert walk.types['_Cat'].discriminator_value == 'cat'
        assert 'PetsBase' in walk.types
        shape = parsed_types(walk)['M']
        assert shape.validate({'pets': [{'kind': 'cat', 'species': 'c'}]}) is None
        assert shape.validate({'pets': [{'kind': 'cat', 'species': 'd'}]}) is not None


class TestConstraintsInsideUnionsAndItems:
    """A type expression holds names, so a narrowed member is declared by name."""

    def test_a_constrained_optional_string_parses_and_keeps_its_bound(self):
        """`{type: string | nil, maxLength: 3}` is an unknown facet to a parser."""

        class M(BaseModel):
            code: str | None = Field(default=None, max_length=3)

        walk = Walk()
        walk.model(M)
        shape = parsed_types(walk)['M']
        assert shape.validate({'code': 'abc'}) is None
        assert shape.validate({'code': None}) is None
        assert shape.validate({'code': 'abcd'}) is not None

    def test_a_constraint_on_a_union_member_is_kept(self):
        class M(BaseModel):
            code: Annotated[str, Field(max_length=3)] | None = None

        walk = Walk()
        walk.model(M)
        assert parsed_types(walk)['M'].validate({'code': 'abcd'}) is not None

    def test_a_literal_union_member_keeps_its_enum(self):
        class M(BaseModel):
            status: Literal['a', 'b'] | None = None

        walk = Walk()
        walk.model(M)
        shape = parsed_types(walk)['M']
        assert shape.validate({'status': 'a'}) is None
        assert shape.validate({'status': 'c'}) is not None

    def test_an_array_items_constraint_is_kept(self):
        class M(BaseModel):
            scores: list[Annotated[int, Field(ge=1)]]

        walk = Walk()
        walk.model(M)
        shape = parsed_types(walk)['M']
        assert shape.validate({'scores': [1]}) is None
        assert shape.validate({'scores': [0]}) is not None

    def test_a_non_nullable_position_reads_optional_as_its_member(self):
        """A query parameter is text or absent; `None` there means only optional."""

        class M(BaseModel):
            q: str | None = Field(default=None, max_length=3)

        walk = Walk()
        decl = walk.field(M.model_fields['q'], 'q', nullable=False)
        assert decl.render() == {'type': 'string', 'maxLength': 3}


class TestExtraScalars:
    def test_a_registered_class_and_its_subclasses_are_that_builtin(self):
        class Upload:
            pass

        class Special(Upload):
            pass

        walk = Walk(scalars={Upload: 'file'})
        assert walk.annotation(Special, 'x').type == 'file'
        assert walk.annotation(list[Upload], 'x').type == 'file[]'
        assert not walk.dropped


class TestOutputShapes:
    """What a model writes is not always what it reads."""

    def test_a_serialization_alias_declares_an_output_type(self):
        class User(BaseModel):
            user_name: str = Field(serialization_alias='userName')

        walk = Walk()
        assert walk.model(User) == 'User'
        with walk.output():
            assert walk.model(User) == 'UserOutput'
        assert list(walk.types['User'].properties) == ['user_name']
        assert list(walk.types['UserOutput'].properties) == ['userName']
        parsed_types(walk)

    def test_an_excluded_field_and_a_computed_one(self):
        class User(BaseModel):
            first: str
            secret: str = Field(exclude=True)

            @computed_field  # type: ignore[prop-decorator]
            @property
            def initial(self) -> str:
                return self.first[:1]

        walk = Walk()
        with walk.output():
            name = walk.model(User)
        assert walk.types[name].render()['properties'] == {'first': 'string', 'initial': 'string'}

    def test_a_model_that_writes_what_it_reads_is_declared_once(self):
        class Plain(BaseModel):
            a: int

        walk = Walk()
        walk.model(Plain)
        with walk.output():
            assert walk.model(Plain) == 'Plain'
        assert list(walk.types) == ['Plain']

    def test_a_model_holding_a_diverging_one_diverges_too(self):
        class Inner(BaseModel):
            a: int = Field(serialization_alias='A')

        class Outer(BaseModel):
            inner: list[Inner]

        walk = Walk()
        with walk.output():
            assert walk.model(Outer) == 'OuterOutput'
        assert walk.types['OuterOutput'].properties['inner'].type == 'InnerOutput[]'
