"""Converting a column between fastRAML and a protocol (docs/21 § 3)."""

from __future__ import annotations

import pytest

from fastraml.service.text import Encoding, Lines

#: `𝄞` is outside the Basic Multilingual Plane: one code point, two UTF-16
#: units, four UTF-8 bytes. `é` is two UTF-8 bytes and one UTF-16 unit.
TEXT = 'title: T\nname: 𝄞é: x\r\nlast'


class TestToProtocol:
    @pytest.mark.parametrize(
        ('encoding', 'expected'),
        [(Encoding.UTF32, 8), (Encoding.UTF16, 9), (Encoding.UTF8, 12)],
        ids=['utf-32', 'utf-16', 'utf-8'],
    )
    def test_a_column_past_wide_characters_counts_the_encodings_units(self, encoding, expected):
        # Column 9 is the `:` after `é`: eight code points precede it.
        assert Lines(TEXT).to_protocol(2, 9, encoding) == (1, expected)

    def test_lines_and_columns_count_from_zero(self):
        assert Lines(TEXT).to_protocol(1, 1, Encoding.UTF16) == (0, 0)

    def test_crlf_ends_a_line(self):
        assert Lines(TEXT).to_protocol(3, 2, Encoding.UTF16) == (2, 1)


class TestFromProtocol:
    @pytest.mark.parametrize(
        ('encoding', 'character'),
        [(Encoding.UTF32, 8), (Encoding.UTF16, 9), (Encoding.UTF8, 12)],
        ids=['utf-32', 'utf-16', 'utf-8'],
    )
    def test_it_inverts_to_protocol(self, encoding, character):
        assert Lines(TEXT).from_protocol(1, character, encoding) == (2, 9)

    def test_a_unit_inside_a_code_point_lands_on_it(self):
        # UTF-16 unit 7 is the second half of `𝄞`, which starts at column 7.
        assert Lines(TEXT).from_protocol(1, 7, Encoding.UTF16) == (2, 7)

    def test_a_character_past_the_end_lands_on_the_end(self):
        assert Lines(TEXT).from_protocol(0, 99, Encoding.UTF16) == (1, len('title: T') + 1)
