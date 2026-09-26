"""The occurrence law and the round trip, over the fixtures and the TCK (docs/16 § 9).

The law: the retained text at an occurrence's span is the name it records. The
round trip: every reference and prefix meets exactly one definition on its
target. Both are checked on every valid document the corpora hold.

`DROPPED` counts, per document, the candidates the law rejects today. It is a
ratchet, like `tests/tck/ratchet.json`: a new drop fails, and so does a fix
that leaves its entry behind. Each entry is a gap listed in
`research/language-service-plan.md` M3.3.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from fastraml import ParseOptions, parse_lenient
from fastraml.views.occurrences import Role, build_occurrences
from tests.tck.conftest import case_directory, collect_fixtures, fixture_id, tck_root

if TYPE_CHECKING:
    from fastraml.registry import Raml
    from fastraml.views.occurrences import Occurrences

_FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures'

#: A template substitution keeps the template's position (G2).
_SUBSTITUTED: Final = {
    'Annotations/complex-06/valid-params.raml': 1,
    'EdgeCases/inclusion-paths/valid.raml': 1,
    'EdgeCases/missing-subtypes/valid.raml': 12,
    'EdgeCases/parsing-param-array-type/valid-parsing-param-array-type.raml': 6,
    'Resources/request-datatype-property/valid.raml': 1,
    **{f'Resources/restype-datatype-property-0{n}/valid.raml': 1 for n in range(1, 9)},
    'ResourceTypes/chaining-functions/valid.raml': 1,
    **{f'ResourceTypes/datatype-properties-0{n}/valid.raml': 1 for n in range(1, 10)},
    **{f'Traits/datatype-properties-0{n}/valid.raml': 1 for n in range(1, 5)},
}

DROPPED: Final = {
    **_SUBSTITUTED,
    # A substitution.
    'fixtures/sample/api.raml': 1,
}


def round_trip(raml: Raml, occurrences: Occurrences) -> list[str]:
    """Every reference and prefix that does not meet exactly one definition."""
    uses = (
        found
        for uri in raml.source_texts
        for found in occurrences.in_file(uri)
        if found.role in {Role.REFERENCE, Role.ALIAS_PREFIX}
    )
    return [
        f'{use} meets {count} definitions'
        for use in uses
        if (count := sum(found.role is Role.DEFINITION for found in occurrences.of(use.target))) != 1
    ]


def _check(entry: Path, workspace: Path, name: str, dropped: dict[str, int], broken: list[str]) -> None:
    options = ParseOptions(unwrap=True, validate=False, retain_text=True, workspace_root=workspace)
    raml, _ = parse_lenient(entry, options)
    occurrences = build_occurrences(raml)
    if occurrences.dropped:
        dropped[name] = len(occurrences.dropped)
    broken += [f'{name}: {problem}' for problem in round_trip(raml, occurrences)]


def _expected(names: set[str]) -> dict[str, int]:
    return {name: count for name, count in DROPPED.items() if name in names}


def test_the_fixtures_keep_the_law_and_the_round_trip():
    dropped: dict[str, int] = {}
    broken: list[str] = []
    _check(_FIXTURES / 'sample' / 'api.raml', _FIXTURES, 'fixtures/sample/api.raml', dropped, broken)
    assert not broken, broken
    assert dropped == _expected({'fixtures/sample/api.raml'})


@pytest.mark.tck
def test_the_tck_keeps_the_law_and_the_round_trip():
    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
    dropped: dict[str, int] = {}
    broken: list[str] = []
    names = set()
    for path in collect_fixtures('valid'):
        name = fixture_id(root, path)
        names.add(name)
        _check(path, case_directory(root, path), name, dropped, broken)
    assert not broken, broken
    assert dropped == _expected(names)
