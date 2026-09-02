"""URI template parsing and validation.

The rules under test come from docs/08-templates-and-endpoints.md section 8.2
(RFC 6570 Levels 1 and 2 only) and section 5.3 (`resourcePathName`). Malformed
templates must report the exact offending byte, so every error test asserts a
column rather than merely the presence of an error. See docs/14-testing.md.
"""

from __future__ import annotations

import pytest

from pyraml.errors import RamlError
from pyraml.parser.uritemplates import UriTemplateExpression, extract_uri_template_params, resource_path_name
from pyraml.positions import Position

LOC = 'file:///t/api.raml'
POS = Position(1, 1)


def _names(uri: str) -> list[tuple[str, str]]:
    return [(e.operator, e.name) for e in extract_uri_template_params(uri, LOC, POS)]


class TestSpecExamples:
    """The worked examples from the RAML spec's Template URIs section."""

    def test_simple_parameter(self):
        assert _names('/jobs/{jobId}') == [('', 'jobId')]

    def test_bare_parameter(self):
        assert _names('/{userId}') == [('', 'userId')]

    def test_two_parameters_in_one_segment(self):
        # "In the next example, the top-level resource has URI parameters
        # folderId and fileId": /folder_{folderId}-file_{fileId}
        assert _names('/folder_{folderId}-file_{fileId}') == [('', 'folderId'), ('', 'fileId')]

    def test_ext_parameter_glued_to_a_literal_segment(self):
        # "/users{ext}" — the reserved `ext` parameter, no separating slash.
        assert _names('/users{ext}') == [('', 'ext')]

    def test_array_valued_parameter_name(self):
        assert _names('/{userIds}') == [('', 'userIds')]

    def test_no_parameters_is_an_empty_list(self):
        assert _names('/users') == []


class TestOperators:
    def test_simple_expansion_has_no_operator(self):
        result = extract_uri_template_params('/{var}', LOC, POS)
        assert result == [UriTemplateExpression('', 'var')]

    def test_reserved_expansion_records_plus(self):
        result = extract_uri_template_params('/{+var}', LOC, POS)
        assert result == [UriTemplateExpression('+', 'var')]

    def test_fragment_expansion_records_hash(self):
        result = extract_uri_template_params('/{#var}', LOC, POS)
        assert result == [UriTemplateExpression('#', 'var')]


class TestValidVarnames:
    @pytest.mark.parametrize('name', ['a', 'a.b', 'a_b', 'a1', '%20'])
    def test_accepted(self, name):
        assert _names(f'/{{{name}}}') == [('', name)]


class TestMalformedVarnames:
    @pytest.mark.parametrize('name', ['a.', '.a', 'a..b', 'a-b', 'a b', '%2', '%zz'])
    def test_rejected(self, name):
        uri = f'/{{{name}}}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message in {
            'invalid character in variable name',
            'invalid pct-encoded sequence in variable name',
        }

    def test_leading_dot_points_at_the_dot(self):
        uri = '/{.a}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.position.column == uri.index('.') + 1

    def test_trailing_dot_points_at_the_dot(self):
        uri = '/{a.}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.position.column == uri.rindex('.') + 1

    def test_doubled_dot_points_at_the_second_dot(self):
        uri = '/{a..b}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.position.column == uri.rindex('.') + 1

    def test_invalid_character_reports_the_character(self):
        uri = '/{a-b}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.position.column == uri.index('-') + 1
        assert excinfo.value.head.info == {'character': '-'}

    def test_space_is_an_invalid_character(self):
        uri = '/{a b}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.position.column == uri.index(' ') + 1

    def test_truncated_pct_encoding_points_at_the_percent(self):
        uri = '/{%2}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == 'invalid pct-encoded sequence in variable name'
        assert excinfo.value.head.position.column == uri.index('%') + 1

    def test_non_hex_pct_encoding_points_at_the_percent(self):
        uri = '/{%zz}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == 'invalid pct-encoded sequence in variable name'
        assert excinfo.value.head.position.column == uri.index('%') + 1


class TestMalformedTemplates:
    def test_unclosed_brace_points_at_the_brace(self):
        uri = '/foo{bar'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == "unclosed '{'"
        assert excinfo.value.head.position.column == uri.index('{') + 1

    def test_nested_brace_points_at_the_inner_brace(self):
        uri = '/foo{bar{baz}}'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == "nested '{'"
        assert excinfo.value.head.position.column == uri.rindex('{') + 1

    def test_unexpected_closing_brace_points_at_it(self):
        uri = '/foo}bar'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == "unexpected '}'"
        assert excinfo.value.head.position.column == uri.index('}') + 1

    def test_empty_expression_points_at_the_opening_brace(self):
        uri = '/foo{}bar'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, POS)
        assert excinfo.value.head.message == 'empty expression'
        assert excinfo.value.head.position.column == uri.index('{') + 1

    def test_position_is_shifted_from_the_scalars_own_position(self):
        # The scalar's own position (line 5, column 10) must anchor the shift,
        # not an absolute (1, 1) — this is the whole reason the function takes
        # a `uri_pos` argument rather than assuming column 1.
        scalar_pos = Position(5, 10)
        uri = '/foo{bar'
        with pytest.raises(RamlError) as excinfo:
            extract_uri_template_params(uri, LOC, scalar_pos)
        offset = uri.index('{')
        assert excinfo.value.head.position.line == 5
        assert excinfo.value.head.position.column == 10 + offset


class TestResourcePathName:
    def test_trailing_non_parameter_segment(self):
        assert resource_path_name('/users/{userId}/addresses') == 'addresses'

    def test_trailing_parameter_segment_falls_back_to_the_parent(self):
        assert resource_path_name('/users/{userId}') == 'users'

    def test_concatenated_parameters_form_one_skipped_segment(self):
        assert resource_path_name('/bom/{itemId}{ext}') == 'bom'

    def test_trailing_slash_is_ignored(self):
        assert resource_path_name('/foo/bar/') == 'bar'

    def test_all_parameter_path_yields_nothing(self):
        assert resource_path_name('/{a}/{b}') == ''

    def test_empty_string_yields_nothing(self):
        assert resource_path_name('') == ''
