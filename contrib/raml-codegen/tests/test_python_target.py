"""What the `python` target decides, named decision by decision.

Every assertion here is about a Python spelling. None of them is about RAML:
the language ran nine passes before this package saw anything (docs/16 § 11.7),
and a test here that needed a RAML rule would be evidence of a gap in the
parser rather than a test of this.
"""

from __future__ import annotations

import pytest

from raml_codegen.targets import Settings
from raml_codegen.targets.python.annotate import make_annotator
from raml_codegen.targets.python.emit import _RESERVED
from raml_codegen.targets.shared.plan import plan


@pytest.fixture(scope='session')
def package(tree):
    return plan(tree, Settings(), make_annotator, _RESERVED)


def model(package, name):
    return next(one for one in package.models if one.name == name)


def endpoint(package, method, path):
    return next(one for one in package.endpoints if one.method == method and one.path == path)


def annotation_of(package, model_name, field):
    return next(one for one in model(package, model_name).fields if one.wire == field).annotation


class TestThePackage:
    def test_it_is_named_from_the_title(self, package):
        assert package.distribution == 'bookstore-api'
        assert package.module == 'bookstore_api'

    def test_the_caller_may_name_it_instead(self, tree):
        assert plan(tree, Settings(package='books-client'), make_annotator, _RESERVED).distribution == 'books-client'

    def test_a_templated_base_uri_keeps_its_tokens(self, package):
        # What `{tenant}` stands for is the caller's to supply; substituting a
        # placeholder would be inventing a value the document does not state.
        assert package.base_uri == 'https://{tenant}.books.example.com/{version}'


class TestShapesBecomeTypes:
    def test_a_scalar_is_a_scalar(self, package):
        assert annotation_of(package, 'Book', 'title').spelling == 'str'
        # `number` is `float` and `integer` is `int`. The parser is careful to
        # keep a *facet* out of a float; a JSON body arrives through
        # `json.loads` and is one already, so there is nothing here to protect.
        assert annotation_of(package, 'Money', 'amount').spelling == 'float'
        assert model(package, 'Grams').alias.spelling == 'int'

    def test_a_datetime_crosses_the_json_boundary(self, package):
        created = annotation_of(package, 'Book', 'createdAt')
        assert created.spelling == 'datetime.datetime'
        assert created.encode == '{}.isoformat()'
        assert created.decode == 'datetime.datetime.fromisoformat({})'
        assert 'datetime' in created.imports

    def test_an_object_with_properties_is_a_class(self, package):
        assert annotation_of(package, 'Book', 'price').spelling == 'Money'

    def test_an_object_with_none_is_a_mapping(self, package):
        # `additionalProperties` and nothing named: there is no class to make.
        assert annotation_of(package, 'Book', 'metadata').spelling == 'dict[str, Any]'

    def test_an_array_is_a_list_and_carries_its_items_conversion(self, package):
        prices = annotation_of(package, 'Book', 'priceHistory')
        assert prices.spelling == 'list[Money]'
        # `as_list` rather than iterating whatever arrived: a `dict` where an
        # array was promised yields its keys, and the client would hand back
        # models built from strings with nothing having gone wrong.
        assert prices.decode == '[Money.from_dict(_item) for _item in as_list({})]'

    def test_an_enum_is_a_literal_and_not_a_class(self, package):
        # RAML's `enum:` is a list of *values* with no names attached, so an
        # `Enum` subclass would have to invent identifiers for them.
        assert annotation_of(package, 'Barcode', 'symbology').spelling == ("Literal['EAN-13', 'UPC-A', 'CODE-128']")

    def test_a_union_is_a_union(self, package):
        assert model(package, 'Payload').alias.spelling == 'list[Book] | Review'

    def test_a_union_converts_both_ways(self, package):
        # A union is the one field that does not know its own type until it has
        # a value, so identity conversion is wrong in both directions: `json=`
        # would be handed a dataclass, and a parsed response would be a `dict`
        # where the annotation promised a model.
        payload = model(package, 'Payload').alias
        assert payload.encode == 'to_json({})'
        assert payload.decode == (
            '([Book.from_dict(_item) for _item in as_list({})] if isinstance({}, list) else Review.from_dict({}))'
        )
        assert 'to_json' in payload.runtime

    def test_a_nine_member_union_keeps_all_nine(self, package):
        # The spelling is what the document says, always. Widening it because
        # the *decode* cannot tell two members apart would throw away what the
        # author wrote in order to describe a limitation of this generator.
        anything = model(package, 'Anything').alias
        for name in ('Book', 'Review', 'Money', 'Publication', 'Magazine', 'Pamphlet', 'Delivery'):
            assert name in anything.spelling
        assert anything.spelling.count('|') == 8

    def test_a_union_uses_the_documents_own_discriminator(self, package):
        # `Magazine` states `discriminatorValue: monthly`, which is the author
        # saying how to recognise one. A required-property guess would be a
        # second answer to a question already answered.
        decode = model(package, 'Anything').alias.decode
        assert "Magazine.from_dict({}) if {}.get('kind') == 'monthly'" in decode

    def test_a_union_falls_back_to_a_property_only_one_member_requires(self, package):
        decode = model(package, 'Anything').alias.decode
        assert "Book.from_dict({}) if 'createdAt' in {}" in decode

    def test_what_the_document_does_not_distinguish_is_handed_back(self, package):
        # Two array members, and nothing says which is which. A `ShelfSlot` that
        # is really a `Money` would be worse than an undecoded list.
        decode = model(package, 'Anything').alias.decode
        assert decode.startswith('({} if isinstance({}, list) else')

    def test_no_discriminator_default_is_invented(self, package):
        # RAML defaults an unstated `discriminatorValue:` to the type name.
        # Applying that here would be this package holding a rule of the
        # language, so `Pamphlet` -- which states none -- is told apart by a
        # required property instead.
        decode = model(package, 'Anything').alias.decode
        assert "'Pamphlet'" not in decode
        assert "Pamphlet.from_dict({}) if 'pages' in {}" in decode

    def test_a_union_of_scalars_needs_no_conversion(self, package):
        search = model(package, 'Search').alias
        assert search.spelling == 'str | float'
        assert search.transparent

    def test_a_declared_scalar_is_an_alias_and_not_an_empty_class(self, package):
        isbn = model(package, 'Isbn')
        assert isbn.is_alias
        assert isbn.alias.spelling == 'str'

    def test_a_json_schema_type_reads_as_its_projection(self, package):
        # docs/16 § 11.10: `json` is how the type arrived, not what it is.
        invoice = model(package, 'Invoice')
        assert not invoice.is_alias
        assert {one.wire for one in invoice.fields} >= {'number', 'total'}


