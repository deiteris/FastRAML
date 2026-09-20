"""What the `fastapi` target decides, named decision by decision.

Every assertion here is about a pydantic or FastAPI spelling. None of them is
about RAML: the language ran nine passes before this package saw anything
(docs/16 § 11.7), and a test here that needed a RAML rule would be evidence of a
gap in the parser rather than a test of this.

The comparison worth keeping in view is with `test_python_target.py`. The two
targets read the same tree through the same traversal and disagree in exactly
one place -- the client documents a facet and the server enforces it -- because
a client that validated could not say whether the server or the document was
wrong, and a server is the party that decides.
"""

from __future__ import annotations

import pytest
from conftest import envelope

from raml_codegen.reader import Tree
from raml_codegen.targets import Settings
from raml_codegen.targets.fastapi.plan import plan_server


@pytest.fixture(scope='session')
def package(tree):
    return plan_server(tree, Settings(package='bookstore-server'))


def model(package, name):
    return next(one for one in package.models if one.name == name)


def endpoint(package, method, path):
    return next(one for one in package.endpoints if one.method == method and one.path == path)


def field(package, model_name, wire):
    return next(one for one in model(package, model_name).fields if one.wire == wire)


class TestAFacetBecomesAConstraint:
    """The one place the two targets disagree, and the reason they do."""

    def test_a_pattern_is_enforced_rather_than_documented(self, package):
        assert field(package, 'Book', 'isbn').annotation.spelling == (
            "Annotated[str, Field(min_length=13, max_length=13, pattern=r'^\\d{13}$')]"
        )

    def test_a_pattern_is_written_raw_so_it_reads_as_the_author_wrote_it(self, package):
        assert "r'^\\d{13}$'" in field(package, 'Book', 'isbn').annotation.spelling

    def test_a_numeric_bound_keeps_the_exact_decimal_the_document_wrote(self, package):
        # The tree carries a bound as text so no consumer rounds it through a
        # float. Writing it out verbatim is how it stays that way -- nothing
        # here parses it into a float and back.
        limit = next(one for one in endpoint(package, 'get', '/books').query_arguments if one.wire == 'limit')
        assert 'Field(le=100)' in limit.annotation.spelling
        # `Parcel.tolerance` is written at the limits of a double, which is
        # where a round trip through `float` would show.
        assert field(package, 'Parcel', 'tolerance').annotation.spelling == (
            'Annotated[float, Field(ge=2.2250738585072014E-308, le=1.7976931348623157E+308)]'
        )

    def test_multiple_of_is_not_enforced(self, package):
        # pydantic compares it in binary floating point: with `multipleOf: 1.1`
        # it accepts 3.3000000000000003, which exact decimal arithmetic rejects.
        # Spelling the field `Decimal` to fix that would let a facet decide the
        # *type*, which is the one thing a constraint must not do.
        for one in package.models:
            for attribute in one.fields:
                assert 'multiple_of' not in attribute.annotation.spelling

    def test_multiple_of_still_reaches_the_docstring(self, package):
        amount = field(package, 'Money', 'amount')
        assert 'multipleOf: 0.01' in amount.docs
        assert amount.annotation.spelling == 'Annotated[float, Field(ge=0)]'

    def test_unique_items_becomes_a_validator_because_pydantic_has_none(self, package):
        # A `set` would be the wrong type: RAML's arrays are ordered and their
        # items need not be hashable.
        tags = field(package, 'Book', 'tags').annotation
        assert 'AfterValidator(unique_items)' in tags.spelling
        assert 'unique_items' in tags.runtime

    def test_array_counts_use_pydantics_names_for_them(self, package):
        assert 'Field(max_length=20)' in field(package, 'Book', 'reviews').annotation.spelling

    def test_the_documented_spelling_carries_no_constraint(self, package):
        # `isbn (str)` is what an `Args:` line should say. The whole
        # `Annotated[...]` blob is what the route needs, and repeating it in
        # prose beside the facets it already lists says nothing twice.
        assert field(package, 'Book', 'isbn').annotation.plain == 'str'
        assert field(package, 'Book', 'reviews').annotation.plain == 'list[Review]'


