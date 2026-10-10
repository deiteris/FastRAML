"""One source input serves all consumers without retaining its workspace."""

import gc
from weakref import ref

import pytest

from fastraml.config import FastRamlConfig, ParserConfig
from fastraml.service import queries, source
from fastraml.service.workspace import BOM
from tests.unit.test_service_workspace import API, _workspace

DOCUMENT = API + 'types:\n  Item:\n    type: string\n    minLength: 2\n'


def _compositions(monkeypatch):
    calls = []
    original = source.compose

    def compose(text, **kwargs):
        calls.append((text, kwargs['max_depth']))
        return original(text, **kwargs)

    monkeypatch.setattr(source, 'compose', compose)
    return calls


@pytest.mark.parametrize('source_first', [False, True])
def test_hover_and_structure_share_one_tree_in_either_request_order(tmp_path, monkeypatch, source_first):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    workspace.open(uri, BOM + DOCUMENT, 1)
    calls = _compositions(monkeypatch)
    first = workspace.source(uri) if source_first else None
    snapshot = workspace.snapshot(uri)
    assert snapshot.error is None
    assert queries.hover(snapshot, uri, 6, 5) is not None
    root = workspace.source(uri)
    assert root is not None
    assert snapshot.hover._node(uri) is root
    assert not source_first or first is root
    assert len(calls) == 1
    assert calls[0][0] == DOCUMENT, 'single-BOM normalization agrees with the parser input'


def test_failed_composition_is_cached_until_the_input_changes(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    workspace.open(uri, API + 'broken: [\n', 1)
    calls = _compositions(monkeypatch)
    assert workspace.source(uri) is None
    assert workspace.source(uri) is None
    assert len(calls) == 1
    workspace.change(uri, DOCUMENT, 2)
    assert workspace.source(uri) is not None
    assert len(calls) == 2


def test_closed_file_equal_text_reuses_the_tree_even_after_another_disk_decode(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    calls = _compositions(monkeypatch)
    first = workspace.source(f'{folder}/api.raml')
    assert workspace.source(f'{folder}/api.raml') is first
    assert len(calls) == 1


def test_source_composition_obeys_depth_and_does_not_reuse_a_different_limit(tmp_path, monkeypatch):
    config = FastRamlConfig(parser=ParserConfig(max_depth=1))
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT}, config=config)
    calls = _compositions(monkeypatch)
    assert workspace.source(f'{folder}/api.raml') is None
    assert workspace.source(f'{folder}/api.raml') is None
    workspace.config = FastRamlConfig(parser=ParserConfig(max_depth=32))
    assert workspace.source(f'{folder}/api.raml') is not None
    assert [depth for _, depth in calls] == [1, 32]


def test_source_cache_key_includes_the_yaml_backend(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    calls = _compositions(monkeypatch)
    first = workspace.source(f'{folder}/api.raml')
    current = source.backend_name()
    monkeypatch.setattr(source, 'backend_name', lambda: 'python' if current == 'libyaml' else 'libyaml')
    assert workspace.source(f'{folder}/api.raml') is not first
    assert len(calls) == 2


def test_old_snapshot_reads_do_not_evict_the_current_text_tree(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    workspace.open(uri, DOCUMENT, 1)
    old = workspace.snapshot(uri)
    workspace.change(uri, DOCUMENT.replace('minLength: 2', 'minLength: 3'), 2)
    current = workspace.source(uri)
    calls = _compositions(monkeypatch)
    assert old.hover._node(uri) is not current
    assert workspace.source(uri) is current
    assert len(calls) == 1, 'only the stale snapshot composes its old input'


def test_an_old_snapshot_cache_miss_cannot_publish_into_a_new_input_generation(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    workspace.open(uri, DOCUMENT, 1)
    old = workspace.snapshot(uri)
    workspace.change(uri, DOCUMENT + '# intermediate\n', 2)
    workspace.change(uri, DOCUMENT, 3)
    calls = _compositions(monkeypatch)
    stale = old.hover._node(uri)
    assert workspace.source(uri) is not stale
    assert len(calls) == 2, 'the old miss is private even when current text returns to the same value'


def test_unchanged_dependency_structure_survives_a_root_snapshot_rebuild(tmp_path, monkeypatch):
    library = '#%RAML 1.0 Library\ntypes:\n  Item: string\n'
    api = API + 'uses:\n  lib: lib.raml\n'
    workspace, folder = _workspace(tmp_path, {'api.raml': api, 'lib.raml': library})
    uri = f'{folder}/lib.raml'
    workspace.open(uri, library, 1)
    calls = _compositions(monkeypatch)
    first = workspace.source(uri)
    assert workspace.snapshot(f'{folder}/api.raml').hover._node(uri) is first
    workspace.change(f'{folder}/api.raml', api + '# edit\n', 2)
    assert workspace.snapshot(f'{folder}/api.raml').hover._node(uri) is first
    assert len(calls) == 1


def test_an_unrelated_change_does_not_prevent_a_current_snapshot_publishing_source(tmp_path, monkeypatch):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT, 'other.raml': API})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    workspace.change(f'{folder}/other.raml', API + '# changed\n', 1)
    calls = _compositions(monkeypatch)
    first = snapshot.hover._node(uri)
    assert workspace.source(uri) is first
    assert len(calls) == 1


@pytest.mark.parametrize('source_first', [False, True])
def test_full_source_retention_offers_its_original_tree_on_demand(tmp_path, monkeypatch, source_first):
    config = FastRamlConfig(lint={'rules': [{'id': 'prefer-array-expression'}]})
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT}, config=config)
    uri = f'{folder}/api.raml'
    calls = _compositions(monkeypatch)
    standalone = workspace.source(uri) if source_first else None
    snapshot = workspace.snapshot(uri)
    original = snapshot.raml.source_nodes[uri]
    assert workspace.source(uri) is original
    assert snapshot.hover._node(uri) is original
    assert len(calls) == int(source_first)
    if source_first:
        assert standalone is not original
        assert queries.folding_ranges_of(standalone) == queries.folding_ranges_of(original)


def test_holding_a_snapshot_does_not_retain_the_workspace_source_cache(tmp_path):
    workspace, folder = _workspace(tmp_path, {'api.raml': DOCUMENT, 'other.yaml': 'a:\n  b: c\n'})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    workspace.source(f'{folder}/other.yaml')
    cache = ref(workspace._sources)
    del workspace
    gc.collect()
    assert cache() is None
    assert queries.hover(snapshot, f'{folder}/api.raml', 6, 5) is not None


def test_normalized_json_include_is_not_substituted_for_generic_current_source(tmp_path):
    document = API + 'types:\n  Item:\n    properties: {a: integer}\n    example: !include example.json\n'
    config = FastRamlConfig(lint={'rules': [{'id': 'prefer-array-expression'}]})
    workspace, folder = _workspace(
        tmp_path, {'api.raml': document, 'example.json': '\t{\n\t"a": 1\n}\n'}, config=config
    )
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    assert snapshot.error is None
    uri = f'{folder}/example.json'
    original = snapshot.raml.include_nodes[uri]
    assert workspace.source(uri) is None, 'generic YAML composition does not perform JSON whitespace normalization'
    assert snapshot.hover._node(uri) is original
    assert workspace.source(uri) is None
