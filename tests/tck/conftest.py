"""Discovery of the RAML Test Compliance Kit fixtures.

The fixtures are a **submodule** at `tests/tck/raml-tck`, from
[deiteris/raml-tck](https://github.com/deiteris/raml-tck). Get them with the
clone, or afterwards:

    git clone --recurse-submodules <this repo>
    git submodule update --init            # if you already cloned

They are a submodule rather than vendored because they are not ours: the suite
comes from the archived `raml-org/raml-tck` and carries no licence, so it is
referenced at a commit rather than copied into this tree (docs/14 section 1.1).

`FASTRAML_TCK_DIR` still overrides, for running against a different checkout —
upstream, or a branch with a fixture fix under review:

    FASTRAML_TCK_DIR=../go-raml-main/raml-tck uv run pytest tests/tck

With neither, the TCK tests skip rather than fail. A missing submodule is a
checkout that was not initialised, not a regression.

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

#: The one fixture a deliberate deviation contradicts. The fixture is correct —
#: it is the spec's own example of `members: Person[]` over a JSON-schema type —
#: and fastRAML accepts it on purpose (docs/01 § 4, D11). Skipped rather than
#: ratcheted to `fail`, because a `fail` entry means work outstanding
#: (docs/14 § 1.2) and this is a decision, not a gap.
_D11 = 'deviation D11: a JSON schema type may be used in a type expression'
SKIPPED_FIXTURES: dict[str, str] = {
    'Root/include-02/valid-https.raml': _NO_NETWORK,
    'Root/include-02/invalid-https.raml': _NO_NETWORK,
    'spec-examples/APIs/external-type-extend-invalid.raml': _D11,
}

_ENV_VAR = 'FASTRAML_TCK_DIR'

#: The submodule, then the fixture root inside it. `tests/raml-1.0` is the
#: upstream layout, which the fork keeps: the submodule's own root holds its
#: README, CONTRIBUTING and KNOWN-ISSUES, and only that subtree is fixtures.
_SUBMODULE = Path('tests') / 'tck' / 'raml-tck' / 'tests' / 'raml-1.0'


def tck_root() -> Path | None:
    """The TCK fixtures, or `None` when they cannot be found.

    `FASTRAML_TCK_DIR` wins, so a different checkout can be run against without
    touching the submodule; otherwise the submodule. `None` means skip, which is
    what an uninitialised submodule should produce -- a clone without
    `--recurse-submodules` is not a failing test run.
    """
    configured = os.environ.get(_ENV_VAR)
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_dir() else None

    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / _SUBMODULE
    # `any()` rather than `is_dir()`: an uninitialised submodule leaves the
    # directory behind as an empty placeholder, which is a directory that would
    # collect zero fixtures and report 951 silent passes as 951 silent skips.
    return candidate if candidate.is_dir() and any(candidate.iterdir()) else None


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
