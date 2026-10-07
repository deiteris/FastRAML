"""Author-facing hover explains source meaning and labels effective information."""

from __future__ import annotations

import pytest

from fastraml.service import queries
from tests.unit.test_service_queries import _buffered, _where

DOCUMENT = """#%RAML 1.0
title: Hover
types:
  Entity:
    properties:
      id: integer
  Word:
    type: string
    minLength: 2
  Name:
    type: Word
    maxLength: 40
    description: |
      **A human name.**

      Keep the second paragraph too.
  User:
    type: Entity
    properties:
      name?: Name
      nickname: string?
      literal?:
        type: string
        required: true
  UserAlias: User
securitySchemes:
  basic:
    type: Basic Authentication
traits:
  paged:
    usage: Use pagination for long lists.
    queryParameters:
      limit:
        type: integer
        maximum: <<max>>
resourceTypes:
  collection:
    get:
      description: Lists the collection.
/users:
  type: collection
  get:
    is: [paged: {max: 100}]
    queryParameters:
      search?: string
    responses:
      404:
        description: The user does not exist.
"""


@pytest.fixture
def hover(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None

    def at(needle, nth=0, offset=0):
        line, column = _where(DOCUMENT, needle, nth)
        result = queries.hover(snapshot, f'{folder}/api.raml', line, column + offset)
        assert result is not None, needle
        return result

    return at


class TestSubjectAndSummary:
    def test_description_is_markdown_and_keeps_all_paragraphs(self, hover):
        text, _ = hover('Name:')
        assert '**A human name.**\n\nKeep the second paragraph too.' in text
        assert '```' not in text
        assert 'Effective summary' not in text
        assert 'Declared at' not in text

    def test_type_and_alias_have_different_meanings(self, hover):
        subtype, _ = hover('User:')
        alias, _ = hover('UserAlias:')
        assert 'data type' in subtype
        assert 'Specializes `Entity`' in subtype
        assert 'Alias of `User`' in alias
        assert 'Specializes' not in alias

    def test_optional_presence_and_nullable_value_are_distinct(self, hover):
        optional, optional_span = hover('name?:')
        nullable, _ = hover('nickname:')
        literal, _ = hover('literal?:')
        assert 'object property' in optional
        assert 'Presence: **optional**' in optional
        assert 'Uses `Name` as its type' in optional
        assert 'Alias of' not in optional
        assert optional_span.end_column - optional_span.column == len('name')
        assert 'Presence: **required**' in nullable
        assert '`string | nil`' in nullable
        assert '**`literal?`**' in literal
        assert 'Presence: **required**' in literal
        assert 'trailing `?` is part of the name' in literal

    def test_query_parameter_is_not_reported_as_an_object_property(self, hover):
        text, span = hover('search?:')
        assert 'query parameter' in text
        assert 'Presence: **optional**' in text
        assert span.end_column - span.column == len('search?')

    def test_base_uri_parameter_has_its_binding_and_documentation(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\nbaseUri: https://{host}\nbaseUriParameters:\n'
            '  host:\n    type: string\n    minLength: 2\n    description: The API hostname.\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'host:'))
        assert 'base URI parameter' in text
        assert 'Presence: **required**' in text
        assert 'The API hostname.' in text
        assert 'Effective summary' not in text

    def test_a_pattern_property_explains_name_matching(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    properties:\n      /^x-/: integer\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, '/^x-/'))
        assert 'pattern property' in text
        assert 'searched against property names' in text
        assert 'Explicit properties take precedence' in text
        assert span.end_column - span.column == len('/^x-/')

    def test_large_types_keep_hover_focused_on_documentation(self, memory_workspace):
        properties = ''.join(f'      field{i}: string\n' for i in range(12))
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Large:\n    properties:\n' + properties
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        result = queries.hover(
            workspace.snapshot(f'{folder}/api.raml'), f'{folder}/api.raml', *_where(document, 'Large:')
        )
        assert result is not None
        text, _ = result
        assert 'data type' in text
        assert 'field0' not in text
        assert 'Effective summary' not in text

    def test_long_constraints_are_left_to_the_effective_view(self, memory_workspace):
        pattern = '^' + 'a' * 200 + '$'
        document = f'#%RAML 1.0\ntitle: T\ntypes:\n  Long:\n    type: string\n    pattern: {pattern}\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'Long:'))
        assert pattern not in text

    def test_inherited_description_is_available_in_both_file_contexts(self, memory_workspace):
        files = {
            'api.raml': '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  Child:\n    type: lib.Word\n',
            'lib.raml': '#%RAML 1.0 Library\ntypes:\n  Word:\n    type: string\n    description: Shared **prose**.\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        child, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(files['api.raml'], 'Child:'))
        assert 'Shared **prose**.' in child
        parent, _ = queries.hover(snapshot, f'{folder}/lib.raml', *_where(files['lib.raml'], 'Word:'))
        assert 'Shared **prose**.' in parent
        assert 'Declared at' not in parent

    def test_trait_shows_usage_and_caller_supplied_parameters(self, hover):
        text, _ = hover('paged: {')
        assert '— trait' in text
        assert 'reusable method template' in text
        assert 'Use pagination for long lists.' in text
        assert 'Template parameters: `max`' in text

    def test_materialized_template_expression_is_not_labeled_as_written(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntraits:\n  picked:\n    queryParameters:\n'
            '      q:\n        type: <<kind>>\n/a:\n  get:\n    is: [picked: {kind: string}]\n'
            '/b:\n  get:\n    is: [picked: {kind: integer}]\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'q:'))
        assert 'query parameter · `string`' in text
        assert 'Declared type: `string`' not in text
        assert 'several materializations' in text

    def test_processor_supplied_variables_are_not_shown_as_caller_parameters(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntraits:\n  named:\n    description: <<methodName>>\n/a:\n  get:\n    is: [named]\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'named]'))
        assert 'Processor-supplied parameters: `methodName`' in text
        assert 'Template parameters: `methodName`' not in text


class TestContextualDocumentation:
    def test_type_key_has_three_contextual_meanings(self, hover):
        data, _ = hover('type: Word')
        resource, _ = hover('type: collection')
        mechanism, _ = hover('type: Basic Authentication')
        assert 'base data type or type expression' in data
        assert 'resource type template' in resource
        assert 'authentication mechanism' in mechanism
        assert 'not a data-type reference' in mechanism

    def test_facet_explanation_does_not_repeat_the_local_value(self, hover):
        text, span = hover('maxLength:')
        assert 'maximum length' in text
        assert 'Effective value:' not in text
        assert span.end_column - span.column == len('maxLength')

    def test_method_and_status_help_have_http_references(self, hover):
        method, span = hover('get:', 1)
        response, _ = hover('404:')
        assert '**GET**' in method
        assert 'Resource: `/users`' in method
        assert '/Methods/GET' in method
        assert span.end_column - span.column == len('get')
        assert '404 Not Found' in response
        assert 'The user does not exist.' in response
        assert '/Status/404' in response

    def test_keys_in_values_are_not_raml_keywords_or_http_statuses(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    type: object\n'
            '    example:\n      type: string\n      get: hello\n      404: missing\n'
            '    default:\n      properties: {}\n      required: false\n'
            '  EnumData:\n    type: object\n    enum:\n      - {responses: {}, get: hi}\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        for needle in (
            'type: string',
            'get: hello',
            '404: missing',
            'properties: {}',
            'required: false',
            'responses: {}',
            'get: hi',
        ):
            assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle)) is None

    def test_property_named_type_is_a_property_not_the_type_facet(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    properties:\n      type: string\n      get: boolean\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        for needle in ('type: string', 'get: boolean'):
            text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle))
            assert 'object property' in text
            assert 'RAML field' not in text
            assert 'HTTP method' not in text

    def test_key_help_survives_a_semantic_error_before_binding(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    type: object\n    properties: []\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        assert not snapshot.raml.unwrapped
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'properties:'))
        assert 'Declares object properties' in text
        declaration, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'Data:'))
        assert 'data type' in declaration
        assert 'Effective summary' not in declaration

    def test_a_builtin_explains_allowed_values_and_supported_facets(self, hover):
        integer, _ = hover('integer')
        string, _ = hover('string', offset=2)
        assert 'whole number' in integer
        assert '`int32`' in integer
        assert '`multipleOf`' in integer
        assert 'Unicode characters' in string
        assert '`pattern`' in string
        assert 'RAML reference' in integer

    def test_hover_does_not_extend_past_the_target_token(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    properties: {} # comment\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        for needle in ('{}', '# comment', 'comment'):
            assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle)) is None

    def test_builtin_documentation_does_not_require_template_materialization(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntraits:\n  unused:\n    queryParameters:\n      q: string | nil\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        for needle, explanation in (('string', 'Unicode characters'), ('nil', 'Accepts only null')):
            text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle))
            assert 'built-in RAML type' in text
            assert explanation in text
            assert span.end_column - span.column == len(needle)
        parameter, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'q:'))
        assert 'query parameter declaration' in parameter

    def test_expanded_yaml_alias_does_not_turn_example_keys_into_method_keys(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    type: object\n'
            '    example: &methods\n      get: {}\n/r: *methods\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'get:')) is None

    def test_literal_includes_inherit_their_receiving_syntax(self, memory_workspace):
        files = {
            'api.raml': '#%RAML 1.0\ntitle: T\n/r:\n  get: !include method.yaml\n',
            'method.yaml': 'queryParameters: !include parameters.yaml\nresponses:\n  404: {}\n',
            'parameters.yaml': 'q:\n  type: string\n  minLength: 2\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        for name, needle, explanation in (
            ('method.yaml', 'queryParameters', 'individual query parameters'),
            ('method.yaml', '404', '404 Not Found'),
            ('parameters.yaml', 'minLength', 'minimum length'),
            ('parameters.yaml', 'q:', 'query parameter'),
        ):
            text, _ = queries.hover(snapshot, f'{folder}/{name}', *_where(files[name], needle))
            assert explanation in text

    @pytest.mark.parametrize('conflicting', [False, True])
    def test_conflicting_include_contexts_suppress_keyword_help(self, memory_workspace, conflicting):
        files = {
            'api.raml': (
                '#%RAML 1.0\ntitle: T\ntypes:\n  Data:\n    properties: !include shared.yaml\n'
                + ('    example: !include shared.yaml\n' if conflicting else '')
            ),
            'shared.yaml': 'q:\n  type: any\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        result = queries.hover(snapshot, f'{folder}/shared.yaml', *_where(files['shared.yaml'], 'type:'))
        if conflicting:
            assert result is None
        else:
            assert result is not None
            assert 'base data type or type expression' in result[0]

    def test_json_schema_keywords_are_not_presented_as_raml_keywords(self, memory_workspace):
        schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
        files = {'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Data: !include schema.json\n', 'schema.json': schema}
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        for needle in ('type', 'properties', 'string'):
            assert queries.hover(snapshot, f'{folder}/schema.json', *_where(schema, needle)) is None

    def test_custom_facet_named_uses_gets_its_documentation_instead_of_import_help(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Base:\n    type: string\n    facets:\n'
            '      uses:\n        type: string\n        description: What this value is used for.\n'
            '  Child:\n    type: Base\n    uses: hello\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'uses: hello'))
        assert 'What this value is used for.' in text
        assert 'Supplied value on' not in text
        assert 'Imports libraries' not in text

    def test_a_dotted_local_type_is_not_mistaken_for_a_library_reference(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  lib.User: string\n  Use: lib.User\n'
        library = '#%RAML 1.0 Library\ntypes:\n  User: integer\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document, 'lib.raml': library})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        line, column = _where(document, 'lib.User', 1)
        for offset in (0, len('lib.')):
            text, span = queries.hover(snapshot, f'{folder}/api.raml', line, column + offset)
            assert '**`lib.User`** — data type · `string`' in text
            assert 'library namespace' not in text
            assert span.column == column
            assert span.end_column == column + len('lib.User')
        definition = queries.definition(snapshot, f'{folder}/api.raml', line, column)
        assert [(site.uri, site.span.line) for site in definition] == [(f'{folder}/api.raml', line - 1)]

    @pytest.mark.parametrize(
        ('header', 'body', 'needle', 'explanation'),
        [
            ('DataType', 'type: string\nminLength: 2\n', 'minLength', 'minimum length'),
            ('Trait', 'queryParameters:\n  q: string\n', 'queryParameters', 'individual query parameters'),
            ('ResourceType', 'get?:\n', 'get?', 'optional method'),
            ('SecurityScheme', 'type: Basic Authentication\n', 'type', 'authentication mechanism'),
        ],
    )
    def test_typed_fragments_use_their_own_root_grammar(self, memory_workspace, header, body, needle, explanation):
        document = f'#%RAML 1.0 {header}\n{body}'
        workspace, folder = _buffered(memory_workspace, {'fragment.raml': document})
        snapshot = workspace.snapshot(f'{folder}/fragment.raml')
        text, _ = queries.hover(snapshot, f'{folder}/fragment.raml', *_where(document, needle))
        assert explanation in text


class TestCustomFacetDocumentation:
    @pytest.mark.parametrize('optional', [False, True])
    def test_declaration_and_supplied_value_show_full_authored_documentation(self, memory_workspace, optional):
        name = 'label?' if optional else 'label'
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Base:\n    type: string\n    facets:\n'
            f'      {name}:\n        type: string\n        minLength: 1\n        description: |\n'
            '          **A reader-facing label.**\n\n          Explain how this type appears in a UI.\n'
            '  Child:\n    type: Base\n    label: Display name\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        for needle in (name + ':', 'label: Display'):
            text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle))
            assert '**A reader-facing label.**\n\nExplain how this type appears in a UI.' in text
            assert 'custom facet declaration' in text
            assert 'not a property of an API payload' in text
            requirement = 'may' if optional else 'must'
            assert f'Subtypes **{requirement} supply this facet**' in text
            assert 'Effective summary' not in text
            assert span.end_column - span.column == len('label')
        assert 'Supplied value on' not in text

    def test_a_transitively_inherited_library_facet_keeps_its_declaration_documentation(self, memory_workspace):
        files = {
            'lib.raml': (
                '#%RAML 1.0 Library\ntypes:\n  Base:\n    type: string\n    facets:\n'
                '      label?:\n        type: string\n        description: Library label documentation.\n'
                '  Mid:\n    type: Base\n'
            ),
            'api.raml': '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  Child:\n    type: lib.Mid\n    label: Name\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(files['api.raml'], 'label:'))
        assert 'Library label documentation.' in text
        assert 'Declared at' not in text
        sites = queries.definition(snapshot, f'{folder}/api.raml', *_where(files['api.raml'], 'label:'))
        assert [(site.uri, site.span.line) for site in sites] == [(f'{folder}/lib.raml', 6)]

    def test_known_facet_keeps_documentation_when_its_supplied_value_is_invalid(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Base:\n    type: string\n    facets:\n'
            '      count:\n        type: integer\n        description: Number of consumers.\n'
            '  Child:\n    type: Base\n    count: bad\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'count: bad'))
        assert 'Number of consumers.' in text
        assert 'custom facet declaration · `integer`' in text

    def test_unknown_facets_and_keys_inside_facet_data_have_no_invented_documentation(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  Base:\n    type: string\n    facets:\n'
            '      payload: object\n  Child:\n    type: Base\n'
            '    payload:\n      type: string\n    unknown:\n      properties: {}\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'payload:', 1))
        assert 'custom facet declaration' in text
        for needle in ('type: string\n    unknown:', 'unknown:', 'properties: {}'):
            assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle)) is None


class TestExplanatoryDocumentation:
    def test_type_help_explains_union_and_multiple_inheritance_instead_of_only_naming_the_field(self, hover):
        text, _ = hover('type: Word')
        assert 'User | nil' in text
        assert 'Alternatives accept either member' in text
        assert 'multiple inheritance must satisfy both parents' in text
        assert 'Alias: User' in text

    def test_facet_help_explains_effect_with_a_concrete_example(self, hover):
        text, _ = hover('maxLength:')
        assert 'Unicode characters for a string, bytes for a file' in text
        assert '`maxLength: 40` accepts values of length 40 or less' in text
        assert 'not a request to truncate content' in text

    def test_builtin_help_explains_anchored_patterns_instead_of_only_listing_facets(self, hover):
        text, _ = hover('string?')
        assert 'anchored pattern' in text
        assert 'merely contain matching text' in text

    def test_method_help_explains_http_behavior(self, hover):
        text, _ = hover('get:', 1)
        assert 'Retrieves a representation' in text
        assert 'without requesting a change to server state' in text
        assert 'possible responses' in text
