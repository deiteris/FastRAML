"""On-demand effective-type views over the existing RAML renderer (docs/21 § 4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastraml.records import identity_record
from fastraml.types.base import BaseShape
from fastraml.views.render import render

if TYPE_CHECKING:
    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.service.index import SemanticIndex
    from fastraml.service.workspace import Snapshot


@identity_record
class Lens:
    name: str
    span: Position


class EffectiveViews:
    """A lazy snapshot index; rendering is reserved for an explicit request."""

    __slots__ = ('by_site', 'by_uri')

    def __init__(self, raml: Raml, semantic: SemanticIndex) -> None:
        self.by_uri: dict[str, list[Lens]] = {}
        self.by_site: dict[tuple[str, int, int, str], BaseShape] = {}
        if not raml.unwrapped:
            return
        # The snapshot's one enumeration, shared with outline, hover and symbols.
        for _, name, base in semantic.declarations:
            if not isinstance(base, BaseShape) or base.id in raml.broken:
                continue
            span = base.key_pos if base.key_pos.is_known else base.value_pos
            if not span.is_known:
                continue
            self.by_uri.setdefault(base.location, []).append(Lens(name, span))
            self.by_site[base.location, span.line, span.column, name] = base


def code_lenses(snapshot: Snapshot, uri: str) -> list[Lens]:
    views = snapshot.effective_views
    return [] if views is None else views.by_uri.get(uri, [])


def effective_type(snapshot: Snapshot, uri: str, line: int, column: int, *, name: str) -> str | None:
    """Render a current declaration selected by source and name, never a stale ID."""
    views = snapshot.effective_views
    if views is None:
        return None
    base = views.by_site.get((uri, line, column, name))
    return (
        None
        if base is None
        else '#%RAML 1.0 DataType\n'
        + '\n'.join(render(base, depth=None, root=snapshot.root.rpartition('/')[0] + '/'))
        + '\n'
    )
