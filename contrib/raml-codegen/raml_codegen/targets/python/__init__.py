"""The `python` target: a typed `httpx` client.

Two modules. `annotate.py` decides what one shape is in Python and how a value
of it crosses the JSON boundary; `emit.py` renders the plan through the
templates and works out each module's imports. The reading of the tree is in
`targets/shared/`, which every Python target does the same way.

None of it states a RAML rule — everything the language says already ran, nine
passes ago (docs/16 § 11.7).
"""

from __future__ import annotations

from .emit import generate_python

__all__ = ['generate_python']
