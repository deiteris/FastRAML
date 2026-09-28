"""Types an endpoint declares inline, which have no name to be generated from.

`fixtures/sample/api.raml` declares every body by name — `type: Book`,
`type: Delivery[]` — so the whole anonymous-naming path is unreachable from it,
and every name it produces was untested until this file. Changing the shared
fixture to reach it would move four other consumers (docs/17 § 2), so this
document is its own. `DOCUMENT` below is the source; `tests/inline.json` is that
document projected, and is what the tests actually read.

Where a name comes from, in order:

1. what the author *labelled* it, `displayName:` — the only one of the four a
   person wrote as a name;
2. where it sits, for a body or a response — `POST /orders` has exactly one
   request body, so `PostOrdersBody` is unambiguous and says where to look;
3. the property it hangs off, for a nested object — `shipTo` is a `ShipTo`;
4. its address, which is always there and always distinct.

The document also declares `Entry`, whose property names Python cannot spell as
attributes: `@odata.type`, `$ref`, `class`, `1st`, `userId` beside `user_id`,
and `json` and `model_config`, which a pydantic model already has. `GET
/entries` takes query parameters spelled like the arguments its server method
already has. The shared fixture has none of those either.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from conftest import HERE

from raml_codegen import Settings, generate

#: `tests/inline.json` is this document, projected. It is kept here because it
#: is what a reader needs to check the names below against, and because it is
#: what to edit before regenerating the JSON beside it.
DOCUMENT = """#%RAML 1.0
title: Inline API
mediaType: [application/json]
types:
  Order:
    properties:
      sku: string
  HasHome:
    properties:
      home: string
  OnFarm:
    properties:
      farm: string
  Cat:
    properties:
      purrs: boolean
  Dog:
    properties:
      barks: boolean
  Homely:
    type: [HasHome, Cat | Dog]
  Kept:
    type: [HasHome | OnFarm, Cat | Dog]
  Entry:
    properties:
      '@odata.type': string
      $ref?: string
      class: integer
      1st: boolean
      userId: string
      user_id: string
      json: string
      model_config: string
/entries:
  get:
    queryParameters:
      body: string
      self: string
      userId: string
      user_id: string
      1st: boolean
    responses:
      200:
        body:
          application/json:
            type: Entry
/drafts:
  post:
    body:
      application/json:
        type: Order
  put:
    body:
      application/json:
        type: Order
        properties:
          draft: boolean
/orders:
  post:
    body:
      application/json:
        properties:
          sku: string
          shipTo:
            properties:
              street: string
    responses:
      201:
        body:
          application/json:
            properties:
              id: string
      200:
        body:
          application/json:
            displayName: Existing order
            properties:
              id: string
  get:
    responses:
      200:
        body:
          application/json:
            type: array
            items:
              properties:
                id: string
/invoices:
  get:
    responses:
      200:
        body:
          application/json:
            type: array
            items:
              properties:
                total: number
