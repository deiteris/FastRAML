"""Reading a RAML regular expression's structure, for the rules that judge one.

The pattern rules need an expression's structure, not its behaviour: where its
anchors sit, and whether a quantified group holds another unbounded quantifier.
One tokenizer serves them all. A run of literals is one token, so a loop over
tokens steps over structure rather than over characters (docs/12 § 2).
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ['fully_anchored', 'nested_quantifier']

_TOKEN: Final = re.compile(
    r'\\.'  # an escape
    r'|\[\^?\]?(?:\\.|[^\]\\])*\]'  # a character class
    r'|\{\d+(?:,\d*)?\}'  # a counted quantifier
    r'|\((?:\?(?:[:=!>]|<[=!]|P?<\w+>|[aiLmsux-]*:))?'  # a group opener, prefix included
    r'|[)|^$*+?]'  # structure
    r'|(?:[^\\\[()|^$*+?{](?![*+?{]))+'  # a run of literals no quantifier applies to
    r'|.',  # one literal, which a quantifier may follow
    re.DOTALL,
)
#: Global inline flags at the very start. `m` makes `^` and `$` match at every
#: line, so an expression carrying it is not anchored to the whole value.
_LEADING_FLAGS: Final = re.compile(r'\(\?([aiLmsux]+)\)')
_START_ANCHORS: Final = frozenset({'^', r'\A'})
_END_ANCHORS: Final = frozenset({'$', r'\Z', r'\z'})


def fully_anchored(pattern: str) -> bool:
    r"""Every top-level alternative starts at a start anchor and ends at an end anchor.

    `^a|b$` anchors each branch at one end only, `(?m)^a$` anchors to a line,
    and `^a\$` ends at a literal dollar; none is anchored here. A group
    wrapping the anchors, `(^a$)`, is also reported: rare, and cheap to rewrite
    as `^(a)$`.
    """
    flags = _LEADING_FLAGS.match(pattern)
    if flags is not None:
        if 'm' in flags.group(1):
            return False
        pattern = pattern[flags.end() :]
    branches: list[list[str]] = [[]]
    depth = 0
    for piece in _TOKEN.findall(pattern):
        if piece.startswith('('):
            depth += 1
        elif piece == ')':
            depth -= 1
        elif piece == '|' and depth == 0:
            branches.append([])
            continue
        branches[-1].append(piece)
    return all(branch and branch[0] in _START_ANCHORS and branch[-1] in _END_ANCHORS for branch in branches)


_QUANTIFIERS: Final = frozenset({'*', '+', '?'})


def _is_quantifier(piece: str) -> bool:
    return piece in _QUANTIFIERS or (piece.startswith('{') and piece.endswith('}'))


def _unbounded(piece: str) -> bool:
    return piece in {'*', '+'} or (piece.startswith('{') and piece.endswith(',}'))


class _Group:
    """What one group's direct content can do, for `nested_quantifier`."""

    __slots__ = ('mandatory', 'unbounded')

    def __init__(self) -> None:
        #: Something inside must match exactly once: a separator, such as `-` in `(-[a-z]+)*`.
        self.mandatory = False
        #: Something inside repeats without limit.
        self.unbounded = False

    def settle(self, atom: _Group | None) -> None:
        """An unquantified atom: a group passes on what it holds, anything else must match."""
        if atom is None:
            self.mandatory = True
        else:
            self.mandatory |= atom.mandatory
            self.unbounded |= atom.unbounded


def nested_quantifier(pattern: str) -> bool:
    r"""An unboundedly repeated group whose content repeats and has no separator: `(a+)+`, `(\w+\s?)*`.

    A separator that must match once per repetition, as in `(-[a-z]+)*`, fixes
    where each repetition starts, so the group is not reported. That misses a
    separator the repeated part can also match; this is a heuristic.
    """
    stack = [_Group()]
    pending = False  # an atom is waiting to learn whether a quantifier follows
    atom: _Group | None = None  # that atom, when it is a group
    for piece in _TOKEN.findall(pattern):
        frame = stack[-1]
        if _is_quantifier(piece):
            if pending and _unbounded(piece):
                if atom is not None and atom.unbounded and not atom.mandatory:
                    return True
                frame.unbounded = True
            elif pending and atom is not None:
                frame.unbounded |= atom.unbounded
            pending, atom = False, None
            continue
        if pending:
            frame.settle(atom)
            pending, atom = False, None
        if piece.startswith('('):
            stack.append(_Group())
        elif piece == ')':
            if len(stack) > 1:
                atom, pending = stack.pop(), True
        elif piece not in {'|', '^', '$'}:
            pending = True
    return False
