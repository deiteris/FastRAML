"""The view layer's boundary (docs/02-architecture.md § 2, docs/16-graph.md § 1).

`fastraml/views/` runs after P10 on a finished model. It decides no RAML rule, and
the model does not know it exists. The tests below are what make that a boundary
rather than an intention.

The direction is the whole content of the rule. A view importing the model is
the point of a view. The model importing a view is how a rule that belongs to
the language ends up outside the passes, where nothing runs it in order and
`ParseOptions` cannot reach it.
"""

from __future__ import annotations

import ast
import pathlib

#: Everything the parser is: the passes, the model they build, and the support
#: modules underneath both. `cli` is deliberately absent — it is the one caller
#: allowed to see both sides, which is what a command line is.
_MODEL = ('fastraml/parser', 'fastraml/types', 'fastraml/nodes.py', 'fastraml/registry.py', 'fastraml/datanode.py')

_VIEWS = (
    'walk',
    'severity',
    'graph',
    'tree',
    'render',
    'queries',
    'backward',
    'bindings',
    'jsonschema',
    'openapi',
    'lint',
)


def _imports(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every module `path` imports, by dotted name, with its line."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, node.module))
    return found


def _sources(*roots: str) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        out.extend(sorted(path.rglob('*.py')) if path.is_dir() else [path])
    return out


class TestTheModelDoesNotSeeTheViews:
    def test_no_pass_imports_the_view_layer(self):
        offenders = [
            f'{path}:{line} imports {module}'
            for path in _sources(*_MODEL)
            for line, module in _imports(path)
            if module == 'fastraml.views' or module.startswith('fastraml.views.')
        ]
        assert not offenders, '\n'.join(offenders)

    def test_no_module_outside_the_package_reaches_a_view_except_the_cli(self):
        # The lazy table in `fastraml/__init__.py` names them by string, not by
        # import, so it does not appear here and must not: importing `fastraml`
        # would then build a graph module nobody asked for.
        allowed = {pathlib.Path('fastraml/cli.py')}
        offenders = [
            f'{path}:{line} imports {module}'
            for path in _sources('fastraml')
            if path not in allowed and 'views' not in path.parts
            for line, module in _imports(path)
            if module.startswith('fastraml.views')
        ]
        assert not offenders, '\n'.join(offenders)


class TestTheJoinIsNeitherAPassNorAView:
    """`fastraml/join/` runs on source trees before decoding (docs/20 § 9)."""

    def test_nothing_but_the_cli_imports_the_join(self):
        allowed = {pathlib.Path('fastraml/cli.py')}
        offenders = [
            f'{path}:{line} imports {module}'
            for path in _sources('fastraml')
            if path not in allowed and 'join' not in path.parts
            for line, module in _imports(path)
            if module == 'fastraml.join' or module.startswith('fastraml.join.')
        ]
        assert not offenders, '\n'.join(offenders)

    def test_the_join_imports_no_view(self):
        offenders = [
            f'{path}:{line} imports {module}'
            for path in _sources('fastraml/join')
            for line, module in _imports(path)
            if module.startswith('fastraml.views')
        ]
        assert not offenders, '\n'.join(offenders)


class TestThePackageCostsNothingToImport:
    def test_importing_the_package_imports_no_view(self):
        # A package `__init__` that pulled its modules in would make `fastraml`'s
        # lazy export table pointless: reaching `build_tree` would compile the
        # graph, the renderer and the SPARQL catalogue with it.
        import fastraml.views

        assert fastraml.views.__all__ == []

    def test_only_the_substrates_are_shared_between_views(self):
        """A view importing another *view* would mean a second traversal or a
        second vocabulary. Three modules are substrate rather than view and may
        be shared: `walk` (one addressing traversal, docs/16 § 2), `graph`
        (what the later views read), and `severity` (the ranking arithmetic
        `backward` and `lint` both need, docs/18 § 1 — they grade on different
        axes and share only the comparisons).

        A view that is a package may import *itself*: splitting one view across
        five files is not five views, and `backward` says so thirteen times over
        where the concerns used to interleave.
        """
        substrates = {'walk', 'graph', 'severity'}
        crossings = {
            (path, module)
            for path in _sources('fastraml/views')
            for _, module in _imports(path)
            if module.startswith('fastraml.views.')
        }
        unexpected = {
            (path.as_posix(), module)
            for path, module in crossings
            if module.rsplit('.', 1)[1] not in substrates
            and not any(part in path.parts and module.startswith(f'fastraml.views.{part}') for part in _VIEWS)
        }
        assert not unexpected, unexpected

    def test_the_shared_ranking_knows_nothing_about_either_vocabulary(self):
        """`severity` holds the arithmetic and no meaning. If it ever names a
        grade, the two scales have started to look like one — which is the
        assumption docs/18 § 1 exists to refuse.
        """
        tree = ast.parse(pathlib.Path('fastraml/views/severity.py').read_text(encoding='utf-8'))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        }
        named = literals & {'breaking', 'review', 'compatible', 'cosmetic', 'error', 'warning', 'info'}
        assert not named, named


class TestTheStubMatchesTheExports:
    def test_every_exported_name_is_declared_for_a_type_checker(self):
        # `py.typed` plus a stub means the stub *is* the surface as far as mypy
        # is concerned: a name missing here resolves at runtime and fails to
        # type-check, which is the worst of both.
        import fastraml

        declared = {
            alias.asname or alias.name
            for node in ast.walk(ast.parse(pathlib.Path('fastraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert set(fastraml.__all__) - declared - {'__version__'} == set()

    def test_the_stub_declares_nothing_the_package_does_not_export(self):
        import fastraml

        declared = {
            alias.asname or alias.name
            for node in ast.walk(ast.parse(pathlib.Path('fastraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert declared - set(fastraml.__all__) == set()

    def test_the_stub_points_at_the_module_the_export_table_names(self):
        import fastraml

        stub = {
            (alias.asname or alias.name): node.module
            for node in ast.walk(ast.parse(pathlib.Path('fastraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        wrong = {
            name: (stub[name], module) for name, (module, _) in fastraml._EXPORTS.items() if stub.get(name) != module
        }
        assert not wrong, wrong


def test_the_view_modules_are_the_ones_the_package_documents():
    root = pathlib.Path('fastraml/views')
    on_disk = {path.stem for path in root.glob('*.py')} - {'__init__'}
    on_disk.update(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith('__'))
    assert on_disk == set(_VIEWS)
