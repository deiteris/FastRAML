"""The `python` target: a typed `httpx` client.

Three modules, in the order the work happens. `annotate.py` decides what one
shape is in Python; `plan.py` reads the whole tree into the questions a template
asks; `emit.py` renders and canonicalises. None of them states a RAML rule —
everything the language says already ran, nine passes ago (docs/16 § 11.7).
"""

from __future__ import annotations

from .emit import generate_python

__all__ = ['generate_python']
