"""Small retained-source lookups for syntax-sensitive lint rules."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastraml.yamlnode import NodeKind, pairs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.registry import Raml
    from fastraml.views.lint.engine import Finding
    from fastraml.yamlnode import Node

__all__ = ['SuppressionIndex', 'declaration_nodes', 'mapping_value']

_SUPPRESSION = re.compile(
    r'#[^\S\r\n]*(?i:fastraml:[^\S\r\n]*ignore)[^\S\r\n]+'
    r'([a-zA-Z0-9*_.-]+(?:[^\S\r\n]*,[^\S\r\n]*[a-zA-Z0-9*_.-]+)*)[^\S\r\n]*\r?'
)
_NEWLINE = re.compile('\n')


class SuppressionIndex:
    """Retained source indexed by line start for preceding-comment lookups."""

    __slots__ = ('_lines', '_sources')

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._sources = sources
        self._lines = {
            location: (0, *(match.end() for match in _NEWLINE.finditer(text))) for location, text in sources.items()
        }

    def suppresses(self, finding: Finding) -> bool:
        """Whether a directive immediately above `finding` names its rule."""
        if not finding.position.is_known or finding.position.line < 2:  # noqa: PLR2004 - line 1 has no predecessor
            return False
        starts = self._lines.get(finding.location)
        if starts is None or finding.position.line > len(starts):
            return False
        text = self._sources[finding.location]
        line = text[starts[finding.position.line - 2] : starts[finding.position.line - 1] - 1].lstrip(' \t')
        match = _SUPPRESSION.fullmatch(line)
        if match is None:
            return False
        rules = match.group(1).replace(' ', '').replace('\t', '').split(',')
        return '*' in rules or finding.rule in rules


def declaration_nodes(raml: Raml, entity_id: int) -> tuple[Node | None, Node] | None:
    """The key and value authored for a model entity."""
    source_info = raml.source_info
    if source_info is None:
        return None
    return source_info.get(entity_id)


def mapping_value(node: Node, name: str) -> tuple[Node, Node] | None:
    """A mapping entry by key, preserving both source nodes."""
    if node.kind is not NodeKind.MAPPING:
        return None
    return next(((key, value) for key, value in pairs(node) if key.value == name), None)