class TestShapesBecomeTypes:
    def test_a_scalar_with_no_facets_is_bare(self, package):
        assert field(package, 'Address', 'city').annotation.spelling == 'str'
        assert field(package, 'Page', 'total').annotation.spelling == 'int'

    def test_a_datetime_needs_no_conversion_form(self, package):
        # The client writes `.isoformat()` and `fromisoformat(...)`; pydantic
        # parses and serialises the type itself, so there is nothing to write.
        created = field(package, 'Book', 'createdAt').annotation
        assert created.spelling == 'datetime.datetime'
        assert created.transparent

    def test_an_object_with_properties_is_a_model(self, package):
        assert field(package, 'Book', 'price').annotation.spelling == 'Money'

    def test_an_object_with_none_is_a_mapping(self, package):
        assert field(package, 'Book', 'metadata').annotation.spelling == 'dict[str, Any]'

    def test_an_enum_is_a_literal_and_not_an_enum_class(self, package):
        # RAML's `enum:` is a list of *values* with no names attached, so an
        # `Enum` subclass would have to invent identifiers for them.
        assert field(package, 'Barcode', 'symbology').annotation.spelling == "Literal['EAN-13', 'UPC-A', 'CODE-128']"

    def test_a_declared_scalar_is_an_alias_and_carries_its_facets(self, package):
        isbn = model(package, 'Isbn')
        assert isbn.is_alias
        assert isbn.alias.spelling.startswith('Annotated[str, Field(')

    def test_a_json_schema_type_reads_as_its_projection(self, package):
        # docs/16 § 11.10: `json` is how the type arrived, not what it is.
        invoice = model(package, 'Invoice')
        assert not invoice.is_alias
        assert {one.wire for one in invoice.fields} >= {'number', 'total'}

    def test_a_recursive_property_names_its_own_class_once(self, package):
        assert field(package, 'Chain', 'next').annotation.spelling == 'Chain'
        assert [one.name for one in package.models].count('Chain') == 1


class TestUnions:
    def test_a_union_keeps_every_member_the_document_named(self, package):
        anything = model(package, 'Anything').alias
        assert anything.spelling.count('|') == 8

    def test_an_untagged_union_is_left_untagged(self, package):
        # `Anything` mixes objects, arrays and scalars, and `Pamphlet` states no
        # `discriminatorValue:`. pydantic tells them apart itself; a
        # `discriminator=` here would need every member to carry a tag and would
        # reject valid payloads for the ones that do not.
        assert 'discriminator' not in model(package, 'Anything').alias.spelling

    def test_no_discriminator_default_is_invented(self, package):
        # RAML defaults an unstated `discriminatorValue:` to the type name.
        # Applying that here would be this package holding a rule of the
        # language, and it is what would make the union above taggable.
        assert model(package, 'Pamphlet').discriminator is None

    def test_a_union_of_scalars_needs_nothing(self, package):
        assert model(package, 'Search').alias.spelling == 'str | float'

    def test_a_union_is_tagged_where_every_member_states_its_value(self, tagged):
        # Nothing in the fixture states one for every member, so this is built
        # rather than read: two types under one `discriminator:`, both with a
        # value, and the union over them.
        assert model(tagged, 'Either').alias.spelling == ("Annotated[Cat | Dog, Field(discriminator='kind')]")

    def test_a_tagged_members_discriminator_is_a_literal_of_its_value(self, tagged):
        # `Cat` states `discriminatorValue: cat`, so its `kind` is not a `str`
        # that happens to hold `cat` -- it is `cat`. That is reading the
        # document, and it is what makes the union above taggable at all.
        assert field(tagged, 'Cat', 'kind').annotation.spelling == "Literal['cat']"

    def test_an_untagged_members_discriminator_stays_a_string(self, tagged):
        assert field(tagged, 'Stray', 'kind').annotation.spelling == 'str'


class TestEndpointsBecomeMethods:
    def test_one_method_per_operation_named_uniquely(self, package):
        names = [one.module for one in package.endpoints]
        assert len(names) == len(set(names))
        assert 'get_books_isbn' in names

    def test_a_method_belongs_to_the_group_its_path_names(self, package):
        assert endpoint(package, 'get', '/books/{isbn}').group == 'books'

    def test_the_method_returns_the_lowest_documented_2xx(self, package):
        # A convention, not a RAML rule: RAML orders responses and says nothing
        # about which one is the answer.
        assert endpoint(package, 'post', '/books').success.status == '201'
        assert endpoint(package, 'delete', '/books/{isbn}').success.status == '204'

    def test_a_204_returns_nothing(self, package):
        assert endpoint(package, 'delete', '/books/{isbn}').success.annotation is None

    def test_there_are_no_response_classes(self, package):
        # RAML states 3-digit codes and nothing else (docs/08 § 3). `4xx` is
        # OpenAPI's, and arriving at it by analogy is the mistake docs/17 § 2.1
        # records against raml-mock.
        for one in package.endpoints:
            for case in one.cases:
                assert case.status.isdigit()
                assert len(case.status) == 3


