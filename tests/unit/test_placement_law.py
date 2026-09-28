"""The placement law, over the fixtures and the TCK
(research/language-service-architecture.md § 7).

Every entity the walk reaches that the model positions:

1. is keyed on one line, where its location's text is its name as written:
   a property as `name?`, a body without a media type as `body`, a template's
   key as its `<<parameter>>`;
2. has a value that ends after it starts and starts after its key does;
3. and, for a resource nested in one written in the same file, lies in that
   resource's span. Members a template can contribute are not checked yet:
   what a template contributed is not recorded (§ 6, F2).

What the walk does not report is checked from the model: each `uses:` entry,
each documentation item, placed at its title, which its span holds, and each
example, whose single form is keyed `example`.

A violation is a parser defect, fixed in its pass. Exempt, each for its
reason: a request, which has the method's key; a URI parameter P6
synthesized, and its shape, which were never written; a shape with no name,
or one standing for a type it names, placed at the key it is written under and
checked through what holds it; and an unknown position, which a JSON-projected
shape or an entity built with no source has.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from fastraml import ParseOptions, RamlError, parse_lenient
from fastraml.positions import UNKNOWN, Position
from fastraml.types.complex_ import RecursiveShape
from fastraml.types.examples import examples_of
from fastraml.views.walk import Walk
from tests.tck.conftest import case_directory, collect_fixtures, fixture_id, tck_root

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape

_FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures'

#: What each role the walk reports is named by, and what holds its position.
_NAMES: Final[dict[str, Callable[[Any], str]]] = {
    'type_': lambda entity: entity.name,
    'property_': lambda entity: entity.name,
    'pattern_property': lambda entity: f'/{entity.pattern.pattern}/',
    'parameter': lambda entity: entity.name,
    'payload': lambda entity: entity.media_type,
    'response': lambda entity: entity.code,
    'operation': lambda entity: entity.method,
    'endpoint': lambda entity: entity.uri,
    'trait': lambda entity: entity.name,
    'resource_type': lambda entity: entity.name,
    'security_scheme': lambda entity: entity.name,
}

_LINE_BREAK: Final = re.compile(r'\r\n|\r|\n')


class _Seen:
    """A walk sink recording every entity it is given, by role, once each."""

    def __init__(self) -> None:
        self.found: dict[int, tuple[str, Any]] = {}

    def __getattr__(self, role: str) -> Callable[..., None]:
        def record(_iri: str, entity: object = None, *_rest: object) -> None:
            if role in _NAMES:
                self.found.setdefault(id(entity), (role, entity))

        return record


def _names_another(base: BaseShape) -> bool:
    """Whether `base` stands for a type it names, as `Book[]`'s items or a
    recursive reference do: placed at the key it is written under, and named
    after that type (§ 6, F4).
    """
    return base.alias is not None or isinstance(base.shape, RecursiveShape)


def _written(name: str) -> set[str]:
    return {form for bare in (name, f'{name}?') for form in (bare, f'"{bare}"', f"'{bare}'")}


def violations(raml: Raml) -> list[str]:
    """Every entity of `raml` that breaks the law, as `role name: rule`."""
    seen = _Seen()
    Walk(raml, 'law://', seen).run()  # type: ignore[arg-type]
    lines = {uri: _LINE_BREAK.split(text) for uri, text in raml.source_texts.items()}
    found: list[str] = []
    synthesized = {
        id(entity.base) for role, entity in seen.found.values() if role == 'parameter' and entity.synthesized
    }
    for role, entity in seen.found.values():
        if role == 'parameter' and entity.synthesized:
            continue
        name = _NAMES[role](entity)
        if role == 'type_' and (not name or id(entity) in synthesized or _names_another(entity)):
            continue
        # A property is a record; its shape holds where it was written.
        placed = entity.base if role in {'property_', 'pattern_property'} else entity
        location = getattr(placed, 'location', None) or entity.base.location
        forms = _written(name) | ({'body'} if role == 'payload' else set())
        found += _placed(lines, f'{role} {name}', location, key=placed.key_pos, value=placed.value_pos, forms=forms)
    return found + _unwalked(raml, lines) + _nesting(raml)


def _placed(  # noqa: PLR0913 - an entity's name, file, key, value and spellings
    lines: dict[str, list[str]], what: str, location: str, *, key: Position, value: Position, forms: set[str]
) -> list[str]:
    """Rules 1 and 2 for one entity: its key is one line reading one of
    `forms`, or a `<<parameter>>`; its value starts at or after its key and
    ends after it starts.
    """
    if not key.is_known:
        return []
    where = f'{what} at {location.rsplit("/", 1)[-1]}:{key}'
    text = lines.get(location)
    if key.line != key.end_line or text is None or key.line > len(text):
        return [f'{where}: its key is not one line of its file']
    found: list[str] = []
    written = text[key.line - 1][key.column - 1 : key.end_column - 1]
    if written not in forms and '<<' not in written:
        found.append(f'{where}: its key reads {written!r}')
    if value.is_known and (
        (value.end_line, value.end_column) < (value.line, value.column)
        or (value.line, value.column) < (key.line, key.column)
    ):
        found.append(f'{where}: its value {value}-{value.end_line}:{value.end_column} is misplaced')
    return found


def _unwalked(raml: Raml, lines: dict[str, list[str]]) -> list[str]:
    """Rules 1 to 3 for what the walk does not report: `uses:` entries,
    documentation items and examples.

    A documentation item has no key: its title's value is where its name is
    written, and its span holds it. A single `example:` is keyed `example`.
    """
    found: list[str] = []
    for fragment in raml.fragments.values():
        for alias, link in fragment.uses.items():
            found += _placed(
                lines, f'uses {alias}', link.location, key=link.key_pos, value=link.value_pos, forms=_written(alias)
            )
    api = raml.entry_point
    items = [*getattr(api, 'documentation', ())]
    items += [item for fragment in raml.fragments.values() if (item := getattr(fragment, 'item', None)) is not None]
    for item in dict.fromkeys(items):
        title = item.title
        if title is None:
            continue
        name = str(title.value)
        found += _placed(
            lines, f'documentation {name}', item.location, key=title.value_pos, value=UNKNOWN, forms=_written(name)
        )
        if item.value_pos.is_known and not item.value_pos.contains(title.value_pos):
            found.append(f'documentation {name}: its title lies outside it')
    examples = {id(example): example for base in raml.shapes for example in examples_of(base)}
    for example in examples.values():
        forms = _written(example.name) | {'example'}
        found += _placed(
            lines,
            f'example {example.name}',
            example.location,
            key=example.key_pos,
            value=example.value_pos,
            forms=forms,
        )
    return found


def _nesting(raml: Raml) -> list[str]:
    """Rule 3 for resources, which no template contributes."""
    found: list[str] = []
    for endpoint in raml.endpoints.values():
        for child in endpoint.endpoints.values():
            if child.location != endpoint.location:
                continue
            span = Position.covering((endpoint.key_pos, endpoint.value_pos))
            if not span.contains(Position.covering((child.key_pos, child.value_pos))):
                found.append(f'resource {child.full_uri}: outside {endpoint.full_uri}')
    return found


def _check(entry: Path, workspace: Path, name: str, broken: list[str]) -> None:
    options = ParseOptions(unwrap=True, validate=False, retain_text=True, workspace_root=workspace)
    try:
        raml, _ = parse_lenient(entry, options)
    except (RamlError, OSError):
        # An entry that is not RAML at all holds no entity (docs/13 § 1).
        return
    if raml.unwrapped:
        broken += [f'{name}: {problem}' for problem in violations(raml)]


def test_the_fixtures_keep_the_placement_law():
    broken: list[str] = []
    _check(_FIXTURES / 'sample' / 'api.raml', _FIXTURES, 'fixtures/sample/api.raml', broken)
    assert not broken, broken


@pytest.mark.tck
def test_the_tck_keeps_the_placement_law():
    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
    broken: list[str] = []
    for path in [*collect_fixtures('valid'), *collect_fixtures('invalid')]:
        _check(path, case_directory(root, path), fixture_id(root, path), broken)
    assert not broken, broken


def _misplace_key(raml: Raml) -> None:
    raml.endpoints['/a'].operations['get'].key_pos = Position(1, 1, 1, 4)


def _misplace_value(raml: Raml) -> None:
    get = raml.endpoints['/a'].operations['get']
    get.value_pos = Position(get.key_pos.line, 1, get.key_pos.line, 2)


def _misplace_child(raml: Raml) -> None:
    child = raml.endpoints['/a/b']
    child.key_pos = child.value_pos = Position(2, 1, 2, 3)


def _misplace_uses(raml: Raml) -> None:
    raml.entry_point.uses['lib'].key_pos = Position(2, 1, 2, 4)


def _misplace_title(raml: Raml) -> None:
    (item,) = raml.entry_point.documentation
    item.value_pos = Position(1, 1, 1, 2)


def _misplace_example(raml: Raml) -> None:
    raml.entry_point.types['T'].example.key_pos = Position(2, 1, 2, 6)


@pytest.mark.parametrize(
    ('move', 'rule'),
    [
        pytest.param(_misplace_key, 'its key reads', id='a key off its name'),
        pytest.param(_misplace_value, 'is misplaced', id='a value before its key'),
        pytest.param(_misplace_child, 'outside /a', id='a resource outside its parent'),
        pytest.param(_misplace_uses, 'uses lib', id='a uses entry off its alias'),
        pytest.param(_misplace_title, 'title lies outside it', id='a documentation title outside its item'),
        pytest.param(_misplace_example, 'example', id='an example off its key'),
    ],
)
def test_a_misplaced_entity_breaks_the_law(memory_workspace, move, rule):
    # The law's own check: each rule reports what breaks it.
    api = (
        '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ndocumentation:\n  - title: Home\n    content: c\n'
        'types:\n  T:\n    type: string\n    example: x\n/a:\n  get:\n    description: d\n  /b:\n'
    )
    root = memory_workspace({'api.raml': api, 'lib.raml': '#%RAML 1.0 Library\n'})
    raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_text=True))
    assert violations(raml) == []
    move(raml)
    assert any(rule in problem for problem in violations(raml)), violations(raml)
