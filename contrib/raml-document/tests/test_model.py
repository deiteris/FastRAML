"""What the model renders, and that a parser accepts it.

The second half is the point of testing a *document* model rather than a string
builder: every construct here is built, rendered, and parsed back by fastraml with
`validate=True`. A facet spelled wrongly fails here rather than in whichever
emitter first used it.
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Any

import pytest
import yaml
from fastraml import ParseOptions, parse_from_path

from raml_document import (
    UNSET,
    Body,
    Document,
    Method,
    Resource,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
)


def parsed(document: Document) -> Any:
    """`document`, through fastraml. Raises if it is not valid RAML."""
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / 'api.raml'
        source.write_text(document.to_raml(), encoding='utf-8')
        return parse_from_path(source, ParseOptions(unwrap=True, validate=True))


class TestTypeDeclCollapses:
    def test_a_bare_type_renders_as_a_string(self):
        assert TypeDecl(type='string').render() == 'string'

    def test_a_type_expression_renders_as_a_string(self):
        assert TypeDecl(type='Cat | Dog').render() == 'Cat | Dog'
        assert TypeDecl(type='string[]').render() == 'string[]'

    @pytest.mark.parametrize(
        ('decl', 'why'),
        [
            (TypeDecl(type='string', pattern='^x'), 'a facet'),
            (TypeDecl(type='string', required=False), 'optionality'),
            (TypeDecl(type='string', default='x'), 'a default'),
            (TypeDecl(type='object', properties={'a': TypeDecl(type='string')}), 'properties'),
        ],
    )
    def test_anything_more_renders_as_a_mapping(self, decl, why):
        assert isinstance(decl.render(), dict), why

    def test_a_declaration_with_no_type_is_still_a_mapping(self):
        assert TypeDecl(properties={'a': TypeDecl(type='string')}).render() == {'properties': {'a': 'string'}}


class TestUnsetIsNotNone:
    """`default: ~` is a value; not writing `default:` is not."""

    def test_an_unwritten_default_is_absent(self):
        assert 'default' not in TypeDecl(type='string', pattern='^x').render()

    def test_a_null_default_is_written(self):
        rendered = TypeDecl(type='nil', default=None, required=False).render()
        assert rendered['default'] is None

    def test_the_same_holds_for_discriminator_value(self):
        assert 'discriminatorValue' not in TypeDecl(type='object', discriminator='k').render()
        assert TypeDecl(type='Base', discriminator_value=None, description='x').render()['discriminatorValue'] is None

    def test_unset_is_falsy_free(self):
        # A caller testing `if decl.default:` would be wrong for `default: 0`;
        # the sentinel makes the right test an identity one.
        assert TypeDecl(type='integer', default=0).render()['default'] == 0
        assert UNSET is not None


class TestFacetSpelling:
    def test_snake_case_fields_render_as_camel_case_raml(self):
        rendered = TypeDecl(
            type='string',
            min_length=1,
            max_length=4,
            display_name='A name',
        ).render()
        assert set(rendered) == {'type', 'displayName', 'minLength', 'maxLength'}

    def test_status_codes_render_as_strings(self):
        # Keyed by int so `sorted` orders numerically; written as a string so
        # the render output stays a plain str-keyed mapping.
        method = Method(responses={404: Response(description='no'), 200: Response(description='ok')})
        rendered = method.render()
        assert list(rendered['responses']) == ['200', '404']

    def test_secured_by_has_two_spellings(self):
        assert SecuredBy(scheme='oauth').render() == 'oauth'
        assert SecuredBy(scheme='oauth', scopes=['read']).render() == {'oauth': {'scopes': ['read']}}


class TestResourceNesting:
    def test_at_creates_each_segment(self):
        root = Resource()
        root.at('/books/{isbn}').methods['get'] = Method()
        assert list(root.children) == ['/books']
        assert list(root.children['/books'].children) == ['/{isbn}']

    def test_at_is_idempotent(self):
        root = Resource()
        assert root.at('/books/{isbn}') is root.at('/books/{isbn}')

    def test_a_shared_prefix_is_one_node(self):
        root = Resource()
        root.at('/books')
        root.at('/books/{isbn}')
        assert list(root.children) == ['/books']

    def test_the_base_uri_is_a_resource_and_not_the_document_root(self):
        """An API answering on its own base URI writes `/:`, a relative URI like
        any other. Returning the document root instead would put `get:` beside
        `title:`, where RAML has no method node and a parser rejects it."""
        document = Document(title='T')
        document.root.at('/').methods['get'] = Method()
        rendered = document.render()
        assert 'get' not in rendered
        assert rendered['/'] == {'get': {}}


class TestParsesBack:
    def test_the_smallest_document(self):
        raml = parsed(Document(title='T'))
        assert raml.entry_point.title.value == 'T'

    def test_types_endpoints_and_security(self):
        document = Document(
            title='Library',
            version='v2',
            base_uri='https://api.example/{ver}',
            base_uri_parameters={'ver': TypeDecl(type='string', default='v2')},
            types={
                'Book': TypeDecl(
                    type='object',
                    additional_properties=False,
                    properties={
                        'isbn': TypeDecl(type='string', pattern=r'^\d{13}$'),
                        'pages': TypeDecl(type='integer', minimum=1, required=False, default=100),
                    },
                ),
                'Shelf': TypeDecl(type='array', items=TypeDecl(type='Book')),
            },
            security_schemes={
                'oauth': SecurityScheme(
                    type='OAuth 2.0',
                    settings={
                        'authorizationGrants': ['authorization_code'],
                        'authorizationUri': 'https://a/auth',
                        'accessTokenUri': 'https://a/tok',
                        'scopes': ['read'],
                    },
                )
            },
        )
        books = document.root.at('/books')
        books.methods['get'] = Method(
            display_name='Every book',
            query_parameters={'q': TypeDecl(type='string', required=False)},
            headers={'trace': TypeDecl(type='string', required=False)},
            responses={200: Response(body=Body({'application/json': TypeDecl(type='Shelf')}))},
            secured_by=[SecuredBy(scheme='oauth', scopes=['read'])],
        )
        item = document.root.at('/books/{isbn}')
        item.uri_parameters['isbn'] = TypeDecl(type='string', pattern=r'^\d{13}$')
        item.methods['put'] = Method(
            body=Body({'application/json': TypeDecl(type='Book')}),
            responses={204: Response(description='updated')},
        )

        raml = parsed(document)
        types = raml.types_in(raml.location)
        assert sorted(types) == ['Book', 'Shelf']
        assert sorted(raml.endpoints) == ['/books', '/books/{isbn}']

    def test_a_discriminated_hierarchy(self):
        document = Document(
            title='Pets',
            types={
                'Pet': TypeDecl(type='object', discriminator='kind', properties={'kind': TypeDecl(type='string')}),
                'Cat': TypeDecl(
                    type='Pet',
                    discriminator_value='cat',
                    properties={'kind': TypeDecl(type='string', enum=['cat']), 'meows': TypeDecl(type='boolean')},
                ),
                'Dog': TypeDecl(
                    type='Pet',
                    discriminator_value='dog',
                    properties={'kind': TypeDecl(type='string', enum=['dog']), 'barks': TypeDecl(type='boolean')},
                ),
                'Holder': TypeDecl(type='object', properties={'pet': TypeDecl(type='Cat | Dog')}),
            },
        )
        raml = parsed(document)
        holder = raml.types_in(raml.location)['Holder']
        assert holder.validate({'pet': {'kind': 'cat', 'meows': True}}) is None
        assert holder.validate({'pet': {'kind': 'fish'}}) is not None

    def test_a_recursive_type(self):
        document = Document(
            title='Tree',
            types={
                'Node': TypeDecl(
                    type='object',
                    properties={
                        'name': TypeDecl(type='string'),
                        'children': TypeDecl(type='array', items=TypeDecl(type='Node'), required=False),
                    },
                )
            },
        )
        raml = parsed(document)
        node = raml.types_in(raml.location)['Node']
        assert node.validate({'name': 'r', 'children': [{'name': 'c'}]}) is None

    def test_a_pattern_any_property(self):
        document = Document(
            title='Tags',
            types={'Tags': TypeDecl(type='object', properties={'//': TypeDecl(type='string')})},
        )
        tags = parsed(document)
        shape = tags.types_in(tags.location)['Tags']
        assert shape.validate({'anything': 'x'}) is None
        assert shape.validate({'anything': 1}) is not None


def test_the_header_is_written_once():
    text = Document(title='T').to_raml()
    assert text.startswith('#%RAML 1.0\n')
    assert text.count('#%RAML') == 1


def test_render_is_plain_yaml_safe():
    # No custom encoder anywhere: `render()` returns only builtin types.
    yaml.safe_dump(Document(title='T', types={'A': TypeDecl(type='string')}).render())
