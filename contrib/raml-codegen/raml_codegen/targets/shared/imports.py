"""Work out one generated module's import block.

**The import block is computed, not formatted.** An earlier version ran
`ruff check --fix --select I,F401` over the finished files. That made the output
depend on the installed ruff version, so upgrading ruff changed the golden
record for reasons unrelated to this package. It also failed silently where ruff
was missing: `check --fix-only` and "no such module" both exit 1, so imports
came out unsorted and unused ones stayed. A module's imports follow from its own
plan, so this works them out and runs no subprocess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .annotate import Annotation
    from .plan import Model

__all__ = ['Imports', 'Needs', 'Source', 'imports_for']


@dataclass(frozen=True, slots=True)
class Source:
    """Where one importable name lives."""

    #: `None` when the name is itself a module, as `datetime` and `httpx` are.
    module: str | None
    third_party: bool


@dataclass(frozen=True, slots=True)
class Needs:
    """What one generated module's own body imports.

    Separate from what its *types* import, which the annotations already know.
    """

    #: Names to resolve through the target's `_FROM` table.
    names: tuple[str, ...] = ()
    #: Relative imports, written out: they vary with the module's position.
    lines: tuple[str, ...] = ()
    #: Names from the generated package's own runtime module.
    runtime: tuple[str, ...] = ()
    #: How this module reaches the runtime and the models beside it.
    types_import: str = '..types'
    model_prefix: str = '.'
    #: The model this module defines, which it must not import.
    own: str | None = None


@dataclass(frozen=True, slots=True)
class Imports:
    """The whole import section of one generated module, already ordered.

    Ordered the way isort would, in four blocks separated by a blank line:
    `__future__`, the standard library, third-party, then this package's own.
    A plain `import x` comes before a `from x import y` within a block, and the
    names in one `from` run constants, then classes, then functions.
    """

    block: str
    models: tuple[Model, ...]


def imports_for(
    annotations: Iterable[Annotation],
    by_name: Mapping[str, Model],
    needs: Needs,
    sources: Mapping[str, Source],
) -> Imports:
    """Everything one module imports.

    Two sources, and neither can see the other. The annotations say what the
    *types* need — `datetime` for a date, `Literal` for an enum, a model class.
    `needs` says what the module's own body needs, which no annotation knows: a
    dataclass needs `dataclass` and `Mapping`, a router needs `APIRouter`.
    """
    listed = tuple(annotations)
    wanted = {name for one in listed for name in one.models}
    # A recursive type names itself, and a module cannot import itself. The
    # self-reference is legal because every generated module opens with
    # `from __future__ import annotations`, and finite because of the marker.
    wanted.discard(needs.own or '')
    models = tuple(by_name[name] for name in sorted(wanted) if name in by_name)

    standard: list[str] = []
    third_party: list[str] = []
    by_module: dict[tuple[str, bool], list[str]] = {}
    for name in {*needs.names, *(name for one in listed for name in one.imports)}:
        source = sources[name]
        if source.module is None:
            (third_party if source.third_party else standard).append(f'import {name}')
        else:
            by_module.setdefault((source.module, source.third_party), []).append(name)
    for (module, is_third_party), names in by_module.items():
        line = f'from {module} import {", ".join(sorted(names, key=_by_kind))}'
        (third_party if is_third_party else standard).append(line)

    own = list(needs.lines)
    from_runtime = sorted({*needs.runtime, *(name for one in listed for name in one.runtime)}, key=_by_kind)
    if from_runtime:
        own.append(f'from {needs.types_import} import {", ".join(from_runtime)}')
    own += [f'from {needs.model_prefix}{model.module} import {model.name}' for model in models]

    blocks = [
        ['from __future__ import annotations'],
        sorted(standard, key=_import_order),
        sorted(third_party, key=_import_order),
        sorted(own, key=_import_order),
    ]
    return Imports(block='\n\n'.join('\n'.join(block) for block in blocks if block), models=models)


def _by_kind(name: str) -> tuple[int, str]:
    """Constants, then classes, then functions — isort's `order-by-type`."""
    if name.isupper():
        return (0, name)
    return (1, name) if name[:1].isupper() else (2, name)


def _import_order(line: str) -> tuple[int, str]:
    """`import x` before `from x import y`, then by module, most dots first."""
    if line.startswith('import '):
        return (0, line.removeprefix('import '))
    return (1, line.split(' ', 2)[1])
