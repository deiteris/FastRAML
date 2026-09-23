"""Discovery of the RAML Test Compliance Kit fixtures.

The fixtures are a **submodule** at `tests/tck/raml-tck`, from
[deiteris/raml-tck](https://github.com/deiteris/raml-tck). Get them with the
clone, or afterwards:

    git clone --recurse-submodules <this repo>
    git submodule update --init            # if you already cloned

They are a submodule rather than vendored because they are not ours: the suite
comes from the archived `raml-org/raml-tck` and carries no licence, so it is
referenced at a commit rather than copied into this tree (docs/14 § 2).

`FASTRAML_TCK_DIR` still overrides, for running against a different checkout —
upstream, or a branch with a fixture fix under review:

    FASTRAML_TCK_DIR=/path/to/raml-tck uv run pytest tests/tck

With neither, the TCK tests skip rather than fail. A missing submodule is a
checkout that was not initialised, not a regression.

The kit's naming convention (from its README):

* ``*valid*.raml``   — must parse, unwrap and validate without error
* ``*invalid*.raml`` — must produce at least one error

See docs/14-testing.md § 2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

#: Individual fixtures skipped, with the reason. Not a coverage gap: `http(s)`
#: includes work when `ParseOptions.http_client` supplies a client, and these
#: two `!include` a gist. Running them would make the suite depend on the
#: network — and the negative one would go on "passing" offline for the wrong
#: reason, because an unreachable host and an unregistered URI scheme both
#: produce an error.
_NO_NETWORK = 'fetches an https include; the suite must not touch the network'

#: The one fixture the JSON Schema expression policy contradicts. The fixture is
#: the spec's example of `members: Person[]` over a JSON-schema type, which
#: fastRAML accepts (docs/01 § 4.5). Skipped rather than
#: ratcheted to `fail`, because a `fail` entry means work outstanding
#: (docs/14 § 3) and this is a supported policy, not a gap.
_JSON_SCHEMA_EXPRESSION_POLICY = 'JSON schema types may be used in type expressions'
SKIPPED_FIXTURES: dict[str, str] = {
    'Root/include-02/valid-https.raml': _NO_NETWORK,
    'Root/include-02/invalid-https.raml': _NO_NETWORK,
    'spec-examples/APIs/external-type-extend-invalid.raml': _JSON_SCHEMA_EXPRESSION_POLICY,
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


def skip_reason(fixture_key: str) -> str | None:
    """Why this fixture is skipped. Only named fixtures are; no category is."""
    return SKIPPED_FIXTURES.get(fixture_key)


def case_directory(root: Path, path: Path) -> Path:
    """`<category>/<case>/`: the directory one test case's documents share.

    The fixture's workspace root. An Overlay in `overlays/` may extend
    `../base.raml`, which the default root, the entry's own directory, would
    refuse (docs/19 § 2). A fixture filed directly in its category gets the
    category.
    """
    parts = path.relative_to(root).parts
    return root.joinpath(*parts[: min(2, len(parts) - 1)])


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
