"""The sparse navigation scenario reaches queries in both measurement passes."""

import pytest

from bench import corpus
from bench.__main__ import run_one
from fastraml.service import index as index_module
from fastraml.service import outline, queries
from fastraml.service.datahover import DataHover
from fastraml.service.hover import Hover
from fastraml.service.workspace import Workspace


@pytest.mark.parametrize('count', [1, 2, 4])
def test_navigation_reaches_fixed_requests_and_edit_boundaries(tmp_path, monkeypatch, count):
    entry = corpus.write_hover(tmp_path, family_count=count)
    versions = []
    requests = dict.fromkeys(('type_at', 'supertypes', 'subtypes', 'definition', 'workspace_symbols', 'outlines'), 0)
    populations = dict.fromkeys(('declarations', 'data', 'outline'), 0)
    original_parse = Workspace._parse

    def parse(workspace, uri):
        versions.append(workspace.buffers[uri].version)
        return original_parse(workspace, uri)

    def counted(tally, name, operation):
        def request(*args, **kwargs):
            tally[name] += 1
            return operation(*args, **kwargs)

        return request

    def declarations(raml):
        populations['declarations'] += 1
        yield from original_declarations(raml)

    def presentation(*args, **kwargs):
        pytest.fail('navigation and cached outlines must not initialize hover presentation or compose source')

    original_declarations = index_module.every_declaration
    monkeypatch.setattr(index_module, 'every_declaration', declarations)
    monkeypatch.setattr(Hover, '_index', presentation)
    monkeypatch.setattr(Hover, '_node', presentation)
    for owner, name, counter in ((DataHover, '_index', 'data'), (outline, '_document_symbols', 'outline')):
        monkeypatch.setattr(owner, name, counted(populations, counter, getattr(owner, name)))
    monkeypatch.setattr(Workspace, '_parse', parse)
    for name in requests:
        owner, attribute = (outline, 'document_symbols') if name == 'outlines' else (queries, name)
        monkeypatch.setattr(owner, attribute, counted(requests, name, getattr(owner, attribute)))
    run_one('service-navigation', 'unwrap', entry, repeat=1)
    assert versions == [1, 2, 3, 1, 2, 3]
    assert requests == {
        'type_at': 36,
        'supertypes': 36,
        'subtypes': 36,
        'definition': 18,
        'workspace_symbols': 12,
        'outlines': 12,
    }
    assert populations == {'declarations': 6, 'data': 6, 'outline': 6}
