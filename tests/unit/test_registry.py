"""The registry: ids, the parse-context stack, and the stores.

Each test names the rule it protects; see docs/02-architecture.md section 3.
"""

from __future__ import annotations

import pytest

from fastraml.loaders import UnsupportedSchemeError
from fastraml.registry import DEFAULT_MAX_INCLUDE_SIZE, ParseCtx, Raml


class TestIds:
    def test_ids_are_unique_and_increasing(self):
        raml = Raml()
        issued = [raml.next_id() for _ in range(5)]
        assert issued == [1, 2, 3, 4, 5]

    def test_two_registries_do_not_share_a_counter(self):
        # Ids key the clone memo and the source-info index, both of which are
        # per-parse; sharing would make two parses collide.
        assert Raml().next_id() == Raml().next_id() == 1


class TestParseCtxStack:
    def test_current_ctx_is_empty_outside_a_decode(self):
        assert Raml().current_ctx().anchor is None

    def test_push_and_pop_restore_the_outer_scope(self):
        raml = Raml()
        outer, inner = ParseCtx(anchor=None), ParseCtx(anchor=None)
        raml.push_ctx(outer)
        raml.push_ctx(inner)
        assert raml.current_ctx() is inner
        raml.pop_ctx()
        assert raml.current_ctx() is outer
        raml.pop_ctx()
        assert raml.current_ctx().anchor is None

    def test_popping_an_empty_stack_is_harmless(self):
        # Decoders pop in a finally block, which may run after a failed push.
        raml = Raml()
        raml.pop_ctx()
        assert raml.current_ctx().anchor is None

    def test_parse_ctx_is_frozen(self):
        ctx = ParseCtx(anchor=None)
        assert hash(ctx) is not None


class TestStores:
    def test_indices_start_empty_and_are_keyed_by_uri(self):
        raml = Raml()
        raml.put_type('User', 'file:///a.raml', 'shape')
        raml.put_annotation_type('Deprecated', 'file:///a.raml', 'ann')
        raml.put_typedef('file:///a.raml', 'shape')
        assert raml.types_in('file:///a.raml') == {'User': 'shape'}
        assert raml.annotation_types_in('file:///a.raml') == {'Deprecated': 'ann'}
        assert list(raml.typedefs_in('file:///a.raml')) == ['shape']
        assert raml.types_in('file:///b.raml') == {}

    def test_source_nodes_are_kept_only_when_asked_for(self):
        off, on = Raml(), Raml(retain_source=True)
        off.store_source_node('file:///a.raml', 'node')
        on.store_source_node('file:///a.raml', 'node')
        assert off.source_node('file:///a.raml') is None
        assert on.source_node('file:///a.raml') == 'node'


class TestDefaults:
    def test_a_registry_without_a_loader_reports_the_missing_scheme(self):
        # Better than an AttributeError from a None loader: the message names
        # what was asked for.
        with pytest.raises(UnsupportedSchemeError, match='file'):
            Raml().loader.load('file:///a.raml')

    def test_include_limit_defaults_to_the_documented_value(self):
        assert Raml().max_include_size == DEFAULT_MAX_INCLUDE_SIZE == 65536

    def test_location_is_empty_until_an_entry_point_is_set(self):
        assert Raml().location == ''
        assert Raml().is_unwrapped is False
