"""DataNode hover uses the bound value type, not the RAML keyword grammar."""

import pytest

from fastraml.service import queries
from tests.unit.test_service_queries import _buffered, _where

DOCUMENT = """#%RAML 1.0
title: Typed data hover
types:
  Person:
    properties:
      name:
        type: string
        description: The person's **display name**.
  Tag:
    type: string
    description: A label used for grouping.
  Settings:
    properties:
      user: Person
      tags: Tag[]
      type?:
        type: string
        description: A literal data field, not a RAML type expression.
  Base:
    type: string
    facets:
      config: Settings
  Child:
    type: Base
    config:
      user:
        name: Ada
      tags: [admin, reader]
      type: literal
  DefaultPerson:
    type: Person
    default: {name: Default}
  ExamplePerson:
    type: Person
    examples:
      regular:
        value: {name: Example}
  EnumPerson:
    type: Person
    enum:
      - {name: Allowed}
annotationTypes:
  settings: Settings
  flag:
    type: boolean
    description: Enables the documented feature.
(settings):
  user:
    name: Grace
  tags: [author]
(flag): false
"""


@pytest.fixture
def typed(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None

    def hover(needle, offset=0):
        line, column = _where(DOCUMENT, needle)
        result = queries.hover(snapshot, f'{folder}/api.raml', line, column + offset)
        assert result is not None, needle
        return result

    return hover


@pytest.mark.parametrize('name', ['Ada', 'Grace', 'Default', 'Example', 'Allowed'])
def test_nested_keys_and_scalars_share_the_property_documentation(typed, name):
    key, key_span = typed('name: ' + name)
    value, value_span = typed('name: ' + name, len('name: '))
    for text in (key, value):
        assert "The person's **display name**." in text
        assert 'object property' in text
        assert 'Presence: **required**' in text
    assert key_span.end_column - key_span.column == len('name')
    assert value_span.end_column - value_span.column == len(name)


@pytest.mark.parametrize('item', ['admin', 'reader', 'author'])
def test_array_elements_follow_the_items_type(typed, item):
    text, span = typed(item)
    assert 'array item' in text
    assert 'A label used for grouping.' in text
    assert span.end_column - span.column == len(item)


def test_ramllike_data_field_names_use_their_schema_description(typed):
    text, _ = typed('type: literal')
    assert 'A literal data field, not a RAML type expression.' in text
    assert 'object property' in text
    assert 'RAML field' not in text
    assert 'base data type or type expression' not in text


def test_scalar_annotation_value_has_the_bound_annotation_documentation(typed):
    text, span = typed('(flag): false', len('(flag): '))
    assert 'annotation value' in text
    assert 'Enables the documented feature.' in text
    assert 'Value in' not in text
    assert span.end_column - span.column == len('false')


def test_comments_whitespace_and_untyped_fields_have_no_parent_fallback(memory_workspace):
    document = DOCUMENT.replace('name: Grace', 'name: Grace # comment\n    extra: unknown')
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    for needle in ('# comment', 'comment', 'extra: unknown', 'unknown'):
        assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle)) is None
    line, column = _where(document, 'name: Grace')
    assert queries.hover(snapshot, f'{folder}/api.raml', line, column - 1) is None


def test_typed_fields_are_documented_even_when_values_are_invalid(memory_workspace):
    document = DOCUMENT.replace('name: Grace', 'name: 42')
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is not None
    text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'name: 42'))
    assert "The person's **display name**." in text


def test_root_and_nested_includes_keep_each_values_file_and_span(memory_workspace):
    document = DOCUMENT.replace(
        '(settings):\n  user:\n    name: Grace\n  tags: [author]', '(settings): !include settings.yaml'
    )
    files = {
        'api.raml': document,
        'settings.yaml': 'user: !include person.yaml\ntags: [author]\n',
        'person.yaml': 'name: Included\n',
    }
    workspace, folder = _buffered(memory_workspace, files)
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    for name, needle, explanation in (
        ('settings.yaml', 'user:', 'object property'),
        ('settings.yaml', 'author', 'A label used for grouping.'),
        ('person.yaml', 'name:', "The person's **display name**."),
        ('person.yaml', 'Included', "The person's **display name**."),
    ):
        result = queries.hover(snapshot, f'{folder}/{name}', *_where(files[name], needle))
        assert result is not None
        text, span = result
        assert explanation in text
        assert (span.line, span.column) == _where(files[name], needle)


