"""Names, and the fact that a name is not an identity."""

from __future__ import annotations

import pytest

from raml_codegen.naming import Names, class_name, field_name, from_address, module_name


class TestSpelling:
    @pytest.mark.parametrize(
        ('given', 'expected'),
        [
            ('Book', 'Book'),
            ('price-history', 'PriceHistory'),
            ('priceHistory', 'PriceHistory'),
            ('application/json', 'ApplicationJson'),
            # A leading digit is not a Python identifier; `Type` is the prefix
            # rather than an underscore, which reads as private.
            ('2fa', 'Type2fa'),
            ('', 'Unnamed'),
        ],
    )
    def test_class_names(self, given, expected):
        assert class_name(given) == expected

    @pytest.mark.parametrize(
        ('given', 'expected'),
        [
            ('PriceHistory', 'price_history'),
            ('books', 'books'),
            ('class', 'class_'),
            ('2fa', '_2fa'),
        ],
    )
    def test_module_names(self, given, expected):
        assert module_name(given) == expected

    @pytest.mark.parametrize(
        ('given', 'expected'),
        [
            ('title', 'title'),
            ('createdAt', 'created_at'),
            ('priceHistory', 'price_history'),
            # A keyword, and two builtins a reader would mistake for one.
            ('next', 'next_'),
            ('id', 'id_'),
            ('type', 'type_'),
            ('class', 'class_'),
        ],
    )
    def test_field_names(self, given, expected):
        assert field_name(given) == expected

    def test_a_field_name_is_not_reversible_so_the_wire_name_is_kept(self):
        # `created_at` and `createdAt` are the same attribute and different
        # keys. The generated `to_dict` carries the wire name for this reason.
        assert field_name('createdAt') == field_name('created_at')


class TestAnAddressIsTheIdentity:
    """docs/16 § 2: two files may declare one name, and it is silent."""

    def test_the_first_claimant_keeps_the_spelling(self):
        names = Names()
        assert names.claim('a#Page', 'Page') == 'Page'
        assert names.claim('a#Page', 'Page') == 'Page'

    def test_a_second_address_wanting_one_name_takes_the_alternative(self):
        names = Names()
        names.claim('sample#Page', 'Page')
        assert names.claim('shared#Page', 'Page', 'CommonPage') == 'CommonPage'

    def test_a_number_is_the_last_resort_and_not_the_first(self):
        names = Names()
        names.claim('a#Page', 'Page')
        names.claim('b#Page', 'Page', 'CommonPage')
        assert names.claim('c#Page', 'Page', 'CommonPage') == 'Page2'

    def test_a_reserved_name_is_never_handed_out(self):
        names = Names(frozenset({'types'}))
        assert names.claim('x', 'types', 'api_types') == 'api_types'


class TestAnonymousNodes:
    def test_a_node_with_no_name_is_named_from_its_address(self):
        address = 'fastraml://id#/web-api/endpoint/%2Fbooks/supportedOperation/post/request/payload/x/schema'
        assert from_address(address) == 'PostRequestPayloadX'

    def test_two_anonymous_nodes_do_not_collapse(self):
        first = from_address('fastraml://id#/a/post/request/payload/one/schema')
        second = from_address('fastraml://id#/a/post/request/payload/two/schema')
        assert first != second
