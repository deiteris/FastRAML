"""Deferring full collections during an operation (docs/12 § 6).

The collector's thresholds belong to the whole process, so each rule here
protects the host: only raise, restore once, never overwrite a host change,
and stay out of the way when told to.
"""

from __future__ import annotations

import gc

import pytest

from fastraml import ParseOptions, build_graph, gctuning, parse_from_string, set_gc_tuning, to_openapi
from fastraml.gctuning import FULL_COLLECTION_THRESHOLD, tuned_gc

HOST = (700, 10, 10)
TUNED = (700, 10, FULL_COLLECTION_THRESHOLD)
API = '#%RAML 1.0\ntitle: T\n/a:\n  get:\n'
SOURCE = ParseOptions(unwrap=True, retain_source=True)


@pytest.fixture(autouse=True)
def host_collector():
    """Every test starts from the default thresholds and leaves them there."""
    saved, enabled = gc.get_threshold(), gc.isenabled()
    gc.set_threshold(*HOST)
    gc.enable()
    yield
    set_gc_tuning(True)
    gc.set_threshold(*saved)
    if not enabled:
        gc.disable()
    assert gctuning._depth == 0


def test_an_operation_raises_only_the_full_collection_threshold():
    with tuned_gc():
        assert gc.get_threshold() == TUNED
    assert gc.get_threshold() == HOST


def test_a_failing_operation_restores_the_thresholds():
    def failing() -> None:
        with tuned_gc():
            raise ValueError('decode failed')

    with pytest.raises(ValueError, match='decode failed'):
        failing()
    assert gc.get_threshold() == HOST


def test_a_nested_operation_leaves_the_restore_to_the_outer_one():
    # A lint run parses inside its own tuned span.
    with tuned_gc():
        with tuned_gc():
            pass
        assert gc.get_threshold() == TUNED
    assert gc.get_threshold() == HOST


def test_overlapping_operations_restore_when_the_last_one_leaves():
    # Two threads: the first to finish must not restore under the other.
    first, second = tuned_gc(), tuned_gc()
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    assert gc.get_threshold() == TUNED
    second.__exit__(None, None, None)
    assert gc.get_threshold() == HOST


def test_a_higher_host_threshold_is_never_lowered():
    gc.set_threshold(700, 10, 5000)
    with tuned_gc():
        assert gc.get_threshold() == (700, 10, 5000)
    assert gc.get_threshold() == (700, 10, 5000)


def test_a_disabled_collector_is_left_alone():
    gc.disable()
    with tuned_gc():
        assert gc.get_threshold() == HOST
    assert not gc.isenabled()


def test_a_threshold_the_host_changed_meanwhile_is_kept():
    with tuned_gc():
        gc.set_threshold(2000, 20, 20)
    assert gc.get_threshold() == (2000, 20, 20)


def test_opting_out_leaves_the_collector_alone():
    set_gc_tuning(False)
    with tuned_gc():
        assert gc.get_threshold() == HOST


def test_opting_out_midway_still_restores_what_was_changed():
    with tuned_gc():
        set_gc_tuning(False)
        with tuned_gc():
            assert gc.get_threshold() == TUNED
    assert gc.get_threshold() == HOST


class TestTunedOperations:
    """The operations that build large object graphs run tuned (docs/12 § 6)."""

    def test_parse(self, monkeypatch, tmp_path):
        from fastraml.parser import entry

        seen = []
        original = entry.build_endpoints

        def spy(raml):
            seen.append(gc.get_threshold())
            return original(raml)

        monkeypatch.setattr(entry, 'build_endpoints', spy)
        parse_from_string(API, file_name='api.raml', base_dir=tmp_path)
        assert seen == [TUNED]
        assert gc.get_threshold() == HOST

    def test_graph_lint_and_openapi(self, monkeypatch, tmp_path):
        from fastraml.views import walk
        from fastraml.views.lint import Config, Linter, builtin_registry
        from fastraml.views.openapi import OpenAPIConversion

        raml = parse_from_string(API, file_name='api.raml', base_dir=tmp_path, options=SOURCE)
        seen: list[tuple[str, tuple[int, int, int]]] = []

        def spy(name, method):
            def recording(self, *args, **kwargs):
                seen.append((name, gc.get_threshold()))
                return method(self, *args, **kwargs)

            return recording

        monkeypatch.setattr(walk.Walk, 'run', spy('graph', walk.Walk.run))
        monkeypatch.setattr(Linter, '_finish', spy('lint', Linter._finish))
        monkeypatch.setattr(OpenAPIConversion, 'convert', spy('openapi', OpenAPIConversion.convert))
        graph = build_graph(raml)
        # The graph is supplied, so lint's own span is what is observed.
        Linter(builtin_registry(), Config(extends=('all',))).report(raml, graph=graph)
        to_openapi(raml)
        assert seen == [('graph', TUNED), ('lint', TUNED), ('openapi', TUNED)]
        assert gc.get_threshold() == HOST