class TestADescriptionKeepsItsShape:
    """RAML says every `description:` is Markdown, so it may be a list."""

    def test_a_list_is_not_flattened_into_a_sentence(self, package):
        from raml_codegen.targets.shared.docs import details

        rendered = details(model(package, 'Book').description)
        assert '* `price` is the current price' in rendered
        # Collapsing the whole description to one line is what put a bullet in
        # the middle of the sentence above it.
        assert 'about: *' not in rendered

    def test_each_item_is_on_its_own_line(self, package):
        from raml_codegen.targets.shared.docs import details

        items = [line for line in details(model(package, 'Book').description).splitlines() if line.strip()[:1] == '*']
        assert len(items) == 2

    def test_a_paragraph_is_separated_from_the_list(self, package):
        from raml_codegen.targets.shared.docs import details

        rendered = details(model(package, 'Book').description)
        assert 'worth knowing about:' + chr(10) * 2 + '    * `price`' in rendered

    def test_a_pattern_is_never_wrapped_mid_token(self, package):
        # `^(?:[A-Z]{2}-` on one line and `)?(?:AISLE` on the next reads as part
        # of the pattern.
        from raml_codegen.targets.shared.docs import attribute

        locator = next(one for one in model(package, 'ShelfSlot').fields if one.wire == 'locator')
        wrapped = attribute(locator)
        assert '^(?:[A-Z]{2}-)?(?:AISLE|BAY|SHELF)' in wrapped


class TestInheritanceIsFlattened:
    """RAML inheritance has no Python subclass form, so none of it is one.

    The tree has already merged it (docs/16 § 11.3): what arrives is one object
    carrying every property, inherited and own alike, and `inherits` survives
    only as the reference the supertype check reads.
    """

    def test_a_subtype_carries_its_supertype_properties(self, package):
        magazine = model(package, 'Magazine')
        assert {one.wire for one in magazine.fields} == {'issue', 'kind', 'title'}

    def test_it_is_a_class_of_its_own_and_not_a_subclass(self, package):
        # `type: [A, B]` has no MRO and a narrowed property has no override, so
        # a subclass would be a rule with exceptions. Flat is one rule.
        assert model(package, 'Magazine').name == 'Magazine'
        assert model(package, 'Publication').name == 'Publication'

    def test_what_the_hierarchy_states_is_still_reachable(self, package):
        # Flattening loses `isinstance`, so the thing a caller switches on
        # instead has to survive.
        assert model(package, 'Magazine').discriminator == ('kind', 'monthly')

    def test_no_discriminator_value_is_invented(self, package):
        # `Pamphlet` states none. RAML defaults it to the type name; applying
        # that default here would be this package holding a rule of the language.
        assert model(package, 'Pamphlet').discriminator is None
        assert model(package, 'Publication').discriminator is None

    def test_adding_a_property_is_what_stops_the_supertype_collapse(self, package):
        # `body: Book` is `Book`; `body: Book` plus one property is not, and the
        # rule that decides it is about properties rather than about names.
        assert endpoint(package, 'post', '/books').body.annotation.spelling == 'Book'


class TestNamingFollowsTheAuthor:
    def test_an_anonymous_shape_takes_the_label_its_author_wrote(self, package):
        # `Shelf`'s `items:` is called `items` by the structure and `Shelf slot`
        # by its author. `Items` is the answer to a question nobody asked.
        assert any(one.name == 'ShelfSlot' for one in package.models)
        assert not any(one.name == 'Items' for one in package.models)

    def test_a_body_is_not_named_after_its_media_type(self, package):
        assert not any(one.name.startswith('ApplicationJson') for one in package.models)