def test_included_named_examples_use_the_owning_type_for_data_hover(memory_workspace):
    document = DOCUMENT.replace(
        'examples:\n      regular:\n        value: {name: Example}', 'examples: !include examples.raml'
    )
    examples = '#%RAML 1.0 NamedExample\nregular:\n  value:\n    name: Included example\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document, 'examples.raml': examples})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    text, span = queries.hover(snapshot, f'{folder}/examples.raml', *_where(examples, 'name:'))
    assert "The person's **display name**." in text
    assert 'Declared at' not in text
    assert (span.line, span.column) == _where(examples, 'name:')


def test_an_unbound_annotation_does_not_invent_field_descriptions(memory_workspace):
    document = DOCUMENT.replace('(settings):', '(missing):')
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is not None
    assert queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'name: Grace')) is None


def test_nested_data_fields_have_go_to_definition_instead_of_location_text(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    for offset in (0, len('name: ')):
        line, column = _where(DOCUMENT, 'name: Grace')
        sites = queries.definition(snapshot, uri, line, column + offset)
        assert [(site.uri, site.span.line) for site in sites] == [(uri, _where(DOCUMENT, 'name:\n')[0])]


def test_inline_json_does_not_invent_nested_field_positions(memory_workspace):
    document = DOCUMENT.replace(
        '(settings):\n  user:\n    name: Grace\n  tags: [author]',
        """(settings): '{"user": {"name": "Grace"}, "tags": ["author"]}'""",
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'Grace'))
    assert 'annotation value' in text
    assert "The person's **display name**." not in text
    assert span.end_column - span.column > len('Grace')


def test_inherited_and_pattern_properties_use_the_models_matching_order(memory_workspace):
    document = """#%RAML 1.0
title: T
types:
  Parent:
    properties:
      inherited?:
        type: string
        description: An inherited field.
      /x/:
        type: string
        description: First matching pattern.
  Child:
    type: Parent
    properties:
      exact?:
        type: string
        description: Explicit field wins.
      /^x-/:
        type: string
        description: Later pattern.
annotationTypes:
  data: Child
(data):
  inherited: text
  exact: literal
  prefix-x-name: searched
  x-name: both
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    for needle, description in (
        ('inherited: text', 'An inherited field.'),
        ('exact: literal', 'Explicit field wins.'),
        ('prefix-x-name:', 'First matching pattern.'),
        ('x-name: both', 'First matching pattern.'),
    ):
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, needle))
        assert description in text
        assert 'Later pattern.' not in text


@pytest.mark.parametrize('tag', ['cat', 'dog', 'unknown'])
def test_discriminator_selects_nested_documentation_without_validating_the_whole_value(memory_workspace, tag):
    document = f"""#%RAML 1.0
title: T
types:
  Pet:
    discriminator: kind
    properties:
      kind: string
  Cat:
    type: Pet
    discriminatorValue: cat
    properties:
      name:
        type: string
        description: The cat's name.
  Dog:
    type: Pet
    discriminatorValue: dog
    properties:
      name:
        type: string
        description: The dog's name.
annotationTypes:
  pet: Cat | Dog
(pet):
  kind: {tag}
  name: 42
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is not None
    result = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'name: 42'))
    if tag == 'unknown':
        assert result is None
    else:
        assert result is not None
        assert f"The {tag}'s name." in result[0]
        other = 'dog' if tag == 'cat' else 'cat'
        assert f"The {other}'s name." not in result[0]


def test_undiscriminated_union_keeps_both_possible_property_descriptions(memory_workspace):
    document = """#%RAML 1.0
title: T
types:
  A:
    properties:
      name:
        type: string
        description: Meaning in A.
  B:
    properties:
      name:
        type: string
        description: Meaning in B.
annotationTypes:
  either: A | B
(either): {name: ambiguous}
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'name: ambiguous'))
    assert 'Meaning in A.' in text
    assert 'Meaning in B.' in text
    assert 'no single alternative is assumed' in text


def test_recursive_types_are_followed_only_as_far_as_the_data_goes(memory_workspace):
    document = """#%RAML 1.0
title: T
types:
  Tree:
    properties:
      name:
        type: string
        description: The tree node name.
      children?: Tree[]
annotationTypes:
  tree: Tree
(tree):
  name: root
  children:
    - name: child
      children:
        - name: leaf
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    for name in ('root', 'child', 'leaf'):
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'name: ' + name))
        assert 'The tree node name.' in text
