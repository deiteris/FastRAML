"""RAML text from a `Node` tree (docs/20 § 7.1).

Each node becomes a PyYAML node and PyYAML's serializer writes it. The dumper
resolves plain scalars by the parser's YAML 1.2 rules, so a scalar is quoted, or
tagged, exactly when `compose` would otherwise read it back differently.
"""

from __future__ import annotations

from typing import Any, Final

import yaml

from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, plain_tag

__all__ = ['write_raml']

_STANDARD_TAG_PREFIX: Final = 'tag:yaml.org,2002:'
_STR: Final = _STANDARD_TAG_PREFIX + 'str'
#: Long enough that no line is folded: a folded plain scalar reads back the
#: same, but a diff of the output should not depend on the width.
_UNWRAPPED: Final = 1 << 20


class _Dumper(yaml.SafeDumper):
    """`SafeDumper` resolving implicit scalars as `compose` does."""

    def resolve(self, kind: Any, value: Any, implicit: Any) -> Any:
        if kind is yaml.ScalarNode:
            if implicit[0]:
                return _long(plain_tag(value))
            return _STR
        return super().resolve(kind, value, implicit)

    def choose_scalar_style(self) -> str:
        """Plain for an `!include` argument where YAML allows it.

        PyYAML writes a plain scalar only when its tag could be left out, so a
        tagged one is always quoted; `!include 'a.raml'` reads no better.
        """
        event = self.event
        if isinstance(event, yaml.ScalarEvent) and event.tag == TAG_INCLUDE and not event.style:
            if self.analysis is None:
                self.analysis = self.analyze_scalar(event.value)
            if self.analysis.allow_block_plain and not self.analysis.empty and not self.flow_level:
                return ''
        style: str = super().choose_scalar_style()
        return style


def _long(tag: str) -> str:
    return _STANDARD_TAG_PREFIX + tag[2:] if tag.startswith('!!') else tag


def _yaml_node(node: Node) -> yaml.Node:
    """A fresh PyYAML node per visit, so a node written twice gets no alias."""
    if node.kind is NodeKind.SCALAR:
        style = '|' if node.tag == TAG_STR and '\n' in node.value.rstrip('\n') else None
        return yaml.ScalarNode(_long(node.tag), node.value, style=style)
    if node.kind is NodeKind.MAPPING:
        content = node.content
        pairs = [(_yaml_node(content[i]), _yaml_node(content[i + 1])) for i in range(0, len(content), 2)]
        return yaml.MappingNode(_long(node.tag), pairs, flow_style=not pairs)
    items = [_yaml_node(item) for item in node.content]
    return yaml.SequenceNode(_long(node.tag), items, flow_style=not items)


def write_raml(root: Node, header: str = '#%RAML 1.0') -> str:
    """The document `root` as RAML text, with `header` as its first line."""
    body = yaml.serialize(
        _yaml_node(root),
        Dumper=_Dumper,
        allow_unicode=True,
        width=_UNWRAPPED,
        line_break='\n',
    )
    return f'{header}\n{body}'
