"""The view layer's boundary — docs/02-architecture.md section 3, docs/16-graph.md.

`pyraml/views/` runs after P10 on a finished model. It decides no RAML rule, and
the model does not know it exists. That was prose until the package existed; the
tests below are what makes it a boundary rather than an intention.

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
_MODEL = ('pyraml/parser', 'pyraml/types', 'pyraml/nodes.py', 'pyraml/registry.py', 'pyraml/datanode.py')

_VIEWS = ('walk', 'graph', 'tree', 'render', 'queries', 'diff')


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
            if module == 'pyraml.views' or module.startswith('pyraml.views.')
        ]
        assert not offenders, '\n'.join(offenders)

    def test_no_module_outside_the_package_reaches_a_view_except_the_cli(self):
        # The lazy table in `pyraml/__init__.py` names them by string, not by
        # import, so it does not appear here and must not: importing `pyraml`
        # would then build a graph module nobody asked for.
        allowed = {pathlib.Path('pyraml/cli.py')}
        offenders = [
            f'{path}:{line} imports {module}'
            for path in _sources('pyraml')
            if path not in allowed and 'views' not in path.parts
            for line, module in _imports(path)
            if module.startswith('pyraml.views')
        ]
        assert not offenders, '\n'.join(offenders)


class TestThePackageCostsNothingToImport:
    def test_importing_the_package_imports_no_view(self):
        # A package `__init__` that pulled its modules in would make `pyraml`'s
        # lazy export table pointless: reaching `build_tree` would compile the
        # graph, the renderer and the SPARQL catalogue with it.
        import pyraml.views

        assert pyraml.views.__all__ == []

    def test_the_walk_is_the_only_module_the_others_share(self):
        # Every view addresses through one walk (docs/16 § 4). A view importing
        # another view would mean a second traversal or a second vocabulary.
        crossings = {
            (path.stem, module.rsplit('.', 1)[1])
            for path in _sources('pyraml/views')
            for _, module in _imports(path)
            if module.startswith('pyraml.views.')
        }
        unexpected = {(source, target) for source, target in crossings if target not in {'walk', 'graph'}}
        assert not unexpected, unexpected


class TestTheStubMatchesTheExports:
    def test_every_exported_name_is_declared_for_a_type_checker(self):
        # `py.typed` plus a stub means the stub *is* the surface as far as mypy
        # is concerned: a name missing here resolves at runtime and fails to
        # type-check, which is the worst of both.
        import pyraml

        declared = {
            alias.asname or alias.name
            for node in ast.walk(ast.parse(pathlib.Path('pyraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert set(pyraml.__all__) - declared - {'__version__'} == set()

    def test_the_stub_declares_nothing_the_package_does_not_export(self):
        import pyraml

        declared = {
            alias.asname or alias.name
            for node in ast.walk(ast.parse(pathlib.Path('pyraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert declared - set(pyraml.__all__) == set()

    def test_the_stub_points_at_the_module_the_export_table_names(self):
        import pyraml

        stub = {
            (alias.asname or alias.name): node.module
            for node in ast.walk(ast.parse(pathlib.Path('pyraml/__init__.pyi').read_text(encoding='utf-8')))
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        wrong = {
            name: (stub[name], module) for name, (module, _) in pyraml._EXPORTS.items() if stub.get(name) != module
        }
        assert not wrong, wrong


def test_the_view_modules_are_the_ones_the_package_documents():
    on_disk = {path.stem for path in pathlib.Path('pyraml/views').glob('*.py')} - {'__init__'}
    assert on_disk == set(_VIEWS)
