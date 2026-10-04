"""The partial-model contract, over one-mistake mutations of valid documents.

docs/13 § 1 states what a model `parse_lenient` returns may be relied on for.
A unit test checks it on a mistake someone chose. This module makes the
mistakes mechanically, one per parse: it deletes a line, indents one, adds an
unknown key, corrupts a scalar, or misspells a name, in a valid TCK document
or a fixture file. On every result it checks the whole contract:

- nothing escapes but a fatal failure located at the entry (docs/11 § 2);
- `completed` and `stopped_at` agree with each other and with the error;
- each fragment's declaration maps are the registry's;
- every mark in `Raml.broken` names an entity the model holds;
- no registered shape lacks a kind, and no marked one is flagged unwrapped;
- an unmarked shape is not unknown once P7 finished (I5), and one flagged
  unwrapped was merged from flattened parents and aliases a flattened
  referent;
- a walk of the shapes' containment terminates without a visited set;
- the occurrence index builds, and every use in it meets one definition
  (docs/16 § 9);
- on an unwrapped model, the views run and the tree obeys the traversal law
  (docs/16 § 6.1).

The TCK half is exhaustive: a sample of one site per mutation kind passed
while the full run found three defects. The fixtures are larger, so every
seventh mutation is parsed; seven is prime to the five kinds, so each kind is
reached throughout each file.

It is an exploration, not part of the gate: it runs only with `--mutations`
(`conftest.py`), and each defect it finds is pinned by a unit test of its
own.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fastraml import APIFragment, ParseOptions, RamlError, Stage, parse_lenient
from fastraml.loaders import SafeFileLoader
from fastraml.parser.entry import _FATAL
from fastraml.registry import Raml
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape, UnknownShape
from fastraml.uris import path_to_file_uri
from fastraml.views.graph import build_graph
from fastraml.views.lint.engine import Linter
from fastraml.views.lint.rules import builtin_registry
from fastraml.views.occurrences import build_occurrences
from fastraml.views.openapi import to_openapi
from fastraml.views.tree import build_tree
from fastraml.yamlnode import Node
from tests.tck.conftest import case_directory, collect_fixtures, fixture_id, tck_root
from tests.unit.test_consumer_traversal import expand
from tests.unit.test_occurrence_law import round_trip

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.types.base import BaseShape

#: `key: value`, or `- key: value` opening a mapping in a sequence.
_KEY = re.compile(r'^(\s*)(- )?([^\s:#][^:#]*):(\s+)(\S.*)$')
_NAME = re.compile(r'^[A-Za-z_][\w.-]*$')

#: Every stage runs: the options ask for unwrap and validation.
_STAGES = list(Stage)
_OPTIONS = {'unwrap': True, 'validate': True, 'retain_source': True}

#: Deeper than any containment a valid document builds; a walk that gets here
#: has met a cycle nothing marked.
_RUNAWAY = 100

_FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures'
_FIXTURE_STRIDE = 7

#: Built once: rebuilding the rule registry for every parse cost as much as
#: the lint run itself.
_LINTER = Linter(builtin_registry())


def mutations(text: str) -> Iterator[tuple[str, str]]:
    """Every one-mistake variant of `text`, named by mistake and line.

    The first line is kept, so a RAML header survives.
    """
    lines = text.split('\n')

    def replaced(index: int, *new: str) -> str:
        return '\n'.join([*lines[:index], *new, *lines[index + 1 :]])

    for index, line in enumerate(lines):
        if index == 0 or not line.strip():
            continue
        where = f'line {index + 1}'
        yield f'delete {where}', replaced(index)
        yield f'indent {where}', replaced(index, ' ' + line)
        match = _KEY.match(line)
        if match is None:
            continue
        indent, dash, key, gap, value = match.groups()
        sibling = indent + ('  ' if dash else '')
        yield f'unknown key after {where}', replaced(index, line, f'{sibling}zzUnknown: 1')
        corrupt = 'seven' if value[:1].isdigit() else '7'
        yield f'corrupt {where}', replaced(index, f'{indent}{dash or ""}{key}:{gap}{corrupt}')
        if _NAME.match(value):
            yield f'misspell {where}', replaced(index, line.rstrip() + 'Zz')


class _Overlay:
    """The workspace as it is on disk, but for one file, as an editor's buffer."""

    __slots__ = ('_data', '_inner', '_uri')

    def __init__(self, inner: SafeFileLoader, uri: str, text: str) -> None:
        self._inner = inner
        self._uri = uri
        self._data = text.encode()

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        return self._data if uri == self._uri else self._inner.load(uri, max_bytes=max_bytes)


