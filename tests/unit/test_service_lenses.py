"""Code lenses locate declarations; effective rendering happens on demand."""

from fastraml.service import lenses, queries
from tests.unit.test_service_hover import DOCUMENT
from tests.unit.test_service_queries import _buffered, _where


def test_effective_view_renders_all_inherited_and_authored_members(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    found = next(lens for lens in lenses.code_lenses(snapshot, uri) if lens.name == 'User')
    text = lenses.effective_type(snapshot, uri, found.span.line, found.span.column, name=found.name)
    assert text is not None
    assert text.startswith('#%RAML 1.0 DataType\n')
    assert 'id:' in text
    assert 'name' in text
    assert 'nickname' in text
    hover, _ = queries.hover(snapshot, uri, *_where(DOCUMENT, 'User:'))
    assert 'Effective summary' not in hover
    assert 'Declared at' not in hover
    assert 'id:' not in hover


def test_lenses_do_not_offer_an_effective_view_for_a_stopped_parse(memory_workspace):
    document = DOCUMENT + 'unknown: true\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert not lenses.code_lenses(snapshot, uri)
    assert lenses.effective_type(snapshot, uri, *_where(document, 'User:'), name='User') is None


def test_stale_name_and_location_are_not_reused_after_edit(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert lenses.effective_type(snapshot, uri, *_where(DOCUMENT, 'User:'), name='Other') is None
    assert lenses.effective_type(snapshot, uri, 1, 1, name='User') is None
    renamed = DOCUMENT.replace('User:', 'Member:').replace('UserAlias: User', 'UserAlias: Member')
    workspace.change(uri, renamed, 2)
    current = workspace.snapshot(uri)
    assert current is not snapshot
    assert lenses.effective_type(current, uri, *_where(DOCUMENT, 'User:'), name='User') is None
    text = lenses.effective_type(current, uri, *_where(renamed, 'Member:'), name='Member')
    assert text is not None
    assert 'Member:' in text


def test_effective_type_expands_nested_objects_arrays_and_union_members_to_full_depth(memory_workspace):
    import yaml

    document = """#%RAML 1.0
title: T
types:
  Leaf:
    properties:
      value:
        type: string
        minLength: 2
  Branch:
    properties:
      leaves: Leaf[]
  Tree:
    properties:
      branch: Branch
  Choice: Tree | Leaf
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    text = lenses.effective_type(workspace.snapshot(uri), uri, *_where(document, 'Choice:'), name='Choice')
    assert text is not None
    choice = yaml.safe_load(text)['Choice']['anyOf']
    value = choice[0]['properties']['branch']['properties']['leaves']['items']['properties']['value']
    assert value['minLength'] == 2
    assert choice[1]['properties']['value']['minLength'] == 2


def test_full_depth_effective_type_stops_at_recursive_references(memory_workspace):
    document = '#%RAML 1.0\ntitle: T\ntypes:\n  Node:\n    properties:\n      children?: Node[]\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    text = lenses.effective_type(workspace.snapshot(uri), uri, *_where(document, 'Node:'), name='Node')
    assert text is not None
    assert 'Node[]' in text
    assert len(text.splitlines()) < 20


def test_full_depth_keeps_scalar_item_constraints_and_structured_facet_declarations(memory_workspace):
    import yaml

    document = """#%RAML 1.0
title: T
types:
  Collection:
    type: array
    items:
      type: string
      minLength: 3
    facets:
      metadata?:
        type: object
        properties:
          count:
            type: integer
            minimum: 0
"""
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    text = lenses.effective_type(workspace.snapshot(uri), uri, *_where(document, 'Collection:'), name='Collection')
    assert text is not None
    shown = yaml.safe_load(text)['Collection']
    assert shown['items']['minLength'] == 3
    assert shown['facets']['metadata?']['properties']['count']['minimum'] == 0


def test_full_depth_scalar_facet_recursion_remains_a_named_reference(memory_workspace):
    import yaml

    document = '#%RAML 1.0\ntitle: T\ntypes:\n  Code:\n    type: string\n    facets:\n      metadata?: Code\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert snapshot.error is None
    text = lenses.effective_type(snapshot, uri, *_where(document, 'Code:'), name='Code')
    assert text is not None
    assert yaml.safe_load(text)['Code']['facets']['metadata?'] == 'Code'
    assert len(text.splitlines()) < 15
