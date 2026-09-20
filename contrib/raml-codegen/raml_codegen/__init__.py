"""Generate source code from a RAML 1.0 definition.

The input is the *effective tree* — what `fastraml tree` writes, and what
`docs/16-graph.md` § 11 specifies. By the time it exists, nine passes of RAML
have run: includes resolved, type expressions expanded, inheritance merged,
traits and resource types applied, security bound. What is left for a generator
is a choice of spellings in the target language, which is the whole of what this
package decides.

A target is a function from a tree to a set of files. `python` is the one that
ships; the registry is here so a second language is a module rather than a fork.
"""

from __future__ import annotations

from .reader import Tree, UnreadableTree
from .targets import TARGETS, Generated, Settings, generate, generate_from_path

__all__ = ['TARGETS', 'Generated', 'Settings', 'Tree', 'UnreadableTree', 'generate', 'generate_from_path']