class TestParametersKeepWhatTheDocumentGaveThem:
    def test_a_parameter_with_a_default_is_never_none(self, package):
        # The document gives `offset` a default, so a handler always has a
        # value. Widening it to `None` would make every caller check for
        # something that cannot happen.
        offset = next(one for one in endpoint(package, 'get', '/books').query_arguments if one.wire == 'offset')
        assert offset.default == '0'
        assert not offset.required

    def test_a_parameter_with_no_default_stays_optional(self, package):
        since = next(one for one in endpoint(package, 'get', '/deliveries').query_arguments if one.wire == 'since')
        assert since.default is None

    def test_a_uri_parameter_the_path_does_not_mention_is_dropped(self, package):
        for one in package.endpoints:
            for argument in one.uri_arguments:
                assert f'{{{argument.wire}}}' in one.path


class TestSecurity:
    def test_a_secured_operation_takes_a_credential(self, package):
        assert endpoint(package, 'post', '/books').requires_auth

    def test_a_null_scheme_beside_another_makes_the_credential_optional(self, package):
        # `securedBy: [null, oauth2]` says the call may be made either way, so
        # its absence is not an error.
        book = endpoint(package, 'get', '/books/{isbn}')
        assert book.optional_auth
        assert not book.requires_auth

    def test_an_operation_carries_the_scopes_it_narrowed_to(self, package):
        assert 'write:books' in endpoint(package, 'delete', '/books/{isbn}').scopes

    def test_a_scheme_carries_where_its_credential_is_read_from(self, package):
        # The point of reading the scheme at all: a server that only looked at
        # `Authorization: Bearer` would be looking where the caller is not
        # writing, for any scheme that names its own header.
        oauth = next(one for one in package.schemes if one.name == 'oauth2')
        assert (oauth.header_name, oauth.prefix) == ('Authorization', 'Bearer')

    def test_basic_authentication_is_not_a_bearer_token(self, package):
        assert next(one for one in package.schemes if one.name == 'basic').prefix == 'Basic'


class TestModelsAreFlat:
    """RAML inheritance has no pydantic subclass form, so none of it is one."""

    def test_a_subtype_carries_its_supertype_properties(self, package):
        assert {one.wire for one in model(package, 'Magazine').fields} == {'issue', 'kind', 'title'}

    def test_what_the_hierarchy_states_is_still_reachable(self, package):
        assert model(package, 'Magazine').discriminator == ('kind', 'monthly')

    def test_a_body_that_is_a_declared_type_is_that_type(self, package):
        # The effective view inlines a supertype (docs/16 § 11.3), so `body:
        # Book` arrives as an anonymous object carrying Book's properties. Read
        # literally that generates a duplicate class under a name the author
        # never wrote.
        assert endpoint(package, 'post', '/books').body.annotation.spelling == 'Book'


# -- a tree built here, for what the fixture does not state --------------------


def _object(address, name, properties, **extra):
    return {
        'id': address,
        'type': 'object',
        'name': name,
        'properties': {
            wire: {'required': True, 'type': {'id': f'{address}/{wire}', 'type': kind}}
            for wire, kind in properties.items()
        },
        **extra,
    }


@pytest.fixture(scope='session')
def tagged():
    """Two types under one `discriminator:`, both stating a value, and a union.

    Nothing in the fixture does this -- `Pamphlet` states no value -- so the one
    case where a union *can* be tagged has to be written out.
    """
    declared = {
        'Cat': _object(
            'x#/Cat', 'Cat', {'kind': 'string', 'lives': 'integer'}, discriminator='kind', discriminator_value='cat'
        ),
        'Dog': _object(
            'x#/Dog', 'Dog', {'kind': 'string', 'tricks': 'integer'}, discriminator='kind', discriminator_value='dog'
        ),
        'Stray': _object('x#/Stray', 'Stray', {'kind': 'string'}, discriminator='kind'),
        'Either': {
            'id': 'x#/Either',
            'type': 'union',
            'name': 'Either',
            'any_of': [{'$ref': 'x#/Cat'}, {'$ref': 'x#/Dog'}],
        },
    }
    return plan_server(Tree.of(envelope(types={'file://x.raml': declared})), Settings())
