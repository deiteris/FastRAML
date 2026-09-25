"""Values taken off the model, as a reader writes them."""

from __future__ import annotations

from fractions import Fraction

import pytest

from sphinxcontrib.fastraml.model import exact


@pytest.mark.parametrize(
    ('bound', 'written'),
    [
        # Numbers never pass through `float` (AGENTS.md): each of these would
        # come back changed from one.
        (Fraction(9223372036854775807), '9223372036854775807'),
        (Fraction('12345678901234567890.125'), '12345678901234567890.125'),
        (Fraction('1.1'), '1.1'),
        (Fraction('-0.000001'), '-0.000001'),
        (Fraction('1E+30'), '1000000000000000000000000000000'),
    ],
)
def test_a_bound_is_the_decimal_the_author_wrote(bound, written):
    assert exact(bound) == written
