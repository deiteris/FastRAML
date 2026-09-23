"""Where a backend's rendered text goes: a file, or standard output."""

from __future__ import annotations

import pathlib
import sys

__all__ = ['write_rendered']


def write_rendered(destination: str, rendered: str) -> None:
    """Write `rendered` to `destination`, or to standard output for `-`."""
    if destination == '-':
        sys.stdout.write(rendered)
        return
    path = pathlib.Path(destination)
    path.write_text(rendered, encoding='utf-8')
    sys.stdout.write(f'wrote {path.resolve()}\n')
