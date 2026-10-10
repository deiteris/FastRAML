"""Representative editor requests across three versions, not a client trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.positions import Position
from fastraml.service import inlays, lenses, outline, queries
from fastraml.service.workspace import Workspace
from fastraml.uris import path_to_file_uri

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, eq=False)
class SessionInput:
    root: str
    folder: str
    focus: str
    focus_text: str
    versions: tuple[str, ...]
    probes: tuple[tuple[int, int], ...]


def prepare(entry: Path, *, focus: Path | None = None) -> SessionInput:
    """Prepare texts and sparse probes outside measurement."""
    target = entry if focus is None else focus
    text = entry.read_text(encoding='utf-8')
    focus_text = target.read_text(encoding='utf-8')
    candidates = [
        (line, len(raw) - len(raw.lstrip()) + 1)
        for line, raw in enumerate(focus_text.splitlines(), 1)
        if raw.lstrip().startswith(('minLength:', 'maxLength:', 'type:', 'description:'))
    ]
    if not candidates:
        raise RuntimeError('session corpus lost its sparse hover sites')
    probes = tuple(candidates[index] for index in (0, len(candidates) // 2, len(candidates) - 1))
    return SessionInput(
        path_to_file_uri(entry),
        path_to_file_uri(entry.parent),
        path_to_file_uri(target),
        focus_text,
        (text, text + '\n# edit 2\n', text + '\n# edit 3\n'),
        probes,
    )


def folding(workspace: Workspace, uri: str) -> list[tuple[int, int]]:
    """Use the branch's public structural-query interface."""
    if not hasattr(workspace, 'source'):
        return queries.folding_ranges(workspace.text(uri) or '', uri)
    source = workspace.source(uri)
    if hasattr(source, 'folding_ranges'):
        return source.folding_ranges()
    return getattr(queries, 'folding_ranges_of')(source)  # noqa: B009 - historical branch API


def selection(workspace: Workspace, uri: str, line: int, column: int) -> list[Position]:
    """Use the branch's public structural-query interface."""
    if not hasattr(workspace, 'source'):
        return queries.selection_ranges(workspace.text(uri) or '', uri, line, column)
    source = workspace.source(uri)
    if hasattr(source, 'selection_ranges'):
        return source.selection_ranges(line, column)
    return getattr(queries, 'selection_ranges_of')(source, line, column)  # noqa: B009 - historical branch API


def exercise(prepared: SessionInput, *, source_first: bool = False) -> tuple[Workspace, dict[str, int]]:
    """Keep the current workspace/caches, discarding each request's answer."""
    workspace = Workspace([prepared.folder])
    if prepared.focus != prepared.root:
        workspace.open(prepared.focus, prepared.focus_text, 1)
    counts: dict[str, int] = dict.fromkeys(
        ('snapshots', 'outlines', 'lenses', 'hints', 'folds', 'hovers', 'selections'), 0
    )
    viewport = Position(1, 1, 120, 1)
    for version, text in enumerate(prepared.versions, 1):
        workspace.change(prepared.root, text, version)
        workspace.collect()
        if source_first:
            counts['folds'] += len(folding(workspace, prepared.focus))
        snapshot = workspace.snapshot(prepared.root)
        if snapshot.error is not None or snapshot.raml is None:
            raise RuntimeError('session corpus lost its valid semantic snapshot')
        counts['snapshots'] += 1
        queries.diagnostics(snapshot, lint=False)
        snapshot.occurrences  # noqa: B018 - navigation index is part of the request mix
        counts['outlines'] += len(outline.document_symbols(snapshot, prepared.focus))
        queries.links(snapshot, prepared.focus)
        counts['lenses'] += len(lenses.code_lenses(snapshot, prepared.focus))
        counts['hints'] += len(inlays.inlay_hints(snapshot, prepared.focus, viewport))
        counts['folds'] += len(folding(workspace, prepared.focus))
        for line, column in prepared.probes:
            if queries.hover(snapshot, prepared.focus, line, column) is None:
                raise RuntimeError('session corpus lost a sparse hover answer')
            counts['hovers'] += 1
        # Warm requests in the same version; selection is user-triggered.
        counts['outlines'] += len(outline.document_symbols(snapshot, prepared.focus))
        counts['hints'] += len(inlays.inlay_hints(snapshot, prepared.focus, viewport))
        counts['folds'] += len(folding(workspace, prepared.focus))
        for index in range(8):
            line, column = prepared.probes[index % len(prepared.probes)]
            if not selection(workspace, prepared.focus, line, column):
                raise RuntimeError('session corpus lost a selection path')
            counts['selections'] += 1
        # Release the previous model before collect/build on the next edit.
        del snapshot
    if not all(counts.values()):
        raise RuntimeError('session corpus no longer reaches every request kind')
    return workspace, counts
