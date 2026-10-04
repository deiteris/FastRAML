"""Reading a RAML regular expression's structure, for the rules that judge one.

The pattern rules need an expression's structure, not its behaviour: where its
anchors sit, and whether a quantified group holds another unbounded quantifier.
One tokenizer serves them all. A run of literals is one token, so a loop over
tokens steps over structure rather than over characters (docs/12 § 2).
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ['REGEX_TOKEN', 'fully_anchored']

REGEX_TOKEN: Final = re.compile(
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
    for piece in REGEX_TOKEN.findall(pattern):
        if piece.startswith('('):
            depth += 1
        elif piece == ')':
            depth -= 1
        elif piece == '|' and depth == 0:
            branches.append([])
            continue
        branches[-1].append(piece)
    return all(branch and branch[0] in _START_ANCHORS and branch[-1] in _END_ANCHORS for branch in branches)
