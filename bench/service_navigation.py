"""Sparse semantic requests across three edits, not an observed client trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bench.service_session import SessionInput
from bench.service_session import prepare as prepare_session
from fastraml.service import outline, queries
from fastraml.service.workspace import Workspace

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, eq=False)
class NavigationInput:
    session: SessionInput
    types: tuple[tuple[int, int], ...]
    data: tuple[tuple[int, int], ...]


def prepare(entry: Path) -> NavigationInput:
    session = prepare_session(entry)
    types = []
    data = []
    for line, raw in enumerate(session.focus_text.splitlines(), 1):
        if raw.startswith(('  Word', '  Name')):
            types.append((line, 3))
        elif raw.strip() == 'note: Facet note.':
            data.append((line, len(raw) - len(raw.lstrip()) + 1))
    pair_size = 2
    if len(types) < pair_size or not data:
        raise RuntimeError('navigation corpus lost its sparse type/data sites')
    # Three family pairs and three data sites, independent of input width.
    families = len(types) // pair_size
    chosen = (0, families // 2, families - 1)
    return NavigationInput(
        session,
        tuple(types[pair_size * family + member] for family in chosen for member in range(pair_size)),
        tuple(data[family] for family in chosen),
    )


def exercise(prepared: NavigationInput) -> tuple[Workspace, dict[str, int]]:
    session = prepared.session
    workspace = Workspace([session.folder])
    counts: dict[str, int] = dict.fromkeys(
        ('snapshots', 'types', 'supers', 'subs', 'definitions', 'outlines', 'symbols'), 0
    )
    for version, text in enumerate(session.versions, 1):
        workspace.change(session.root, text, version)
        workspace.collect()
        snapshot = workspace.snapshot(session.root)
        if snapshot.error is not None or snapshot.raml is None:
            raise RuntimeError('navigation corpus lost its valid snapshot')
        counts['snapshots'] += 1
        queries.diagnostics(snapshot, lint=False)
        snapshot.occurrences  # noqa: B018 - part of diagnostic/navigation readiness
        for line, column in prepared.types:
            item = queries.type_at(snapshot, session.root, line, column)
            if item is None:
                raise RuntimeError('navigation corpus lost a type preparation')
            counts['types'] += 1
            parents = queries.supertypes(snapshot, item)
            children = queries.subtypes(snapshot, item)
            if (item.name.startswith('Name') and not parents) or (item.name.startswith('Word') and not children):
                raise RuntimeError('navigation corpus lost an inheritance edge')
            counts['supers'] += 1
            counts['subs'] += 1
        for line, column in prepared.data:
            if not queries.definition(snapshot, session.root, line, column):
                raise RuntimeError('navigation corpus lost a typed-data definition')
            counts['definitions'] += 1
        for _ in range(2):
            if not outline.document_symbols(snapshot, session.root):
                raise RuntimeError('navigation corpus lost its outline')
            counts['outlines'] += 1
        for wanted in ('Word', 'MetadataLeaf'):
            if not queries.workspace_symbols([snapshot], wanted):
                raise RuntimeError('navigation corpus lost its workspace symbols')
            counts['symbols'] += 1
        del snapshot
    return workspace, counts
