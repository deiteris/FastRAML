"""Snapshot query caches share work without moving it into diagnostics."""

import pytest

from fastraml.positions import Position
from fastraml.service import index as index_module
from fastraml.service import inlays, outline, queries
from fastraml.service.datahover import DataHover
from fastraml.service.hover import Hover
from tests.unit.test_service_data_hover import DOCUMENT
from tests.unit.test_service_queries import API, COVER, LIBRARY, _buffered, _where


def test_diagnostics_do_not_populate_query_indices_and_declaration_queries_share_one_enumeration(
    memory_workspace, monkeypatch
):
    workspace, folder = _buffered(memory_workspace, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    queries.diagnostics(snapshot, lint=False)
    assert snapshot.occurrences is not None
    assert snapshot._semantic is None
    assert snapshot._data is None
    assert snapshot._hover is None
    assert not snapshot.outlines
    enumerations = 0
    original = index_module.every_declaration

    def declarations(raml):
        nonlocal enumerations
        enumerations += 1
        yield from original(raml)

    monkeypatch.setattr(index_module, 'every_declaration', declarations)
    first = outline.document_symbols(snapshot, uri)
    assert first
    assert outline.document_symbols(snapshot, uri) is first
    queries.workspace_symbols([snapshot], 'Book')
    assert snapshot.hover is not None
    assert enumerations == 1
    assert snapshot.semantic is not None
    assert snapshot.semantic._by_id is None
    assert snapshot.semantic._children is None


def test_typed_definition_before_hover_shares_one_data_population_without_presentation(memory_workspace, monkeypatch):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    line, column = _where(DOCUMENT, 'name: Grace')
    populations = 0
    original = DataHover._index

    def populate(context):
        nonlocal populations
        populations += 1
        return original(context)

    def presentation(*args, **kwargs):
        pytest.fail('typed navigation must not initialize hover presentation')

    monkeypatch.setattr(DataHover, '_index', populate)
    with monkeypatch.context() as isolated:
        isolated.setattr(Hover, '_index', presentation)
        assert queries.definition(snapshot, uri, line, column)
        assert queries.definition(snapshot, uri, line, column + len('name: '))
    assert snapshot._hover is None
    assert snapshot._semantic is None
    assert queries.hover(snapshot, uri, line, column) is not None
    assert inlays.inlay_hints(snapshot, uri, Position(line, column, line, column + 4))
    assert snapshot.hover._data is snapshot.data
    assert populations == 1


def test_reverse_hierarchy_preserves_aliases_multiple_parents_and_declaration_order(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  A: object\n  B: object\n'
        '  Alias: A\n  Both: {type: [B, A]}\n  Last: {type: A}\n  ViaAlias: {type: Alias}\n'
        '  Duplicate: {type: [A, A]}\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    a = queries.type_at(snapshot, uri, *_where(document, 'A: object'))
    assert a is not None
    assert snapshot.semantic._children is None
    assert [item.name for item in queries.subtypes(snapshot, a)] == ['Alias', 'Both', 'Last', 'Duplicate']
    children = snapshot.semantic.children
    assert [item.name for item in queries.subtypes(snapshot, a)] == ['Alias', 'Both', 'Last', 'Duplicate']
    assert snapshot.semantic.children is children
    both = queries.type_at(snapshot, uri, *_where(document, 'Both:'))
    assert both is not None
    parents = queries.supertypes(snapshot, both)
    assert [item.name for item in parents] == ['B', 'A']
    assert [item.selection.line for item in parents] == [_where(document, f'{name}: object')[0] for name in ('B', 'A')]
    alias = queries.type_at(snapshot, uri, *_where(document, 'Alias: A'))
    assert alias is not None
    assert [item.name for item in queries.subtypes(snapshot, alias)] == ['ViaAlias']


def test_multiple_parent_wrappers_navigate_qualified_references_to_the_library_declarations(memory_workspace):
    library = '#%RAML 1.0 Library\ntypes:\n  A: object\n  B: object\n'
    document = '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  Both: {type: [lib.B, lib.A]}\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document, 'lib.raml': library})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert snapshot.error is None
    both = queries.type_at(snapshot, uri, *_where(document, 'Both:'))
    assert both is not None
    parents = queries.supertypes(snapshot, both)
    assert [(item.name, item.uri) for item in parents] == [('B', f'{folder}/lib.raml'), ('A', f'{folder}/lib.raml')]
    assert [item.name for item in queries.subtypes(snapshot, parents[0])] == ['Both']


def test_root_edits_replace_outline_and_hierarchy_caches_but_held_snapshots_keep_their_answers(memory_workspace):
    document = '#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n  B: string\n  Child: A\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    old = workspace.snapshot(uri)
    old_outline = outline.document_symbols(old, uri)
    item = queries.type_at(old, uri, *_where(document, 'Child:'))
    assert item is not None
    assert [parent.name for parent in queries.supertypes(old, item)] == ['A']
    workspace.change(uri, document.replace('Child: A', 'Child: B'), 2)
    new = workspace.snapshot(uri)
    assert outline.document_symbols(new, uri) is not old_outline
    assert [parent.name for parent in queries.supertypes(new, item)] == ['B']
    assert [parent.name for parent in queries.supertypes(old, item)] == ['A']
    assert outline.document_symbols(old, uri) is old_outline
    assert new.semantic is not old.semantic


def test_shared_dependency_edits_invalidate_separate_root_outline_contexts(memory_workspace):
    files = {
        'a.raml': '#%RAML 1.0\ntitle: A\nuses:\n  lib: lib.raml\n',
        'b.raml': '#%RAML 1.0\ntitle: B\nuses:\n  lib: lib.raml\n',
        'lib.raml': LIBRARY,
    }
    workspace, folder = _buffered(memory_workspace, files)
    library = f'{folder}/lib.raml'
    a, b = (workspace.snapshot(f'{folder}/{name}.raml') for name in ('a', 'b'))
    outlines = [outline.document_symbols(snapshot, library) for snapshot in (a, b)]
    assert outlines[0]
    assert outlines[0] is not outlines[1]
    assert a.semantic is not b.semantic
    workspace.change(library, LIBRARY.replace('name: string', 'age: integer'), 2)
    for name, previous in zip(('a', 'b'), outlines, strict=True):
        snapshot = workspace.snapshot(f'{folder}/{name}.raml')
        current = outline.document_symbols(snapshot, library)
        assert current is not previous
        assert current[0].children[0].children[0].name == 'age'


@pytest.mark.parametrize('document', ['#%RAML 1.0\ntitle: T\n', '#%RAML 1.0\ntitle: [\n'])
def test_empty_outline_results_are_cached_even_on_unavailable_models(memory_workspace, document):
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    uri = f'{folder}/missing.raml'
    first = outline.document_symbols(snapshot, uri)
    assert first == []
    assert outline.document_symbols(snapshot, uri) is first