"""


#: How a generated model is declared: `Name = TypedDict(`.
_DECLARED = re.compile(r'^(\w+) = TypedDict\(', re.MULTILINE)


def declares(name):
    return f'{name} = TypedDict('


@pytest.fixture(scope='module')
def generated():
    document = json.loads((HERE / 'inline.json').read_text(encoding='utf-8'))
    return generate(document, 'python-httpx', Settings())


@pytest.fixture(scope='module')
def models(generated):
    return {
        name.rsplit('/', 1)[-1].removesuffix('.py'): text
        for name, text in generated.files.items()
        if '/models/' in name and not name.endswith('__init__.py')
    }


class TestABodyIsNamedForWhereItSits:
    def test_a_request_body(self, models):
        assert declares('PostOrdersBody') in models['post_orders_body']

    def test_a_response_body_carries_its_status(self, models):
        # `POST /orders` documents two, and they are different types. The status
        # is the only thing that tells them apart, so it is in the name.
        assert declares('PostOrders201Response') in models['post_orders201_response']

    def test_it_is_not_named_after_the_media_type(self, models):
        # `name` on a body shape is `application/json`, which says how the value
        # was sent and nothing about what it is.
        assert not any(name.startswith('application') for name in models)
        assert "TypedDict('ApplicationJson" not in ''.join(models.values())


class TestALabelBeatsAPosition:
    def test_display_name_wins(self, models):
        # The author wrote `displayName: Existing order`, which is a name. A
        # generated `PostOrders200Response` would be this package overruling
        # them with a description of where the type happens to live.
        assert declares('ExistingOrder') in models['existing_order']
        assert 'post_orders200_response' not in models


class TestANestedObjectTakesItsProperty:
    def test_the_property_name(self, models):
        assert declares('ShipTo') in models['ship_to']

    def test_the_holder_refers_to_it(self, models):
        assert "'shipTo': 'ShipTo'" in models['post_orders_body']


class TestInlineArrayItems:
    def test_items_are_named_after_their_array(self, models):
        # Without this they are called `items`, which is the structure's word
        # for the position rather than anybody's word for the type.
        assert declares('GetOrders200ResponseItem') in models['get_orders200_response_item']

    def test_two_inline_arrays_do_not_collide(self, models):
        # Both would be `Items`, and the second would silently become `Items2`
        # -- a name that says nothing and changes when a third arrives.
        assert declares('GetInvoices200ResponseItem') in models['get_invoices200_response_item']
        assert not any(name.startswith('items') for name in models)

    def test_the_array_is_typed_by_its_items(self, generated):
        endpoint = generated.files['inline_api/api/orders/get_orders.py']
        assert 'list[GetOrders200ResponseItem]' in endpoint


class TestExtendingANamedTypeIsTheSamePathAsAnInlineOne:
    """A shape is its supertype only when it adds nothing to it.

    The rule is about properties, not names: `type: Order` with nothing added
    *is* `Order`, and `type: Order` plus one property is a new type that takes
    its name the anonymous way, exactly as a body declared from scratch does.
    """

    def test_adding_nothing_generates_nothing(self, generated):
        assert 'body: Order' in generated.files['inline_api/api/drafts/post_drafts.py']

    def test_adding_a_property_generates_a_model_named_for_where_it_sits(self, models):
        assert declares('PutDraftsBody') in models['put_drafts_body']

    def test_that_model_is_flat(self, models):
        # Not a `TypedDict` subclass of `Order`: the tree merged the supertype
        # before this package saw it, and RAML inheritance has no subclass form
        # anyway. So `sku` is a key of its own here.
        assert declares('PutDraftsBody') in models['put_drafts_body']
        assert "'draft': 'bool'" in models['put_drafts_body']
        assert "'sku': 'str'" in models['put_drafts_body']


class TestEveryNameIsDistinct:
    def test_no_model_is_generated_twice(self, models):
        declared = [found for text in models.values() for found in _DECLARED.findall(text)]
        assert declared
        assert len(declared) == len(set(declared))

    def test_every_model_has_its_own_module(self, models):
        assert len(models) == len(set(models))


class TestAVariantIsNamedForTheMembersItTook:
    """An inherited union's variants are anonymous (docs/07 § 5).

    Named from the address they would be `TypesHomelyAnyOf0`, which says nothing
    about which is which. Each variant inherits the members it took, and those
    are what tell it apart from its siblings.
    """

    def test_the_member_the_union_took(self, models):
        assert 'HomelyCat | HomelyDog' in models['homely']
        assert declares('HomelyCat') in models['homely_cat']
        assert declares('HomelyDog') in models['homely_dog']

    def test_one_name_per_pair_of_members(self, models):
        # Neither parent is named: both are unions, and every variant took one
        # member of each.
        assert 'KeptHasHomeCat | KeptOnFarmCat | KeptHasHomeDog | KeptOnFarmDog' in models['kept']

    def test_no_variant_is_named_for_its_address(self, models):
        assert not any('any_of' in name for name in models)


class TestAnyPropertyNameIsAKey:
    """A client model is keyed by the wire names, so no property needs a Python name.

    An attribute would have to rename `@odata.type` and `1st`, and `userId` and
    `user_id` would become one attribute that silently holds either.
    """

    NAMES = ('@odata.type', '$ref', 'class', '1st', 'userId', 'user_id')

    def test_every_name_is_a_key_as_written(self, models):
        for name in self.NAMES:
            assert f'{name!r}: ' in models['entry'], name

    @pytest.fixture(scope='class')
    @staticmethod
    def written(generated, tmp_path_factory):
        destination = tmp_path_factory.mktemp('inline')
        generated.write(destination)
        return destination

    def test_the_model_passes_mypy_strict(self, written):
        # The functional `TypedDict` form is the only one that takes these keys,
        # and its field types are strings a type checker has to resolve.
        result = _run(['-m', 'mypy', 'inline_api'], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_reader_reads_them(self, written):
        payload = {
            '@odata.type': '#Entry',
            'class': 1,
            '1st': True,
            'userId': 'a',
            'user_id': 'b',
            'json': 'c',
            'model_config': 'd',
        }
        check = f"""
