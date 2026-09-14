"""The bundle is the whole deliverable, so the tests are about it being there."""

from __future__ import annotations

import json

import pytest

from fastraml_viewer import index_html, static_dir


def test_the_bundle_is_present():
    assert index_html().is_file()


def test_assets_are_relative_so_any_mount_path_works():
    """vite builds with `base: './'`. Absolute `/assets/...` would 404 for every
    consumer that mounts this anywhere but the server root, which is all of them.
    """
    html = index_html().read_text(encoding='utf-8')
    assert './assets/' in html
    assert 'src="/assets/' not in html
    assert 'href="/assets/' not in html


def test_the_assets_it_references_exist():
    import re

    html = index_html().read_text(encoding='utf-8')
    referenced = re.findall(r'(?:src|href)="\./([^"]+)"', html)
    assert referenced, 'index.html references no assets at all'
    for name in referenced:
        assert (static_dir() / name).is_file(), name


def test_a_missing_bundle_is_a_clear_error(monkeypatch, tmp_path):
    """Rather than an empty page or a confusing 404 at request time."""
    import fastraml_viewer

    monkeypatch.setattr(fastraml_viewer, '_STATIC', tmp_path)
    with pytest.raises(RuntimeError, match='viewer bundle is missing'):
        static_dir()


def test_it_ships_the_sample_document():
    """`api.json` is the fallback the viewer loads when given no `?src=`.

    Shipped on purpose, at 120 KiB of a 517 KiB bundle: it is this project's own
    `fixtures/sample` worked example, so opening the page bare demonstrates the
    viewer instead of failing to load. A consumer that wants its own document
    passes `?src=` and never reads this.
    """
    sample = static_dir() / 'api.json'
    assert sample.is_file()
    assert json.loads(sample.read_text(encoding='utf-8'))['base'].startswith('fastraml://')
