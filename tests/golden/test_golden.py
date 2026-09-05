"""Whole-model regression — docs/14-testing.md section 2.

For inputs where "no error" is too weak an assertion. The TCK scores whether a
document parses; a unit test asserts the one thing it names. A golden asserts
*everything at once*, which is what catches a change that fixes one facet and
silently moves another.

Each case is a directory holding its own RAML plus `expected.json`. A case that
is about positions also holds `expected.pos.json`; the rest do not, so an edit
to a fixture cannot churn a position file that nobody is reading.

Regenerate with `pytest tests/golden --update-golden`. **Read the diff before
committing it** — a golden accepted unread is a test that asserts whatever the
code happened to do, which is worse than no test because it looks like one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyraml import ParseOptions, parse_from_path
from tests.golden.project import positions_of, project

CASES_DIR = Path(__file__).parent / 'cases'

#: Every case parses with both flags. A golden of an un-flattened model would
#: pin the declaration rather than the type, and the declaration is what the
#: unit tests already cover.
OPTIONS = ParseOptions(unwrap=True, validate=True)


def cases() -> list[Path]:
    return sorted(path for path in CASES_DIR.iterdir() if path.is_dir()) if CASES_DIR.is_dir() else []


def entry_of(case: Path) -> Path:
    """The case's entry document: `api.raml`, or `lib.raml` for a library."""
    for name in ('api.raml', 'lib.raml'):
        candidate = case / name
        if candidate.exists():
            return candidate
    pytest.fail(f'{case.name}: no api.raml or lib.raml')
    raise AssertionError


def dumped(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def check(path: Path, payload: object, *, update: bool) -> None:
    rendered = dumped(payload)
    if update:
        path.write_text(rendered, encoding='utf-8')
        return
    if not path.exists():
        pytest.fail(f'{path.name} missing; run pytest tests/golden --update-golden')
    expected = path.read_text(encoding='utf-8')
    if expected != rendered:
        # Compared as parsed JSON so pytest's assertion rewriting shows the
        # differing key rather than two walls of text.
        assert json.loads(rendered) == json.loads(expected), f'{path.parent.name}/{path.name} drifted'
        pytest.fail(f'{path.name} differs only in formatting; regenerate it')


@pytest.mark.parametrize('case', cases(), ids=lambda path: path.name)
def test_model_matches_its_golden(case: Path, request):
    update = request.config.getoption('--update-golden')
    raml = parse_from_path(entry_of(case), OPTIONS)
    check(case / 'expected.json', project(raml), update=update)

    positions = case / 'expected.pos.json'
    if positions.exists() or (update and (case / 'positions').exists()):
        check(positions, positions_of(raml), update=update)


def test_there_are_cases():
    """A parametrised test over an empty directory passes and means nothing."""
    assert len(cases()) >= 10, 'docs/14 § 2 lists the cases worth pinning'
