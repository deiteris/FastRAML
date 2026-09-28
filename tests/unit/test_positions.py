"""Source positions. See docs/11-diagnostics.md § 3."""

from __future__ import annotations

import pytest

from fastraml.positions import UNKNOWN, Position
from fastraml.uris import path_to_file_uri


def test_positions_render_as_line_column():
    assert str(Position(17, 10, 17, 14)) == '17:10'


def test_shifted_marks_a_single_character_by_default():
    # Sub-token positioning: an error inside a URI template or a type expression
    # points at one offending character within the enclosing scalar.
    shifted = Position(4, 8, 4, 30).shifted(6)
    assert (shifted.line, shifted.column) == (4, 14)
    assert (shifted.end_line, shifted.end_column) == (4, 15)


def test_shifted_spans_a_name():
    shifted = Position(4, 8, 4, 30).shifted(6, 4)
    assert (shifted.line, shifted.column, shifted.end_line, shifted.end_column) == (4, 14, 4, 18)


class TestWithin:
    """docs/11 § 3: where a scalar's text starts inside the node's span."""

    def test_a_plain_scalar_starts_where_its_node_does(self):
        span = Position(2, 5, 2, 9)
        assert span.within('User') is span

    def test_a_quoted_scalar_starts_past_its_quote(self):
        inner = Position(2, 5, 2, 11).within('User')
        assert (inner.line, inner.column, inner.end_column) == (2, 6, 10)

    @pytest.mark.parametrize(
        'span',
        [
            pytest.param(Position(2, 5, 3, 3), id='over two lines'),
            pytest.param(Position(2, 5, 2, 13), id='a tag or an escape'),
        ],
    )
    def test_a_span_that_fits_neither_is_kept(self, span):
        assert span.within('User') is span


def test_with_end_replaces_only_the_end():
    span = Position(3, 1).with_end(9, 4)
    assert (span.line, span.column, span.end_line, span.end_column) == (3, 1, 9, 4)


def test_positions_are_frozen_and_hashable():
    assert Position(1, 2) == Position(1, 2)
    assert len({Position(1, 2), Position(1, 2)}) == 1


def test_unknown_is_not_reported_as_a_real_position():
    assert not UNKNOWN.is_known
    assert Position(4, 1).is_known
    assert Position(1, 1, 1, 5).is_known


def test_an_unknown_span_is_unknown_by_value(memory_workspace):
    # A node built with no source spans `1:1` too: the synthetic key naming
    # a DataType fragment's shape made its key read as written on the header.
    assert not Position(1, 1, 1, 1).is_known
    root = memory_workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  A: !include a.raml\n',
            'a.raml': '#%RAML 1.0 DataType\ntype: string\n',
        }
    )
    included = memory_workspace.parse(root / 'api.raml').fragments[path_to_file_uri(root / 'a.raml')]
    assert not included.shape.key_pos.is_known


class TestSpans:
    """docs/11 § 1: span arithmetic, on `Position` only."""

    SPAN = Position(2, 3, 4, 5)

    @pytest.mark.parametrize(
        ('inner', 'contained'),
        [
            pytest.param(Position(2, 3, 4, 5), True, id='itself'),
            pytest.param(Position(3, 1, 3, 9), True, id='a middle line'),
            pytest.param(Position(2, 2, 2, 4), False, id='starts before'),
            pytest.param(Position(4, 1, 4, 6), False, id='ends after'),
        ],
    )
    def test_contains_holds_its_ends(self, inner, contained):
        assert self.SPAN.contains(inner) is contained

    @pytest.mark.parametrize(
        ('line', 'column', 'held'),
        [
            pytest.param(2, 3, True, id='the start'),
            pytest.param(4, 5, True, id='just after the end'),
            pytest.param(2, 2, False, id='before'),
            pytest.param(4, 6, False, id='after'),
        ],
    )
    def test_holds_a_cursor_just_after_its_end(self, line, column, held):
        assert self.SPAN.holds(line, column) is held

    def test_covering_spans_from_the_earliest_start_to_the_latest_end(self):
        # The first to start is not the last to end.
        spans = [Position(3, 1, 9, 1), Position(2, 5, 2, 8), Position(5, 1, 5, 2)]
        assert Position.covering(spans) == Position(2, 5, 9, 1)

    def test_covering_nothing_is_an_error(self):
        with pytest.raises(ValueError, match='no span'):
            Position.covering([])