class Corpus:
    """Parses each mutation and collects every breach of the contract."""

    def __init__(self) -> None:
        self.offenders: list[str] = []
        self.tally: Counter[str] = Counter()

    def run(self, entry: Path, workspace: Path, mutated: Path, variants: Iterator[tuple[str, str]], label: str) -> None:
        """Parse `entry` with `mutated` replaced by each variant's text, and check each result."""
        uri = path_to_file_uri(mutated)
        loader = SafeFileLoader(workspace)
        for name, text in variants:
            self._one(entry, str(workspace), _Overlay(loader, uri, text), f'{label}: {name}')

    def _one(self, entry: Path, workspace: str, loader: _Overlay, label: str) -> None:
        options = ParseOptions(workspace_root=workspace, file_loader=loader, **_OPTIONS)
        self.tally['parsed'] += 1
        try:
            raml, error = parse_lenient(entry, options)
        except RamlError as err:
            if err.head.message in _FATAL and err.head.location == path_to_file_uri(entry):
                self.tally['fatal'] += 1
                return
            self.offenders.append(f'{label}: raised {err.head.message!r} at {err.head.location}')
            return
        except Exception as err:
            self.offenders.append(f'{label}: raised {type(err).__name__}: {err}')
            return
        self.offenders += [f'{label}: {problem}' for problem in self._check(raml, error)]

    def _check(self, raml: Raml, error: RamlError | None) -> list[str]:
        problems = _stages(raml, error) + _registries(raml) + _marks(raml) + _shapes(raml) + _occurrences(raml)
        if raml.stopped_at is not None:
            self.tally[f'stopped at {raml.stopped_at.value}'] += 1
        if raml.broken:
            self.tally['marked'] += 1
        if Stage.UNWRAPPED in raml.completed:
            self.tally['views'] += 1
            problems += _views(raml)
        return problems


def _stages(raml: Raml, error: RamlError | None) -> list[str]:
    if error is None:
        if raml.stopped_at is not None or raml.broken or raml.completed != _STAGES:
            return [f'clean, but stopped at {raml.stopped_at}, {len(raml.broken)} marks, completed {raml.completed}']
        return []
    if raml.stopped_at is None:
        return ['an error, but nothing stopped']
    expected = _STAGES[: _STAGES.index(raml.stopped_at)]
    if raml.completed != expected:
        return [f'stopped at {raml.stopped_at}, but completed {raml.completed}']
    return []


def _registries(raml: Raml) -> list[str]:
    """Each fragment's declaration maps are the registry's, entry for entry."""
    problems = []
    for location, fragment in raml.fragments.items():
        for attribute, registry in (('types', raml.fragment_types), ('annotation_types', raml.fragment_annotations)):
            declared = getattr(fragment, attribute, None)
            if declared is None:
                continue
            held = registry.get(location, {})
            if {name: id(base) for name, base in declared.items()} != {name: id(base) for name, base in held.items()}:
                problems.append(f'{attribute} of {location}: fragment {list(declared)}, registry {list(held)}')
    return problems


def _marks(raml: Raml) -> list[str]:
    """Every mark names an entity reachable from what a consumer reads."""
    if not raml.broken:
        return []
    held = _reachable_ids(raml)
    return [
        f'mark on {entity} is on nothing the model holds: {raml.broken[entity].head.message!r}'
        for entity in raml.broken
        if entity not in held
    ]


def _reachable_ids(raml: Raml) -> set[int]:
    """The id of every model object reachable through public fields.

    A union's declarations written beside `type: A | B` are held privately
    until P9 hands them out, and read through `member_declarations`, so that
    one private field is followed too.
    """
    stack: list[object] = [
        *raml.fragments.values(),
        *raml.endpoints.values(),
        *raml.domain_extensions,
        *raml.global_secured_by,
    ]
    seen: set[int] = set()
    ids: set[int] = set()
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, (str, int, float, Node, Raml)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            stack += item.values()
        elif isinstance(item, (list, tuple)):
            stack += item
        elif type(item).__module__.startswith('fastraml.'):
            ident = getattr(item, 'id', None)
            if isinstance(ident, int):
                ids.add(ident)
            stack += [
                getattr(item, slot, None)
                for cls in type(item).__mro__
                for slot in getattr(cls, '__slots__', ())
                if not slot.startswith('_') or slot == '_member_declarations'
            ]
    return ids


