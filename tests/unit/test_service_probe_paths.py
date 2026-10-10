"""Published service measurements keep import and checkout paths relative."""

import json
import runpy
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

import fastraml
from bench import corpus

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'docs/reports/2026-10-10'


def test_recorded_measurements_use_relative_checkout_and_import_paths():
    for name in ('service-model-inlays-phase-metrics.json', 'service-shared-indices-phase-metrics.json'):
        document = json.loads((REPORTS / name).read_text(encoding='utf-8'))
        assert document['trees']
        for _, tree in document['trees']:
            assert not PurePosixPath(tree).is_absolute()
            assert not PureWindowsPath(tree).is_absolute()
        for rounds in document['results'].values():
            assert rounds
            for measurement in rounds:
                assert measurement['import'] == 'fastraml/__init__.py'


def test_phase_worker_reports_the_import_relative_to_its_verified_checkout(tmp_path, monkeypatch, capsys):
    entry = corpus.write_hover(tmp_path, family_count=1)
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    probe = runpy.run_path(str(REPORTS / 'service_cost_probe.py'))
    probe['worker'](
        SimpleNamespace(
            entry=str(entry),
            focus=str(entry),
            repeat=1,
            no_capture=False,
            detail=False,
            mixed=False,
            ownership=False,
            rss_edits=0,
            source_first=False,
        )
    )
    result = json.loads(capsys.readouterr().out)
    assert result['import'] == 'fastraml/__init__.py'
    assert str(ROOT) not in json.dumps(result)


def test_wrong_checkout_import_is_rejected_without_reporting_its_absolute_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    private = tmp_path / 'private-checkout/fastraml/__init__.py'
    monkeypatch.setattr(fastraml, '__file__', str(private))
    probe = runpy.run_path(str(REPORTS / 'service_cost_probe.py'))
    with pytest.raises(RuntimeError, match='outside the requested tree') as caught:
        probe['worker'](SimpleNamespace())
    assert str(private) not in str(caught.value)
