"""Generate source code from a RAML 1.0 definition.

The input is the **effective tree**: the JSON that `fastraml tree` writes, and
that `docs/16-graph.md` § 6 specifies. Not RAML, and not a path to RAML.

**This package does not depend on the parser**, the same way `viewer/` does not.
It reads a published wire format, so generating needs the JSON and nothing else
— no `fastraml` installed, no RAML files on disk, no matching versions. Produce
the tree wherever the RAML lives, and possibly long before:

    fastraml tree api.raml > api.json      # once, wherever the parser is
    raml-codegen python api.json -o out/   # here, with neither

By the time the tree exists, the parser has run: includes resolved,
type expressions expanded, inheritance merged, traits and resource types
applied, security bound. What is left for a generator is a choice of spellings
in the target language, which is the whole of what this package decides.

A target is a function from a tree to a set of files. `python` is the one that
ships; the registry is here so that a second language is a module rather than a
fork.
"""

from __future__ import annotations

from .reader import Tree, UnreadableTree
from .targets import TARGETS, Generated, Settings, generate

__all__ = ['TARGETS', 'Generated', 'Settings', 'Tree', 'UnreadableTree', 'generate']
