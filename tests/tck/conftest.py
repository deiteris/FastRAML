"""Discovery of the RAML Test Compliance Kit fixtures.

The fixtures are not vendored into this repository yet. Point the suite at a
checkout with `PYRAML_TCK_DIR`; without it the TCK tests are skipped:

    PYRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck

The kit's naming convention (from its README):

* ``*valid*.raml``   — must parse, unwrap and validate without error
* ``*invalid*.raml`` — must produce at least one error

See docs/14-testing.md section 1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

#: Fixture categories skipped wholesale, each with the reason. The list only
#: shrinks; every entry is a line item in docs/15-implementation-plan.md.
SKIPPED_CATEGORIES: dict[str, str] = {
    'Overlays/': 'overlays are planned for v1.1 (docs/01 section 3.5)',
    'Extensions/': 'extensions are planned for v1.1 (docs/01 section 3.5)',
}

#: The same two kinds, identified by what a document *is* rather than where it
#: sits. `EdgeCases/overlay-overrides-resources/valid.raml` is an Overlay filed
#: outside `Overlays/`, and matching on the path alone let it through as a
#: failure that read like missing coverage.
SKIPPED_HEADS: dict[str, str] = {
    '#%RAML 1.0 Overlay': SKIPPED_CATEGORIES['Overlays/'],
    '#%RAML 1.0 Extension': SKIPPED_CATEGORIES['Extensions/'],
}

#: Individual fixtures skipped, with the reason. Not a coverage gap: `http(s)`
#: includes work when `ParseOptions.http_client` supplies a client, and these
#: two `!include` a gist. Running them would make the suite depend on the
#: network — and the negative one would go on "passing" offline for the wrong
#: reason, because an unreachable host and an unregistered URI scheme both
#: produce an error.
_NO_NETWORK = 'fetches an https include; the suite must not touch the network'
SKIPPED_FIXTURES: dict[str, str] = {
    'Root/include-02/valid-https.raml': _NO_NETWORK,
    'Root/include-02/invalid-https.raml': _NO_NETWORK,
}

_ENV_VAR = 'PYRAML_TCK_DIR'
_DEFAULT_RELATIVE = Path('..') / 'go-raml-main' / 'raml-tck'


def tck_root() -> Path | None:
    """The TCK checkout, or `None` when it cannot be found.

    Looks at `PYRAML_TCK_DIR` first, then at a sibling go-raml checkout, which
    is the layout this parser is developed against.
    """
    configured = os.environ.get(_ENV_VAR)
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_dir() else None

    repo_root = Path(__file__).resolve().parents[2]
    candidate = (repo_root / _DEFAULT_RELATIVE).resolve()
    return candidate if candidate.is_dir() else None


def fixture_id(root: Path, path: Path) -> str:
    """A stable, platform-independent identifier used as the ratchet key."""
    return path.relative_to(root).as_posix()


def collect_fixtures(kind: str) -> list[Path]:
    """Every `*valid*.raml` or `*invalid*.raml` fixture, in a stable order.

    `valid` excludes `invalid`, since 'invalid' contains 'valid' as a substring
    and glob matching alone would put every negative fixture in both lists.
    """
    root = tck_root()
    if root is None:
        return []
    matches = (p for p in root.rglob('*.raml') if kind in p.name)
    if kind == 'valid':
        matches = (p for p in matches if 'invalid' not in p.name)
    return sorted(matches)


def skip_reason(fixture_key: str, path: Path | None = None) -> str | None:
    """Why this fixture is skipped, by name, by directory, or by its RAML header."""
    named = SKIPPED_FIXTURES.get(fixture_key)
    if named is not None:
        return named
    for prefix, reason in SKIPPED_CATEGORIES.items():
        if fixture_key.startswith(prefix):
            return reason
    if path is not None:
        try:
            head = path.read_text(encoding='utf-8', errors='replace').partition('\n')[0].strip()
        except OSError:
            return None
        return SKIPPED_HEADS.get(head)
    return None


@pytest.fixture(scope='session')
def tck_dir() -> Path:
    root = tck_root()
    if root is None:
        pytest.skip(f'TCK fixtures not found; set {_ENV_VAR} (see docs/14-testing.md)')
    return root


# --- the ratchet -------------------------------------------------------------

RATCHET_PATH = Path(__file__).with_name('ratchet.json')


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--update-ratchet',
        action='store_true',
        default=False,
        help='rewrite tests/tck/ratchet.json from this run instead of comparing against it',
    )


@pytest.fixture(scope='session')
def ratchet() -> dict[str, str]:
    """The recorded outcome per fixture, keyed by `fixture_id`."""
    if not RATCHET_PATH.exists():
        return {}
    with RATCHET_PATH.open(encoding='utf-8') as handle:
        outcomes: dict[str, str] = json.load(handle).get('outcomes', {})
    return outcomes


@pytest.fixture(scope='session')
def _observed() -> dict[str, str]:
    return {}


@pytest.fixture
def record_outcome(request: pytest.FixtureRequest, _observed: dict[str, str]):
    """Collect this run's outcomes so `--update-ratchet` can write them out."""

    def record(key: str, outcome: str) -> None:
        _observed[key] = outcome

    yield record

    if not request.config.getoption('--update-ratchet'):
        return
    payload = {
        'comment': (
            'Expected TCK outcome per fixture. "pass" means the parser did what the '
            "fixture's name promises. Regenerate with pytest --update-ratchet."
        ),
        'outcomes': dict(sorted(_observed.items())),
    }
    with RATCHET_PATH.open('w', encoding='utf-8', newline='\n') as handle:
        json.dump(payload, handle, indent=2)
        handle.write('\n')
