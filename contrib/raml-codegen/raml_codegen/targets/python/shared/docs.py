"""Turn a RAML `description:` into a docstring.

RAML says every `description:` is Markdown, so it may be a list, and the shape
of one has to survive being put into a docstring: collapsed to one line, a list
item such as `* price is the current price` lands mid-sentence.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from .plan import Argument, Field

__all__ = ['attribute', 'blocks', 'details', 'one_line', 'summary', 'wrapped']


def one_line(text: str | None) -> str:
    r"""A description, as one line.

    For somewhere that has room for one: an `Attributes:` entry, a `#:` comment.
    A whole description goes through `details`, which keeps its shape.

    A description may contain anything -- including `\"\"\"`, which would end the
    docstring it is being written into. Generated docstrings are raw, so a
    `pattern:` like `^\\d{13}$` reads as the author wrote it rather than as
    `^\\\\d{13}$`; a raw string cannot escape a quote, so the one sequence that
    could close one early is replaced rather than escaped.
    """
    return ' '.join((text or '').replace('"""', "'''").split())


#: A Markdown list item. An author writes lists in a `description:`, and the
#: shape of one has to survive being put into a docstring.
_BULLET: Final = re.compile(r'^([*+-]|\d+[.)])\s+')


@dataclass(frozen=True, slots=True)
class _Block:
    """One paragraph or one list item of a description."""

    kind: Literal['paragraph', 'item']
    text: str


def blocks(text: str | None) -> list[_Block]:
    """A description, as the paragraphs and list items the author wrote.

    A blank line ends a paragraph, a bullet starts an item, and an indented line
    under an item continues it.
    """
    found: list[_Block] = []
    current: list[str] = []
    kind: Literal['paragraph', 'item'] = 'paragraph'

    def flush() -> None:
        if current:
            found.append(_Block(kind, one_line(' '.join(current))))
            current.clear()

    for raw in (text or '').replace('\r\n', '\n').split('\n'):
        line = raw.strip()
        if not line:
            flush()
            kind = 'paragraph'
        elif _BULLET.match(line):
            flush()
            kind = 'item'
            current.append(line)
        elif kind == 'item' and raw[:1].isspace():
            current.append(line)
        else:
            if kind == 'item':
                flush()
                kind = 'paragraph'
            current.append(line)
    flush()
    return found


def summary(text: str | None) -> str:
    """The first sentence, for the line a docstring opens on."""
    found = blocks(text)
    if not found:
        return ''
    head, stop, _ = found[0].text.partition('. ')
    return f'{head}.' if stop else found[0].text


def details(text: str | None, indent: int = 4) -> str:
    """Everything after the first sentence, with its shape kept.

    Paragraphs are reflowed and separated by a blank line. A list item keeps its
    bullet, sits on a line of its own, and hangs its continuation under itself.
    """
    found = blocks(text)
    if not found:
        return ''
    _, stop, rest = found[0].text.partition('. ')
    remaining = [_Block('paragraph', rest)] if stop and rest.strip() else []
    remaining += found[1:]
    if not remaining:
        return ''

    written: list[str] = []
    for index, block in enumerate(remaining):
        # Consecutive items are one list, so nothing goes between them.
        if index and not (block.kind == 'item' and remaining[index - 1].kind == 'item'):
            written.append('')
        written.append(wrapped(block.text, indent + 2 if block.kind == 'item' else indent, first=indent))
    return '\n'.join(written)


def wrapped(text: str, indent: int, first: int = 0) -> str:
    """Wrapped, but never inside a word.

    `break_long_words` off, because the words here include a `pattern:` -- and
    `^(?:[A-Z]{2}-` on one line with `)?(?:AISLE` on the next reads as part of
    the pattern. A line that runs long is honest; a regex cut in half is not.
    """
    return textwrap.fill(
        text,
        width=100,
        initial_indent=' ' * first,
        subsequent_indent=' ' * indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def attribute(one: Field | Argument, indent: int = 8) -> str:
    """One `Attributes:` or `Args:` line, wrapped.

    A description and its constraints run to a few hundred characters on a type
    whose author had something to say, and generated code is still code.
    """
    head = f'{" " * indent}{one.name} ({one.annotation.spelling})'
    if not one.docs:
        return head
    return wrapped(f'{head}: {one_line(one.docs)}', indent + 4)
