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

#: A whole line holding only a directive, after leading indentation.
_SUPPRESSION = re.compile(
    r'^[ \t]*#[^\S\r\n]*(?i:fastraml:[^\S\r\n]*ignore)[^\S\r\n]+'
    r'([a-zA-Z0-9*_.-]+(?:[^\S\r\n]*,[^\S\r\n]*[a-zA-Z0-9*_.-]+)*)[^\S\r\n]*\r?$',
    re.MULTILINE,
)


class SuppressionIndex:
    """The directives in retained source, keyed by the line each one covers.

    One regex scan per source finds every directive up front, so checking a
    finding is a dict lookup. A document has thousands of findings and, at
    most, a handful of directives.
    """

    __slots__ = ('_covered',)

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._covered: dict[tuple[str, int], frozenset[str]] = {}
        for location, text in sources.items():
            line, scanned = 1, 0
            for match in _SUPPRESSION.finditer(text):
                line += text.count('\n', scanned, match.start())
                scanned = match.start()
                rules = frozenset(match.group(1).replace(' ', '').replace('\t', '').split(','))
                # The directive covers the line after its own.
                self._covered[location, line + 1] = rules

    def suppresses(self, finding: Finding) -> bool:
        """Whether a directive immediately above `finding` names its rule."""
        if not self._covered or not finding.position.is_known:
            return False
        rules = self._covered.get((finding.location, finding.position.line))
        return rules is not None and ('*' in rules or finding.rule in rules)


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
