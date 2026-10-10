"""Reach and ownership boundaries of the representative mixed editor workload."""

import pytest

from bench import corpus
from bench.service_session import exercise, prepare
from fastraml.service.workspace import Workspace


@pytest.mark.parametrize('count', [2, 4])
@pytest.mark.parametrize('source_first', [False, True])
def test_session_rebuilds_three_versions_and_checks_available_source_reuse(tmp_path, monkeypatch, count, source_first):
    from fastraml import yamlnode
    from fastraml.service import inlays, outline

    entry = corpus.write_hover(tmp_path, family_count=count)
    prepared = prepare(entry)
    parsed, compositions, outlines, hints, sources = [], [], [], [], []
    original_parse = Workspace._parse
    original_compose = yamlnode.yaml.compose
    original_outline = outline.document_symbols
    original_hints = inlays.inlay_hints
    original_source = getattr(Workspace, 'source', None)

    def parse(workspace, root):
        parsed.append(workspace.buffers[root].version)
        return original_parse(workspace, root)

    def compose(text, **kwargs):
        if text in prepared.versions:
            compositions.append(text)
        return original_compose(text, **kwargs)

    def symbols(snapshot, uri):
        result = original_outline(snapshot, uri)
        outlines.append(result)
        return result

    def inlay(snapshot, uri, span):
        result = original_hints(snapshot, uri, span)
        hints.append([(hint.position, hint.label) for hint in result])
        return result

    def source(workspace, uri):
        assert original_source is not None
        result = original_source(workspace, uri)
        sources.append((workspace.buffers[uri].version, result))
        return result

    monkeypatch.setattr(Workspace, '_parse', parse)
    monkeypatch.setattr(yamlnode.yaml, 'compose', compose)
    monkeypatch.setattr(outline, 'document_symbols', symbols)
    monkeypatch.setattr(inlays, 'inlay_hints', inlay)
    if original_source is not None:
        monkeypatch.setattr(Workspace, 'source', source)
    workspace, counts = exercise(prepared, source_first=source_first)
    assert parsed == [1, 2, 3]
    snapshot = workspace.snapshot(prepared.root)
    if getattr(snapshot.raml, 'projection', None) is not None:
        assert len(compositions) == (6 if source_first else 3)
    else:
        assert len(compositions) >= 3, 'historical source readers may compose for each request'
    if hasattr(snapshot, 'outlines'):
        assert all(outlines[index] is outlines[index + 1] for index in (0, 2, 4))
    selections = [[(symbol.name, symbol.span, symbol.selection) for symbol in symbols] for symbols in outlines]
    assert all(selections[index] == selections[index + 1] for index in (0, 2, 4))
    for version in (1, 2, 3):
        current = [root for at, root in sources if at == version]
        warm = current[1:] if source_first else current
        assert all(root is warm[0] for root in warm), 'available source caches share the warm requests'
    assert all(hints[index] == hints[index + 1] for index in (0, 2, 4))
    assert counts['hovers'] == 9
    assert counts['selections'] == 24
    assert len(workspace._snapshots) == 1


def test_session_can_query_an_unchanged_library_after_root_edits(tmp_path):
    entry = corpus.write_large(tmp_path, type_count=24, library_count=4)
    workspace, counts = exercise(prepare(entry, focus=tmp_path / 'lib/g0/l0.raml'))
    assert counts['snapshots'] == 3
    assert len(workspace._snapshots) == 1


@pytest.mark.parametrize('name', ['service-session', 'service-source-first'])
def test_benchmark_and_linearity_drive_the_session_in_both_measurement_runs(tmp_path, monkeypatch, name):
    from bench.__main__ import LINEARITY_CONFIGS, run_one

    entry = corpus.write_hover(tmp_path, family_count=2)
    versions = []
    original = Workspace._parse

    def parse(workspace, uri):
        versions.append(workspace.buffers[uri].version)
        return original(workspace, uri)

    monkeypatch.setattr(Workspace, '_parse', parse)
    assert LINEARITY_CONFIGS[name] == 'unwrap'
    run_one(name, 'unwrap', entry, repeat=1)
    assert versions == [1, 2, 3, 1, 2, 3]
