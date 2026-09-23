"""The RAML Test Compliance Kit, and the ratchet that tracks progress against it.

Every evaluated fixture runs and its outcome is compared with `ratchet.json`. CI fails on
either direction of drift:

* a fixture that passed now fails — a regression;
* a fixture that was expected to fail now passes and the ratchet was not
  updated — progress must be recorded, so coverage claims stay verifiable.

Regenerate after intentional change:

    FASTRAML_TCK_DIR=... uv run pytest tests/tck --update-ratchet

See docs/14-testing.md § 2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.tck.conftest import collect_fixtures, fixture_id, skip_reason, tck_root

RATCHET_PATH = Path(__file__).with_name('ratchet.json')

pytestmark = pytest.mark.tck


def parser_available() -> bool:
    """Whether the parser entry point is importable."""
    import fastraml

    return hasattr(fastraml, 'parse_from_path')


requires_parser = pytest.mark.skipif(
    not parser_available(),
    reason='parser entry point is unavailable',
)


def load_ratchet() -> dict[str, str]:
    if not RATCHET_PATH.exists():
        return {}
    with RATCHET_PATH.open(encoding='utf-8') as handle:
        data = json.load(handle)
    outcomes: dict[str, str] = data.get('outcomes', {})
    return outcomes


def _fixture_params(kind: str) -> list[pytest.ParameterSet]:
    root = tck_root()
    if root is None:
        return []
    return [pytest.param(path, id=fixture_id(root, path)) for path in collect_fixtures(kind)]


def _run_fixture(path: Path, root: Path, *, expect_error: bool) -> str:
    """Run one fixture and report `'pass'` or `'fail'`.

    'pass' means the parser did what the fixture's name promises: a valid
    fixture parsed, or an invalid one produced an error.
    """
    from fastraml import ParseOptions, RamlError, parse_from_path

    key = skip_reason(fixture_id(root, path), path)
    if key is not None:
        pytest.skip(key)

    try:
        parse_from_path(path, ParseOptions(validate=True, unwrap=True))
    except RamlError:
        return 'pass' if expect_error else 'fail'
    return 'fail' if expect_error else 'pass'


@requires_parser
@pytest.mark.parametrize('fixture', _fixture_params('valid'))
def test_valid_fixture(fixture: Path, tck_dir: Path, ratchet: dict[str, str], record_outcome):
    key = fixture_id(tck_dir, fixture)
    outcome = _run_fixture(fixture, tck_dir, expect_error=False)
    record_outcome(key, outcome)
    assert outcome == ratchet.get(key, 'fail'), _drift_message(key, outcome, ratchet.get(key, 'fail'))


@requires_parser
@pytest.mark.parametrize('fixture', _fixture_params('invalid'))
def test_invalid_fixture(fixture: Path, tck_dir: Path, ratchet: dict[str, str], record_outcome):
    key = fixture_id(tck_dir, fixture)
    outcome = _run_fixture(fixture, tck_dir, expect_error=True)
    record_outcome(key, outcome)
    assert outcome == ratchet.get(key, 'fail'), _drift_message(key, outcome, ratchet.get(key, 'fail'))


def _drift_message(key: str, actual: str, expected: str) -> str:
    if actual == 'fail':
        return f'regression: {key} was expected to {expected} but now fails'
    return f'progress on {key}: rerun with --update-ratchet to record it'


# --- harness tests -----------------------------------------------------------
# These cover discovery, keys, skip policy, and ratchet-file validation directly.


class TestDiscovery:
    def test_the_kit_is_found_and_is_the_expected_size(self, tck_dir: Path):
        # go-raml's copy carries 496 valid and 471 invalid fixtures, 967 in
        # total. A markedly different count means a different checkout, which
        # would make the ratchet meaningless.
        assert len(collect_fixtures('valid')) > 450
        assert len(collect_fixtures('invalid')) > 400

    def test_valid_and_invalid_sets_do_not_overlap(self, tck_dir: Path):
        # 'invalid' contains 'valid' as a substring; the filter must exclude it.
        valid = set(collect_fixtures('valid'))
        invalid = set(collect_fixtures('invalid'))
        assert not (valid & invalid)

    def test_fixture_ids_are_posix_and_relative(self, tck_dir: Path):
        for path in collect_fixtures('valid')[:20]:
            key = fixture_id(tck_dir, path)
            assert '\\' not in key
            assert not key.startswith('/')

    def test_skip_list_matches_by_category_prefix(self):
        assert skip_reason('Overlays/basic/valid.raml') is not None
        assert skip_reason('Types/array-types/valid.raml') is None

    def test_an_overlay_outside_its_category_is_skipped_by_its_header(self, tmp_path):
        # `EdgeCases/overlay-overrides-resources/valid.raml` is an Overlay filed
        # elsewhere. Matching on the path alone read it as missing coverage.
        overlay = tmp_path / 'valid.raml'
        overlay.write_text('#%RAML 1.0 Overlay\ntitle: T\nextends: base.raml\n', encoding='utf-8')
        assert skip_reason('EdgeCases/somewhere/valid.raml', overlay) is not None

        api = tmp_path / 'api.raml'
        api.write_text('#%RAML 1.0\ntitle: T\n', encoding='utf-8')
        assert skip_reason('EdgeCases/somewhere/api.raml', api) is None


class TestRatchetFile:
    def test_ratchet_parses_and_holds_only_known_outcomes(self):
        assert set(load_ratchet().values()) <= {'pass', 'fail'}

    def test_ratchet_keys_are_posix_relative_paths(self):
        for key in load_ratchet():
            assert '\\' not in key
            assert not key.startswith('/')
