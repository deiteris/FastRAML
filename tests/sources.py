"""The package's source files and the modules they import, for the guard tests.

Paths are anchored at the installed package, not the working directory: a guard
that globbed `fastraml/` relative to the cwd found nothing from anywhere else
and passed vacuously.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import fastraml

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    'MODEL',
    'NOT_MODEL',
    'PACKAGE',
    'Import',
    'edges',
    'imports',
    'module_name',
    'offenders',
    'parse',
    'sources',
    'within',
]

#: The `fastraml` package directory.
PACKAGE = Path(fastraml.__file__).parent

#: The model, as roots for `sources`: the passes, what they build, and the
#: support modules underneath both (docs/02 § 2). The composition roots,
#: `cli/` and `service/`, are absent: they are the callers allowed to see
#: both the model and the views.
MODEL = (
    'fastraml/parser',
    'fastraml/types',
    'fastraml/registry.py',
    'fastraml/nodes.py',
    'fastraml/datanode.py',
    'fastraml/yamlnode.py',
)

#: The packages, dotted, that consume the model rather than form it: the composition
#: roots and what they ship. Not `MODEL`'s complement: the support modules
#: (`errors`, `loaders`, `positions`, `uris`, ...) are in neither. They define
#: model classes, so the `__slots__` rule covers them, but `loaders` is the
#: I/O boundary, so the rules on what the passes import and read do not.
NOT_MODEL = ('fastraml.views', 'fastraml.service', 'fastraml.join', 'fastraml.cli', 'fastraml.skilldata')


@dataclass(frozen=True, slots=True)
class Import:
    line: int
    #: The dotted name imported. `from X import name` yields both `X` and
    #: `X.name`, since `name` may be a submodule: `from fastraml import views`.
    module: str
    #: Inside a function body: runs at call time, not at import.
    deferred: bool
    #: Under `if TYPE_CHECKING:`: never runs.
    type_checking: bool


def sources(*roots: str) -> list[Path]:
    """The `.py` files under each of `roots`, given relative to the package's
    parent (`'fastraml/views'`, `'fastraml/registry.py'`).
    """
    out: list[Path] = []
    for root in roots:
        path = PACKAGE.parent / root
        assert path.exists(), path
        out.extend(sorted(path.rglob('*.py')) if path.is_dir() else [path])
    return out


def module_name(path: Path) -> str:
    """`path`'s dotted module name: `fastraml/views/lint/__init__.py` is
    `fastraml.views.lint`.
    """
    parts = path.relative_to(PACKAGE.parent).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def within(module: str, *packages: str) -> bool:
    """Whether dotted `module` is one of `packages` or lies below one."""
    return any(module == package or module.startswith(f'{package}.') for package in packages)


def edges(*roots: str, runtime: bool = False) -> list[tuple[str, Import]]:
    """(importer, import) for every import in a module under `roots`; with
    `runtime`, only those that run (not under `if TYPE_CHECKING:`).
    """
    return [
        (module_name(path), found)
        for path in sources(*roots)
        for found in imports(path)
        if not (runtime and found.type_checking)
    ]


def offenders(roots: Iterable[str], *banned: str, allowed: tuple[str, ...] = (), runtime: bool = False) -> list[str]:
    """`importer:line imports module` for each import, from a module under
    `roots` but not within `allowed`, of a module within one of `banned`.
    """
    return [
        f'{importer}:{found.line} imports {found.module}'
        for importer, found in edges(*roots, runtime=runtime)
        if within(found.module, *banned) and not within(importer, *allowed)
    ]


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'))


def imports(source: str | Path, module: str | None = None, *, is_package: bool = False) -> list[Import]:
    """Every module `source` imports, with relative imports resolved.

    `source` is a file, or source text naming the module it stands for as
    `module` (and whether that module is a package's `__init__`).
    """
    if isinstance(source, Path):
        module, is_package = module_name(source), source.name == '__init__.py'
        tree = parse(source)
    else:
        tree = ast.parse(source)
    assert module is not None
    package = module if is_package else module.rpartition('.')[0]
    found: list[Import] = []
    _collect(tree.body, package, found, deferred=False, type_checking=False)
    return found


def _collect(body: list[ast.stmt], package: str, found: list[Import], *, deferred: bool, type_checking: bool) -> None:
    for node in body:
        if isinstance(node, ast.Import):
            found.extend(Import(node.lineno, alias.name, deferred, type_checking) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolved(node, package)
            found.append(Import(node.lineno, base, deferred, type_checking))
            found.extend(
                Import(node.lineno, f'{base}.{alias.name}', deferred, type_checking)
                for alias in node.names
                if alias.name != '*'
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect(node.body, package, found, deferred=True, type_checking=type_checking)
        elif isinstance(node, ast.If) and _is_type_checking(node.test):
            _collect(node.body, package, found, deferred=deferred, type_checking=True)
            _collect(node.orelse, package, found, deferred=deferred, type_checking=type_checking)
        else:
            # A class body runs at import; so do `try`, `with`, loops and the
            # rest. Their statements hang off list fields, directly or through
            # an `except` handler or a `case`.
            for _, value in ast.iter_fields(node):
                if not isinstance(value, list):
                    continue
                nested = [item for item in value if isinstance(item, ast.stmt)]
                for item in value:
                    if isinstance(item, (ast.ExceptHandler, ast.match_case)):
                        nested.extend(item.body)
                _collect(nested, package, found, deferred=deferred, type_checking=type_checking)


def _resolved(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        assert node.module is not None
        return node.module
    parts = package.split('.')
    anchor = '.'.join(parts[: len(parts) - (node.level - 1)])
    return f'{anchor}.{node.module}' if node.module else anchor


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == 'TYPE_CHECKING') or (
        isinstance(test, ast.Attribute) and test.attr == 'TYPE_CHECKING'
    )
