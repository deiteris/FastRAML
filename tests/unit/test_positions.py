"""Source positions. See docs/11-diagnostics.md § 3."""

from __future__ import annotations

import pytest

from fastraml.positions import UNKNOWN, Position


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
