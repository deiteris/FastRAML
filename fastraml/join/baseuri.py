"""The common base URI and the endpoints created under it (docs/20 § 6).

Pure string and tree work: which inputs agree on the scheme and authority, the
longest run of path segments they share, and the trie of what is left, with
single-child chains written as one key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

__all__ = ['BaseUri', 'CreatedEndpoint', 'plan_created', 'split_base_uri', 'uri_variables']

#: One RFC 6570 expression; the operator and the variable list.
_EXPRESSION: Final = re.compile(r'\{([+#./;?&]?)([^{}]*)\}')
#: A variable's modifiers: `:3` prefix and `*` explode.
_MODIFIER: Final = re.compile(r'(?::\d+|\*)$')


@dataclass(frozen=True, slots=True)
class BaseUri:
    """A base URI split into what must match exactly and what may be shared."""

    #: `https://api.example.com`, or `''` for a URI with no authority.
    authority: str
    segments: tuple[str, ...]


def split_base_uri(text: str) -> BaseUri:
    """Split at the first `/` after `scheme://`; drop empty segments at either end."""
    scheme_end = text.find('://')
    start = 0 if scheme_end < 0 else scheme_end + 3
    slash = text.find('/', start)
    if scheme_end < 0:
        authority, path = '', text
    elif slash < 0:
        authority, path = text, ''
    else:
        authority, path = text[:slash], text[slash:]
    return BaseUri(authority, tuple(segment for segment in path.strip('/').split('/') if segment))


def uri_variables(text: str) -> list[str]:
    """The variable names in a URI template, in order, without operators or modifiers."""
    names: list[str] = []
    for match in _EXPRESSION.finditer(text):
        for raw in match.group(2).split(','):
            name = _MODIFIER.sub('', raw.strip())
            if name and name not in names:
                names.append(name)
    return names


def common_segments(uris: list[BaseUri]) -> tuple[str, ...]:
    """The longest run of leading segments every URI shares."""
    first = uris[0].segments
    length = min(len(uri.segments) for uri in uris)
    for index in range(length):
        if any(uri.segments[index] != first[index] for uri in uris[1:]):
            return first[:index]
    return first[:length]


@dataclass(slots=True, eq=False)
class _Trie:
    children: dict[str, _Trie] = field(default_factory=dict)
    #: Indices of the inputs whose remainder ends here.
    inputs: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CreatedEndpoint:
    """One created endpoint on an input's path, outermost first."""

    #: The key as written, `/orders` or `/{tenant}/orders`.
    key: str
    #: The full path from the root, for identity (docs/20 § 3.3).
    full: str


def plan_created(remainders: list[tuple[str, ...]]) -> list[list[CreatedEndpoint]]:
    """Per input, the created endpoints its resources go under; empty for none.

    Remainders form a trie by segment. A trie node with one child and no input
    of its own is merged into its child's key.
    """
    root = _Trie()
    for index, segments in enumerate(remainders):
        node = root
        for segment in segments:
            node = node.children.setdefault(segment, _Trie())
        node.inputs.append(index)

    plans: list[list[CreatedEndpoint]] = [[] for _ in remainders]

    def visit(node: _Trie, chain: list[CreatedEndpoint], full: str) -> None:
        for index in node.inputs:
            plans[index] = list(chain)
        for segment, child in node.children.items():
            key, target = '/' + segment, child
            # Collapse a chain of pass-through nodes into one key.
            while not target.inputs and len(target.children) == 1:
                next_segment, target = next(iter(target.children.items()))
                key += '/' + next_segment
            visit(target, [*chain, CreatedEndpoint(key, full + key)], full + key)

    # A pass-through at the root is not collapsed into the base URI: the common
    # path already took every segment all inputs share.
    visit(root, [], '')
    return plans