class TestRecursionIsFiniteAndNamed:
    def test_a_recursive_property_names_its_own_class(self, package):
        chain = model(package, 'Chain')
        assert next(one for one in chain.fields if one.wire == 'next').annotation.spelling == 'Chain'

    def test_it_is_generated_once(self, package):
        assert [one.name for one in package.models].count('Chain') == 1


class TestConstraintsAreDocumentedAndNotEnforced:
    def test_a_pattern_reaches_the_docstring(self, package):
        assert 'pattern: ^\\d{13}$' in annotation_docs(package, 'Book', 'isbn')

    def test_an_exact_decimal_is_never_divided(self, package):
        # The tree carries a bound as text so no consumer rounds it through a
        # float. A client is no place to undo that.
        limit = next(one for one in endpoint(package, 'get', '/books').query_arguments if one.wire == 'limit')
        assert 'maximum: 100' in limit.docs

    def test_no_constraint_reaches_the_annotation(self, package):
        assert annotation_of(package, 'Book', 'isbn').spelling == 'str'


def annotation_docs(package, model_name, field):
    return next(one for one in model(package, model_name).fields if one.wire == field).docs


class TestEndpoints:
    def test_one_module_per_operation_grouped_by_first_segment(self, package):
        assert endpoint(package, 'get', '/books').group == 'books'
        assert endpoint(package, 'get', '/books/{isbn}').group == 'books'
        assert endpoint(package, 'get', '/books/{isbn}').module == 'get_books_isbn'

    def test_uri_parameters_come_in_the_order_the_path_uses_them(self, package):
        assert [one.wire for one in endpoint(package, 'get', '/books/{isbn}').uri_arguments] == ['isbn']

    def test_an_inherited_uri_parameter_the_path_does_not_mention_is_dropped(self, package):
        # The tree carries every parameter an endpoint inherited. An argument
        # that goes nowhere is worse than a missing one.
        for one in package.endpoints:
            for argument in one.uri_arguments:
                assert f'{{{argument.wire}}}' in one.path

    def test_a_query_string_becomes_query_parameters(self, package):
        # RAML says `queryString:` and `queryParameters:` are alternatives, not
        # that one is the other. Where the former is an object, its properties
        # are the query, and that is what a caller can use.
        deliveries = endpoint(package, 'get', '/deliveries')
        assert {one.wire for one in deliveries.query_arguments} >= {'since', 'status', 'city'}

    def test_a_body_is_the_first_declared_media_type(self, package):
        post = endpoint(package, 'post', '/books')
        assert post.body.media_type == 'application/json'

    def test_a_body_that_is_a_declared_type_is_that_type(self, package):
        # The effective view inlines a supertype (docs/16 § 11.3), so `body:
        # Book` arrives as an anonymous object carrying Book's properties. Read
        # literally that generates a duplicate class under a name the author
        # never wrote.
        assert endpoint(package, 'post', '/books').body.annotation.spelling == 'Book'

    def test_success_is_the_lowest_documented_2xx(self, package):
        assert endpoint(package, 'post', '/books').success.status == '201'
        assert endpoint(package, 'delete', '/books/{isbn}').success.status == '204'

    def test_every_documented_status_is_still_reachable(self, package):
        assert {case.status for case in endpoint(package, 'post', '/books').cases} == {'201', '400'}

    def test_there_are_no_response_classes(self, package):
        # RAML states 3-digit codes and nothing else (docs/08 § 3). `4xx` is
        # OpenAPI's, and arriving at it by analogy is the mistake docs/17 § 2.1
        # records against raml-mock.
        for one in package.endpoints:
            for case in one.cases:
                assert case.status.isdigit()
                assert len(case.status) == 3


class TestSecurity:
    def test_a_secured_operation_needs_an_authenticated_client(self, package):
        assert endpoint(package, 'post', '/books').requires_auth

    def test_a_null_scheme_beside_another_makes_it_optional(self, package):
        # `securedBy: [null, oauth2]` says the call may be made either way.
        book = endpoint(package, 'get', '/books/{isbn}')
        assert book.optional_auth
        assert not book.requires_auth

    def test_an_operation_names_the_schemes_it_accepts(self, package):
        assert 'oauth2' in endpoint(package, 'post', '/books').scheme_names

    def test_a_scheme_carries_where_its_credential_goes(self, package):
        # The point of reading the scheme at all: a client that always sent
        # `Authorization: Bearer` would be sending it where the API is not
        # reading, for any scheme that names its own header.
        oauth = next(one for one in package.schemes if one.name == 'oauth2')
        assert (oauth.header_name, oauth.prefix) == ('Authorization', 'Bearer')
        assert 'read:books' in oauth.scopes

    def test_basic_authentication_is_not_a_bearer_token(self, package):
        basic = next(one for one in package.schemes if one.name == 'basic')
        assert basic.prefix == 'Basic'

    def test_every_declared_scheme_reaches_the_package(self, package):
        assert {one.name for one in package.schemes} >= {'oauth2', 'machineToken', 'basic'}
