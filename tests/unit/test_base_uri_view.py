"""`bound_base_uri`: the base URI a caller sends requests to (docs/16 § 8)."""

from __future__ import annotations

import fastraml
from fastraml import ParseOptions, parse_from_string


def api(tmp_path, body: str) -> fastraml.APIFragment:
    raml = parse_from_string(
        f'#%RAML 1.0\ntitle: T\n{body}', file_name='api.raml', base_dir=tmp_path, options=ParseOptions()
    )
    entry = raml.entry_point
    assert isinstance(entry, fastraml.APIFragment)
    return entry


def test_version_is_bound_and_every_other_variable_stays(tmp_path):
    written = 'version: v2\nbaseUri: https://{tenant}.example.com/{version}\n'
    assert fastraml.bound_base_uri(api(tmp_path, written)) == 'https://{tenant}.example.com/v2'


def test_a_numeric_version_is_bound_as_written(tmp_path):
    assert fastraml.bound_base_uri(api(tmp_path, 'version: 1\nbaseUri: https://example.com/{version}\n')) == (
        'https://example.com/1'
    )


def test_without_a_version_nothing_is_guessed(tmp_path):
    # The document a lint rule reports: `{version}` with no `version:` to take.
    assert fastraml.bound_base_uri(api(tmp_path, 'baseUri: https://example.com/{version}\n')) == (
        'https://example.com/{version}'
    )


def test_version_declared_as_a_parameter_is_the_callers(tmp_path):
    written = (
        'version: v2\nbaseUri: https://example.com/{version}\nbaseUriParameters:\n  version:\n    enum: [v1, v2]\n'
    )
    assert fastraml.bound_base_uri(api(tmp_path, written)) == 'https://example.com/{version}'


def test_no_base_uri_is_none(tmp_path):
    assert fastraml.bound_base_uri(api(tmp_path, 'version: v2\n')) is None
