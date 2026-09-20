"""Language bindings for the ``fastraml tree`` contract.

Each language backend owns its renderer. The caller owns the destination, so a
backend can generate checked-in artifacts, temporary files, or standard output
without repository paths embedded in library code.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .python import main as _python_main
from .python import python
from .typescript import main as _typescript_main
from .typescript import typescript

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['main', 'python', 'typescript']

_BACKENDS = {'python': _python_main, 'typescript': _typescript_main}


def main(arguments: Sequence[str] | None = None) -> None:
    """Select a binding backend and forward its arguments."""
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args or args[0] not in _BACKENDS:
        choices = ', '.join(_BACKENDS)
        raise SystemExit(f'usage: python -m fastraml.views.bindings <{choices}> -o FILE')
    _BACKENDS[args[0]](args[1:])
