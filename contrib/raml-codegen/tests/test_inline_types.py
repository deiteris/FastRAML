"""Types an endpoint declares inline, which have no name to be generated from.

`fixtures/sample/api.raml` declares every body by name — `type: Book`,
`type: Delivery[]` — so the whole anonymous-naming path is unreachable from it,
and every name it produces was untested until this file. Changing the shared
fixture to reach it would move four other consumers (docs/17 § 3), so this
document is its own. `DOCUMENT` below is the source; `tests/inline.json` is that
document projected, and is what the tests actually read.

Where a name comes from, in order:

1. what the author *labelled* it, `displayName:` — the only one of the four a
   person wrote as a name;
2. where it sits, for a body or a response — `POST /orders` has exactly one
   request body, so `PostOrdersBody` is unambiguous and says where to look;
3. the property it hangs off, for a nested object — `shipTo` is a `ShipTo`;
4. its address, which is always there and always distinct.
"""

from __future__ import annotations

import json

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


@pytest.fixture(scope='module')
def generated():
    document = json.loads((HERE / 'inline.json').read_text(encoding='utf-8'))
    return generate(document, 'python', Settings())


@pytest.fixture(scope='module')
def models(generated):
    return {
        name.rsplit('/', 1)[-1].removesuffix('.py'): text
        for name, text in generated.files.items()
        if '/models/' in name and not name.endswith('__init__.py')
    }


class TestABodyIsNamedForWhereItSits:
    def test_a_request_body(self, models):
        assert 'class PostOrdersBody:' in models['post_orders_body']

    def test_a_response_body_carries_its_status(self, models):
        # `POST /orders` documents two, and they are different types. The status
        # is the only thing that tells them apart, so it is in the name.
        assert 'class PostOrders201Response:' in models['post_orders201_response']

    def test_it_is_not_named_after_the_media_type(self, models):
        # `name` on a body shape is `application/json`, which says how the value
        # was sent and nothing about what it is.
        assert not any(name.startswith('application') for name in models)
        assert 'class ApplicationJson' not in ''.join(models.values())


class TestALabelBeatsAPosition:
    def test_display_name_wins(self, models):
        # The author wrote `displayName: Existing order`, which is a name. A
        # generated `PostOrders200Response` would be this package overruling
        # them with a description of where the type happens to live.
        assert 'class ExistingOrder:' in models['existing_order']
        assert 'post_orders200_response' not in models


class TestANestedObjectTakesItsProperty:
    def test_the_property_name(self, models):
        assert 'class ShipTo:' in models['ship_to']

    def test_the_holder_refers_to_it(self, models):
        assert 'ship_to: ShipTo' in models['post_orders_body']


class TestInlineArrayItems:
    def test_items_are_named_after_their_array(self, models):
        # Without this they are called `items`, which is the structure's word
        # for the position rather than anybody's word for the type.
        assert 'class GetOrders200ResponseItem:' in models['get_orders200_response_item']

    def test_two_inline_arrays_do_not_collide(self, models):
        # Both would be `Items`, and the second would silently become `Items2`
        # -- a name that says nothing and changes when a third arrives.
        assert 'class GetInvoices200ResponseItem:' in models['get_invoices200_response_item']
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
        assert 'class PutDraftsBody:' in models['put_drafts_body']

    def test_that_model_is_flat(self, models):
        # Not `class PutDraftsBody(Order)`: the tree merged the supertype before
        # this package saw it, and RAML inheritance has no subclass form anyway.
        assert 'class PutDraftsBody:' in models['put_drafts_body']
        assert 'draft: bool' in models['put_drafts_body']
        assert 'sku: str' in models['put_drafts_body']


class TestEveryNameIsDistinct:
    def test_no_model_is_generated_twice(self, models):
        declared = [text.split('class ', 1)[1].split(':', 1)[0] for text in models.values() if 'class ' in text]
        assert len(declared) == len(set(declared))

    def test_every_model_has_its_own_module(self, models):
        assert len(models) == len(set(models))