from inline_api.models import read_entry
from inline_api.types import reading
payload = {payload!r}
with reading() as found:
    assert read_entry(payload) is payload
assert found == []
with reading() as found:
    read_entry({{'userId': 'a'}})
missing = ['1st', '@odata.type', 'class', 'json', 'model_config', 'user_id']
assert sorted(one.field for one in found) == missing, found
"""
        result = _run(['-c', check], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr


class TestAServerNamesWhatPydanticCannotTake:
    """A server model is a pydantic class, so every property needs an attribute.

    `field_name` alone gave `1st` the name `_1st`, which pydantic reads as a
    private attribute and drops; gave `userId` and `user_id` one attribute; and
    gave `model_config` a name that stops the class being built. `GET /entries`
    takes query parameters spelled like the arguments its method already has.
    """

    @pytest.fixture(scope='class')
    @staticmethod
    def served():
        document = json.loads((HERE / 'inline.json').read_text(encoding='utf-8'))
        return generate(document, 'python-fastapi', Settings())

    @pytest.fixture(scope='class')
    @staticmethod
    def entry(served):
        return served.files['inline_api/models/entry.py']

    def test_a_name_that_starts_with_a_digit_is_not_private(self, entry):
        assert "field_1st: Annotated[bool, Field(alias='1st')]" in entry

    def test_two_properties_that_snake_alike_keep_two_attributes(self, entry):
        # The first declared keeps the spelling.
        assert "user_id: Annotated[str, Field(alias='userId')]" in entry
        assert "user_id2: Annotated[str, Field(alias='user_id')]" in entry

    def test_a_name_basemodel_already_has_is_suffixed(self, entry):
        assert "json_: Annotated[str, Field(alias='json')]" in entry
        assert "model_config_: Annotated[str, Field(alias='model_config')]" in entry

    def test_a_parameter_the_method_already_takes_is_suffixed(self, served):
        routes = served.files['inline_api/api/entries.py']
        assert "body_: Annotated[str, Query(alias='body')]" in routes
        assert "self_: Annotated[str, Query(alias='self')]" in routes

    @pytest.fixture(scope='class')
    @staticmethod
    def written(served, tmp_path_factory):
        destination = tmp_path_factory.mktemp('inline-server')
        served.write(destination)
        return destination

    def test_it_passes_mypy_strict(self, written):
        result = _run(['-m', 'mypy', 'inline_api'], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_name_crosses_the_wire_both_ways(self, written):
        # Query in, model out: each parameter reaches the method under its own
        # attribute, and each property leaves under its own key.
        check = """
import asyncio

import httpx

from inline_api import Api, create_app
from inline_api.models import Entry


async def stub(self, **arguments):
    raise NotImplementedError


async def get_entries(self, *, body_, self_, user_id, user_id2, field_1st):
    return Entry.model_validate({
        '@odata.type': body_, 'class': 1, '1st': field_1st, 'userId': user_id,
        'user_id': user_id2, 'json': self_, 'model_config': 'c',
    })


Implementation = type('Implementation', (Api,), {**dict.fromkeys(Api.__abstractmethods__, stub), 'get_entries': get_entries})


async def main():
    transport = httpx.ASGITransport(app=create_app(Implementation()))
    async with httpx.AsyncClient(transport=transport, base_url='http://x') as client:
        query = {'body': 'b', 'self': 's', 'userId': 'u', 'user_id': 'v', '1st': 'true'}
        response = await client.get('/entries', params=query)
    assert response.status_code == 200, response.text
    got = response.json()
    expected = {
        '@odata.type': 'b', 'class': 1, '1st': True, 'userId': 'u',
        'user_id': 'v', 'json': 's', 'model_config': 'c',
    }
    assert {key: got[key] for key in expected} == expected, got


asyncio.run(main())
"""
        result = _run(['-c', check], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr


class TestAnOperationWithNoResponses:
    """`/drafts` documents no `responses:`, which RAML allows."""

    def test_the_server_is_still_generated(self):
        document = json.loads((HERE / 'inline.json').read_text(encoding='utf-8'))
        routes = generate(document, 'python-fastapi', Settings()).files['inline_api/api/drafts.py']
        # Its `Responses` is empty, so there is no status to show `fail` with.
        assert 'POST_DRAFTS = Responses({})' in routes
        assert 'The document names no response here' in routes


def _run(arguments, cwd):
    return subprocess.run([sys.executable, *arguments], capture_output=True, text=True, cwd=cwd, check=False)
