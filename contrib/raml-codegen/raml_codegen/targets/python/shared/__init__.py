"""What every Python target does the same way.

A target decides spellings. Everything before a spelling — descending the tree,
following a link, stopping at a recursion marker, claiming a name for an
anonymous shape, working out which supertype a body really is — is the same
question whatever the answer is written in, so it lives here once.

`annotate.py` holds the traversal and leaves each kind's spelling to a hook;
`plan.py` reads the whole tree into one `Package`; `docs.py` turns a RAML
`description:` into a docstring; `imports.py` works out a module's import block.
"""

from __future__ import annotations

from .annotate import IDENTITY, Annotation, Annotator, Member, fill
from .imports import Imports, Needs, Source, imports_for
from .plan import Argument, Body, Case, Endpoint, Field, Model, Package, Scheme, plan

__all__ = [
    'IDENTITY',
    'Annotation',
    'Annotator',
    'Argument',
    'Body',
    'Case',
    'Endpoint',
    'Field',
    'Imports',
    'Member',
    'Model',
    'Needs',
    'Package',
    'Scheme',
    'Source',
    'fill',
    'imports_for',
    'plan',
]
