"""`ParseOptions(regex_engine='re2')` — deviation D3.

`re` is the default because it is closer to ECMA-262, which is what the spec
means by a regular expression. `re2` is linear-time and is what a server parsing
untrusted RAML should select; it accepts strictly less, which is the trade.

The rule pinned here is narrow and worth stating exactly: **every regex fastRAML
compiles goes through `regex_engine`**, RAML facets and the JSON Schema
projection alike. What it does *not* reach is the regexes executed inside an
external JSON Schema at validation time — the schema library calls `re.search`
itself and offers no hook. The last test names that, so the limit of the
guarantee is recorded rather than assumed.
"""

from __future__ import annotations

import json

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path
from fastraml.parser.facets import regex_engine
from fastraml.registry import Raml
from tests.unit.test_jsonschema import API, indent

re2 = pytest.importorskip('re2', reason='the optional google-re2 package is not installed')

LIB = '#%RAML 1.0 Library\n'
RE2 = ParseOptions(regex_engine='re2')

#: Valid ECMA-262 and valid `re`; RE2 has no backreferences at all, which is
#: exactly why it is linear-time.
BACKREFERENCE = '(a)\\1'


def messages(error: RamlError) -> list[str]:
    return [trace.message for chain in error.chains() for trace in chain]


def schema_shape(workspace, schema: dict, **options):
    """The `JsonShape` of `T`, declared inline so the schema is its own."""
    root = workspace({'api.raml': API + 'types:\n  T: |\n' + indent(json.dumps(schema))})
    raml = parse_from_path(root / 'api.raml', ParseOptions(regex_engine='re2', **options))
    return raml.types_in(raml.location)['T'].shape


class TestSelection:
    def test_the_default_is_the_standard_library(self):
        import re

        assert regex_engine(Raml()) is re

    def test_re2_is_selected_when_asked_for(self):
        assert regex_engine(Raml(regex_engine='re2')) is re2


class TestRamlPatterns:
    def test_a_pattern_facet_compiles_under_re2(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  T:\n    type: string\n    pattern: ^[a-z]+$\n'})
        raml = parse_from_path(root / 'lib.raml', RE2)
        pattern = raml.entry_point.types['T'].shape.pattern.value
        assert isinstance(pattern, re2._Regexp)
        assert pattern.fullmatch('abc')
        assert not pattern.fullmatch('AB1')

    def test_a_pattern_property_compiles_under_re2(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  T:\n    properties:\n      /^x/: string\n'})
        raml = parse_from_path(root / 'lib.raml', ParseOptions(regex_engine='re2', unwrap=True))
        pattern = raml.entry_point.types['T'].shape.pattern_properties['^x'].pattern
        assert isinstance(pattern, re2._Regexp)

    def test_a_backreference_is_refused_under_re2_and_accepted_under_re(self, workspace):
        """The deviation demonstrated, rather than described.

        A document relying on a backreference parses on the default engine and
        is refused on the safe one. Both halves are asserted: without the first,
        the test would still pass if the pattern were simply invalid.
        """
        # Single-quoted: a double-quoted YAML scalar processes `\1` as an escape
        # and never reaches the regex engine at all.
        root = workspace({'lib.raml': LIB + f"types:\n  T:\n    type: string\n    pattern: '{BACKREFERENCE}'\n"})
        parse_from_path(root / 'lib.raml')

        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', RE2)
        assert 'invalid pattern' in messages(caught.value)


class TestJsonSchemaPatterns:
    """The § 6.3 projection compiles patterns too, so it honours the option."""

    def test_a_schema_pattern_property_goes_through_the_engine(self, workspace):
        shape = schema_shape(workspace, {'type': 'object', 'patternProperties': {'^x': {'type': 'string'}}})
        projected = shape.as_shape().shape.pattern_properties
        assert isinstance(next(iter(projected.values())).pattern, re2._Regexp)

    def test_a_schema_pattern_facet_goes_through_the_engine(self, workspace):
        shape = schema_shape(workspace, {'type': 'string', 'pattern': '^[a-z]+$'})
        assert isinstance(shape.as_shape().shape.pattern.value, re2._Regexp)

    def test_a_backreference_in_a_schema_drops_the_projected_constraint(self, workspace):
        """A facet `re2` cannot compile is dropped from the *view*, not fatal.

        The projection is a convenience over the compiled schema, and the schema
        itself still validates, so refusing the whole type would lose more than
        it protects. The same rule already covered ECMA-262 patterns `re` will
        not take; `re2` widens the set it applies to.
        """
        shape = schema_shape(workspace, {'type': 'string', 'pattern': BACKREFERENCE})
        assert shape.as_shape().shape.pattern is None

    def test_validation_inside_a_schema_is_not_covered(self, workspace):
        """The documented limit of D3, as a test so it cannot rot silently.

        `jsonschema` calls `re.search` directly for `pattern`, so a backreference
        inside a schema still works under `regex_engine='re2'`. There is no hook
        to change that, but a consumer parsing hostile input needs to know the
        guarantee stops at the schema boundary.
        """
        shape = schema_shape(workspace, {'type': 'string', 'pattern': BACKREFERENCE})
        assert shape.validate('aa', '$') is None  # matched by `re`, backreference and all
        with pytest.raises(RamlError):
            shape.validate('ab', '$')
