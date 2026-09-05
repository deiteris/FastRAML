"""The effective view — docs/16-graph.md § 9.

What is worth pinning is what a reader would be misled by, not the layout:

- **every inherited property is present**, which is the whole reason the view
  exists;
- **each one is attributed to the right declaration** — the furthest ancestor
  that declares it, unless a subtype narrowed it, in which case the subtype;
- **a referenced type is named**, not flattened to `object`;
- **the output is valid YAML**, because § 9 claims it pastes back.

Layout is deliberately not asserted beyond that. A test that pins column
alignment fails on every wording change and protects nothing.
"""

from __future__ import annotations

import re

import pytest
import yaml

from pyraml import ParseOptions, parse_from_path
from pyraml.graph import build_graph
from pyraml.render import render

LIB = """#%RAML 1.0 Library
types:
  Entity:
    type: object
    properties:
      id:
        type: string
        maxLength: 36
  Audited:
    type: object
    properties:
      changedBy: string
  User:
    type: Entity
    properties:
      name: string
      address: Address
  Address:
    type: object
    properties:
      city: string
      country:
        type: string
        enum: [uk, us, de]
  Admin:
    type: [User, Audited]
    properties:
      level: integer
  Narrowed:
    type: Entity
    properties:
      id:
        type: string
        maxLength: 8
  UserList: User[]
  Node:
    type: object
    properties:
      child?: Node
  Priced:
    type: object
    properties:
      rate:
        type: number
        multipleOf: 1.1
  Either:
    type: string | integer
"""


@pytest.fixture
def shown(workspace):
    root = workspace({'lib.raml': LIB})
    raml = parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True))
    graph = build_graph(raml)

    def show(name: str, depth: int = 1) -> str:
        found = graph.find(name)
        assert found, f'{name} not found'
        shape = graph.shape_at(found[0])
        assert shape is not None, f'{name} is not a type'
        return '\n'.join(render(shape, depth=depth, root=graph.root))

    return show


def loaded(text: str) -> dict:
    return yaml.safe_load(text)


class TestItIsTheEffectiveType:
    def test_inherited_properties_are_present(self, shown):
        """The point of the whole thing: `Admin` declares one property and has five.

        `level` is its own; `name` and `address` come from `User`; `id` from
        `Entity` two links up; `changedBy` from the second parent.
        """
        properties = loaded(shown('Admin'))['Admin']['properties']
        assert set(properties) == {'level', 'name', 'address', 'id', 'changedBy'}

    def test_an_inherited_constraint_comes_with_it(self, shown):
        assert loaded(shown('Admin'))['Admin']['properties']['id']['maxLength'] == 36

    def test_the_parents_are_named(self, shown):
        assert loaded(shown('Admin'))['Admin']['inherits'] == ['User', 'Audited']

    def test_an_optional_property_keeps_its_question_mark(self, shown):
        assert 'child?' in loaded(shown('Node'))['Node']['properties']


class TestOrigins:
    """Where each property was really written — the question a merged type raises."""

    @staticmethod
    def notes(text: str) -> dict[str, str]:
        found = {}
        for line in text.splitlines():
            body, _, note = line.partition('  # ')
            key = body.strip().split(':')[0]
            if note:
                found[key] = note
        return found

    def test_a_grandparents_property_names_the_grandparent(self, shown):
        """Not the nearest parent. `Admin.id` is on `User` too after unwrap, and
        `Entity` is the answer a reader wants.
        """
        assert self.notes(shown('Admin'))['id'].startswith('Entity,')

    def test_an_own_property_names_no_ancestor(self, shown):
        assert not self.notes(shown('Admin'))['level'].startswith(('User', 'Entity', 'Audited'))

    def test_a_narrowed_property_is_attributed_to_the_narrower(self, shown):
        """`Narrowed` re-declares `id` with a tighter bound, so it owns it.

        This is the case the view exists to settle — is the limit 36 or 8? —
        and attributing it to `Entity` would answer it wrongly.
        """
        text = shown('Narrowed')
        assert loaded(text)['Narrowed']['properties']['id']['maxLength'] == 8
        assert not self.notes(text)['id'].startswith('Entity')

    def test_the_note_carries_a_path_and_a_line(self, shown):
        """A relative path, not a bare file name: two `common.raml` under
        different directories is exactly the project that needs this view.
        """
        assert re.fullmatch(r'Entity, lib\.raml:\d+', self.notes(shown('Admin'))['id'])


class TestNamingRatherThanFlattening:
    def test_a_property_of_a_declared_type_is_named(self, shown):
        """`alias`, not `type` — reading `type` prints `object`, which is useless."""
        assert loaded(shown('User'))['User']['properties']['address'] == 'Address'

    def test_an_array_names_its_member_type(self, shown):
        assert loaded(shown('UserList'))['UserList']['items'] == 'User'

    def test_a_union_lists_its_members(self, shown):
        assert loaded(shown('Either'))['Either']['anyOf'] == ['string', 'integer']

    def test_a_recursive_property_names_the_type_it_closes_back_to(self, shown):
        """Its own `type` is `recursive`, which says nothing about which cycle."""
        text = shown('Node')
        assert loaded(text)['Node']['properties']['child?'] == 'Node'
        assert 'recursive' in text


class TestDepth:
    def test_one_level_names_a_nested_type_without_opening_it(self, shown):
        assert loaded(shown('User', depth=1))['User']['properties']['address'] == 'Address'

    def test_two_levels_open_it(self, shown):
        address = loaded(shown('User', depth=2))['User']['properties']['address']
        assert set(address['properties']) == {'city', 'country'}

    def test_depth_does_not_expand_a_scalar_into_a_block(self, shown):
        """`name:` followed by `type: string` is two lines saying what one said."""
        assert loaded(shown('User', depth=3))['User']['properties']['name'] == 'string'

    def test_a_cycle_terminates_however_deep_it_is_asked_to_go(self, shown):
        assert loaded(shown('Node', depth=10))['Node']['properties']['child?'] == 'Node'


class TestOutputContract:
    @pytest.mark.parametrize('name', ['Admin', 'User', 'UserList', 'Node', 'Either', 'Priced', 'Address'])
    def test_it_is_valid_yaml(self, shown, name):
        """§ 9 claims the output pastes back, so it has to parse."""
        assert loaded(shown(name, depth=2)) is not None

    def test_facets_use_raml_spelling_not_the_slot_name(self, shown):
        assert 'maxLength' in shown('Entity')
        assert 'max_length' not in shown('Entity')

    def test_a_decimal_facet_does_not_pass_through_float(self, shown):
        """docs/10 § 5.2 is about comparison, but `1.100000000000000088`
        reaching a reader would be this module's defect all the same.
        """
        assert 'multipleOf: 1.1' in shown('Priced')
