"""Path and URI handling.

The rules under test come from docs/03-yaml-and-io.md section 8. Windows is the
platform that breaks these, so its cases are exercised on every platform where
the behaviour is platform-independent and marked where it is not.
"""

from __future__ import annotations

import os

import pytest

from fastraml.parser.uritemplates import simple_parameter_segment
from fastraml.uris import (
    file_uri_to_path,
    is_file_uri,
    path_to_file_uri,
    resolve_uri_ref,
    uri_base,
    uri_scheme,
)

WINDOWS = os.name == 'nt'
posix_only = pytest.mark.skipif(WINDOWS, reason='POSIX path semantics')
windows_only = pytest.mark.skipif(not WINDOWS, reason='Windows path semantics')


@pytest.mark.parametrize('segment', ['{id}', '{id}{extension}'])
def test_simple_parameter_segment_uses_parsed_template_expressions(segment):
    assert simple_parameter_segment(segment)


@pytest.mark.parametrize('segment', ['user-{id}', '{+path}', '{#fragment}', 'users'])
def test_non_simple_parameter_segment_is_not_a_route_wildcard(segment):
    assert not simple_parameter_segment(segment)


class TestPathToFileUri:
    def test_is_idempotent(self):
        uri = 'file:///a/b.raml'
        assert path_to_file_uri(uri) == uri
        assert path_to_file_uri(path_to_file_uri(uri)) == uri

    def test_normalises_before_encoding(self):
        # The cache key must not depend on how the caller spelled the path.
        assert path_to_file_uri('/a/b/../c.raml') == path_to_file_uri('/a/c.raml')

    def test_accepts_pathlike(self):
        from pathlib import PurePath

        assert path_to_file_uri(PurePath('/a/b.raml')) == path_to_file_uri('/a/b.raml')

    def test_escapes_characters_that_break_uris(self):
        uri = path_to_file_uri('/a/my types.raml')
        assert ' ' not in uri
        assert uri.endswith('my%20types.raml')

    def test_keeps_colon_unescaped(self):
        # A Windows drive must read as file:///C:/x, not file:///C%3A/x.
        assert path_to_file_uri('/C:/x.raml') == 'file:///C:/x.raml'

    @posix_only
    def test_posix_absolute_path(self):
        assert path_to_file_uri('/srv/api.raml') == 'file:///srv/api.raml'

    @windows_only
    def test_windows_drive_gains_a_third_slash(self):
        assert path_to_file_uri(r'C:\Sources\api.raml') == 'file:///C:/Sources/api.raml'


class TestFileUriToPath:
    def test_round_trips(self):
        original = os.path.abspath(os.path.join(os.sep, 'a', 'b.raml'))
        assert file_uri_to_path(path_to_file_uri(original)) == original

    def test_round_trips_a_path_with_spaces(self):
        original = os.path.abspath(os.path.join(os.sep, 'a', 'my types.raml'))
        assert file_uri_to_path(path_to_file_uri(original)) == original

    @pytest.mark.parametrize(
        'value',
        ['/a/b.raml', 'https://example.com/a.raml', 'http://example.com/a.raml', 'nonsense', ''],
    )
    def test_rejects_anything_that_is_not_a_file_uri(self, value):
        # A silent empty return would let a non-file value be treated as a path.
        with pytest.raises(ValueError, match='not a file URI'):
            file_uri_to_path(value)

    @windows_only
    def test_windows_strips_the_leading_slash(self):
        assert file_uri_to_path('file:///C:/Sources/api.raml') == r'C:\Sources\api.raml'


class TestResolveUriRef:
    def test_relative_reference(self):
        assert resolve_uri_ref('file:///a/api.raml', 'types/user.raml') == 'file:///a/types/user.raml'

    def test_parent_reference(self):
        assert resolve_uri_ref('file:///a/b/api.raml', '../types.raml') == 'file:///a/types.raml'

    def test_absolute_reference_replaces_the_path(self):
        # RFC 3986 resolution. RAML-absolute includes are rewritten against the
        # workspace root *before* reaching this function; see docs/03 section 4.1.
        assert resolve_uri_ref('file:///a/b/api.raml', '/x.raml') == 'file:///x.raml'

    def test_url_reference_wins_outright(self):
        assert resolve_uri_ref('file:///a/api.raml', 'https://e.com/t.raml') == 'https://e.com/t.raml'

    def test_backslashes_are_normalised(self):
        # os.path.relpath produces these on Windows; they are not URI separators.
        assert resolve_uri_ref('file:///a/api.raml', r'types\user.raml') == 'file:///a/types/user.raml'

    def test_preserves_a_json_pointer_fragment(self):
        # `!include schema.json#/definitions/Foo` must keep its fragment.
        resolved = resolve_uri_ref('file:///a/api.raml', 'schema.json#/definitions/Foo')
        assert resolved == 'file:///a/schema.json#/definitions/Foo'

    def test_escapes_a_space_in_a_reference(self):
        assert resolve_uri_ref('file:///a/api.raml', 'my types.raml') == 'file:///a/my%20types.raml'


class TestUriHelpers:
    @pytest.mark.parametrize(
        ('uri', 'expected'),
        [
            ('file:///a/b/user.raml', 'user.raml'),
            ('https://e.com/x/user.raml', 'user.raml'),
            ('file:///a/my%20types.raml', 'my types.raml'),
        ],
    )
    def test_uri_base(self, uri, expected):
        assert uri_base(uri) == expected

    @pytest.mark.parametrize(
        ('uri', 'expected'),
        [
            ('file:///a.raml', 'file'),
            ('http://e.com/a', 'http'),
            ('https://e.com/a', 'https'),
            ('/a/b.raml', ''),
            (r'C:\a\b.raml', ''),
            ('ftp://e.com/a', ''),
        ],
    )
    def test_uri_scheme(self, uri, expected):
        # A Windows drive letter must not be mistaken for a scheme.
        assert uri_scheme(uri) == expected

    def test_is_file_uri(self):
        assert is_file_uri('file:///a.raml')
        assert not is_file_uri('/a.raml')