def _shapes(raml: Raml) -> list[str]:
    problems = []
    resolved = Stage.RESOLVED in raml.completed
    for base in raml.shapes:
        if base.shape is None:
            problems.append(f'shape {base.id} ({base.name!r}) has no kind')
        if base.id in raml.broken:
            if base._unwrapped:
                problems.append(f'marked shape {base.id} ({base.name!r}) is flagged unwrapped')
        else:
            problems += _unmarked(base, resolved=resolved)
        try:
            _contained(base, 0)
        except RecursionError:
            problems.append(f'walking the containment of {base.id} ({base.name!r}) does not terminate')
    return problems


def _unmarked(base: BaseShape, *, resolved: bool) -> list[str]:
    """An unmarked shape keeps the invariants of the stages that finished.

    Once P7 finished, it has a kind (I5). Flagged unwrapped, it was merged
    from flattened parents and aliases a flattened referent; one merged from
    a parent whose own merge failed is a broken shape passed off as sound.
    """
    problems = []
    if resolved and isinstance(base.shape, UnknownShape):
        problems.append(f'unmarked shape {base.id} ({base.name!r}) is unknown after P7')
    if base._unwrapped:
        sources = [*base.inherits, *([base.alias] if base.alias is not None else [])]
        problems += [
            f'unmarked shape {base.id} ({base.name!r}) is flagged unwrapped over unflattened {source.id}'
            for source in sources
            if not source._unwrapped
        ]
    return problems


def _contained(base: BaseShape, depth: int) -> None:
    """Descend containment, stopping at a recursion marker, with no visited set."""
    if depth > _RUNAWAY:
        raise RecursionError
    shape = base.shape
    if isinstance(shape, RecursiveShape):
        return
    if isinstance(shape, ArrayShape) and shape.items is not None:
        _contained(shape.items, depth + 1)
    elif isinstance(shape, UnionShape):
        for member in shape.any_of or ():
            _contained(member, depth + 1)
    elif isinstance(shape, ObjectShape):
        for prop in (*(shape.properties or {}).values(), *(shape.pattern_properties or {}).values()):
            _contained(prop.base, depth + 1)


def _occurrences(raml: Raml) -> list[str]:
    """The occurrence index builds at any stage, and each use meets one definition."""
    try:
        return round_trip(raml, build_occurrences(raml))
    except Exception as err:
        return [f'the occurrence index raised {type(err).__name__}: {err}']


def _views(raml: Raml) -> list[str]:
    """The views that need an unwrapped model run on one, and the tree is walkable."""
    try:
        expand(build_tree(raml))
        _LINTER.run(raml, graph=build_graph(raml))
        if isinstance(raml.entry_point, APIFragment):
            to_openapi(raml)
    except Exception as err:
        return [f'a view raised {type(err).__name__}: {err}']
    return []


def _report(corpus: Corpus) -> str:
    return f'{len(corpus.offenders)} breaches of docs/13 § 1:\n' + '\n'.join(corpus.offenders[:25])


@pytest.mark.tck
def test_every_mutation_of_a_valid_tck_document_keeps_the_contract():
    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
    corpus = Corpus()
    for path in collect_fixtures('valid'):
        workspace = case_directory(root, path)
        text = path.read_bytes().decode('utf-8-sig')
        corpus.run(path, workspace, path, mutations(text), fixture_id(root, path))
    assert not corpus.offenders, _report(corpus)
    # Not vacuous: every stage was reached and stopped at, marks were made,
    # and the views ran.
    assert {f'stopped at {stage.value}' for stage in Stage} <= set(corpus.tally), corpus.tally
    assert corpus.tally['marked'] > 0, corpus.tally
    assert corpus.tally['views'] > 0, corpus.tally


def test_every_seventh_mutation_of_the_fixtures_keeps_the_contract():
    entry = _FIXTURES / 'sample' / 'api.raml'
    corpus = Corpus()
    for path in sorted(_FIXTURES.rglob('*')):
        if path.suffix not in {'.raml', '.json'}:
            continue
        text = path.read_bytes().decode('utf-8-sig')
        variants = islice(mutations(text), 0, None, _FIXTURE_STRIDE)
        corpus.run(entry, _FIXTURES, path, variants, path.relative_to(_FIXTURES).as_posix())
    assert not corpus.offenders, _report(corpus)
    assert corpus.tally['marked'] > 0, corpus.tally
    assert corpus.tally['views'] > 0, corpus.tally
