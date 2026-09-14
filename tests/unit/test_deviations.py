"""The deliberate deviations — docs/01-scope-and-coverage.md section 4.

Each is a decision, not an accident, and each is supposed to be visible to a
user. That second half is the part that rots: a deviation can stay true in
behaviour while the message that was meant to explain it drifts or never
existed. D1 spent nine phases documented and unimplemented, rejecting `.xsd`
with `unknown fragment kind` — correct, and pointing the author at the wrong
thing to fix.

Deviations with a natural home elsewhere are tested there and named here so the
list can be read as a whole: D3 in `test_regex_engine.py`, D4 in
`test_references.py`, D5 in `test_loaders.py`, D9 and D10 in `test_yamlnode.py`.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path
from fastraml.registry import DEFAULT_MAX_INCLUDE_SIZE
from fastraml.types.scalars import INTEGER_FORMATS, NUMBER_FORMATS

LIB = '#%RAML 1.0 Library\n'
XSD = '<?xml version="1.0"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>\n'


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


class TestD1NoXsd:
    def test_an_xsd_type_says_why(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.xsd\n', 's.xsd': XSD})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml')
        assert 'xml schema external types are not supported' in messages(caught.value)

    def test_it_is_not_the_generic_header_diagnostic(self, workspace):
        """What the behaviour was before Phase 9, and why it was not enough."""
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.xsd\n', 's.xsd': XSD})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml')
        assert 'unknown fragment kind' not in messages(caught.value)

    def test_an_xml_scalar_include_is_untouched(self, workspace):
        """Only `.xsd`. An `!include` of `.xml` at a value position is data."""
        root = workspace(
            {
                'lib.raml': LIB + 'types:\n  T:\n    type: string\n    example: !include e.xml\n',
                'e.xml': '<a>hi</a>\n',
            }
        )
        raml = parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
        assert raml.entry_point.types['T'] is not None


class TestD2NumericFormatsDoNotCross:
    """The spec implies `format: float` is legal on an `integer`; it is refused."""

    def test_the_two_tables_are_disjoint(self):
        assert set(INTEGER_FORMATS) & NUMBER_FORMATS == set()

    def test_int_and_long_are_aliases_of_the_sized_forms(self):
        assert INTEGER_FORMATS['int'] == INTEGER_FORMATS['int32']
        assert INTEGER_FORMATS['long'] == INTEGER_FORMATS['int64']

    @pytest.mark.parametrize(('declared', 'fmt'), [('integer', 'float'), ('integer', 'double'), ('number', 'int32')])
    def test_a_format_from_the_other_table_is_refused(self, workspace, declared, fmt):
        root = workspace({'lib.raml': LIB + f'types:\n  T:\n    type: {declared}\n    format: {fmt}\n'})
        with pytest.raises(RamlError):
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))


class TestD6IncludeSizeLimit:
    def test_the_documented_default_is_the_real_one(self):
        """docs/01 says 64 KiB and means it, unlike go-raml's README."""
        assert DEFAULT_MAX_INCLUDE_SIZE == 64 * 1024

    def test_an_oversized_include_is_refused(self, workspace):
        root = workspace(
            {
                'lib.raml': LIB + 'types:\n  T:\n    type: string\n    description: !include big.txt\n',
                'big.txt': 'x' * 200,
            }
        )
        parse_from_path(root / 'lib.raml', ParseOptions(max_include_size=1000))
        with pytest.raises(RamlError):
            parse_from_path(root / 'lib.raml', ParseOptions(max_include_size=100))

    def test_zero_disables_the_limit(self, workspace):
        root = workspace(
            {
                'lib.raml': LIB + 'types:\n  T:\n    type: string\n    description: !include big.txt\n',
                'big.txt': 'x' * 200,
            }
        )
        assert parse_from_path(root / 'lib.raml', ParseOptions(max_include_size=0)) is not None


class TestD7OrderedMapsArePlainDicts:
    def test_declaration_order_survives(self, workspace):
        """The requirement go-raml carries a third-party ordered map for."""
        names = ['Zeta', 'alpha', 'Mid', 'beta']
        body = ''.join(f'  {name}: string\n' for name in names)
        root = workspace({'lib.raml': LIB + 'types:\n' + body})
        raml = parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True))
        assert list(raml.entry_point.types) == names
