"""Source positions. See docs/11-diagnostics.md section 3."""

from __future__ import annotations

from pyraml.positions import UNKNOWN, Position


def test_positions_render_as_line_column():
    assert str(Position(17, 10, 17, 14)) == '17:10'


def test_shifted_marks_a_single_character():
    # Sub-token positioning: an error inside a URI template or a type expression
    # points at one offending byte within the enclosing scalar.
    shifted = Position(4, 8, 4, 30).shifted(6)
    assert (shifted.line, shifted.column) == (4, 14)
    assert (shifted.end_line, shifted.end_column) == (4, 15)


def test_with_end_replaces_only_the_end():
    span = Position(3, 1).with_end(9, 4)
    assert (span.line, span.column, span.end_line, span.end_column) == (3, 1, 9, 4)


def test_positions_are_frozen_and_hashable():
    assert Position(1, 2) == Position(1, 2)
    assert len({Position(1, 2), Position(1, 2)}) == 1


def test_unknown_is_not_reported_as_a_real_position():
    assert not UNKNOWN.is_known
    assert Position(4, 1).is_known
