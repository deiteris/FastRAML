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

import pytest

from tests.sources import MODEL, PACKAGE, imports, module_name, offenders, parse, sources, within

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
    'raml',
    'openapi',
    'base_uri',
    'samples',
    'lint',
    'occurrences',
    'authored',
    'doclinks',
)

#: The modules every view may import (docs/16 § 1).
_SUBSTRATES = frozenset(
    {'fastraml.views.walk', 'fastraml.views.graph', 'fastraml.views.severity', 'fastraml.views.doclinks'}
)


def _view_crossings(importer: str, imported: list[str]) -> list[str]:
    """The modules of `imported` that view module `importer` may not import:
    another view's, unless it is a substrate. A view that is a package may
    import itself.
    """
    own = importer.split('.')[2] if within(importer, 'fastraml.views') and importer.count('.') >= 2 else None
    return [
        module
        for module in imported
        if module.startswith('fastraml.views.')
        and not within(module, *_SUBSTRATES)
        and not (own is not None and within(module, f'fastraml.views.{own}'))
    ]


class TestTheModelDoesNotSeeTheViews:
    def test_no_pass_imports_the_view_layer(self):
        found = offenders(MODEL, 'fastraml.views')
        assert not found, '\n'.join(found)

    def test_no_module_outside_the_package_reaches_a_view_but_the_composition_roots(self):
        # The lazy table in `fastraml/__init__.py` names them by string, not by
        # import, so it does not appear here and must not: importing `fastraml`
        # would then build a graph module nobody asked for. The CLI and the
        # language service compose views; nothing else does (docs/02 § 2).
        allowed = ('fastraml.cli', 'fastraml.views', 'fastraml.service')
        found = offenders(('fastraml',), 'fastraml.views', allowed=allowed)
        assert not found, '\n'.join(found)


class TestTheServiceIsACompositionRoot:
    """`fastraml/service/` composes the views for an editor (docs/21 § 1)."""

    def test_nothing_but_the_cli_imports_the_service(self):
        found = offenders(('fastraml',), 'fastraml.service', allowed=('fastraml.cli', 'fastraml.service'))
        assert not found, '\n'.join(found)


class TestTheJoinIsNeitherAPassNorAView:
    """`fastraml/join/` runs on source trees before decoding (docs/20 § 9)."""

    def test_nothing_but_the_cli_imports_the_join(self):
        found = offenders(('fastraml',), 'fastraml.join', allowed=('fastraml.cli', 'fastraml.join'))
        assert not found, '\n'.join(found)

    def test_the_join_imports_no_view(self):
        found = offenders(('fastraml/join',), 'fastraml.views')
        assert not found, '\n'.join(found)


class TestTheCheckerSeesEveryEvasion:
    """The import walk the boundary tests stand on: each spelling below once
    slipped past it, so a test above passed over the import it exists to catch.
    """

    @pytest.mark.parametrize(
        ('module', 'source', 'expected'),
        [
            ('fastraml.parser.x', 'from fastraml import views\n', 'fastraml.views'),
            ('fastraml.parser.x', 'from ..views import tree\n', 'fastraml.views.tree'),
            ('fastraml.parser.x', 'from .. import views\n', 'fastraml.views'),
            ('fastraml.parser.x', 'def f():\n    from ..views.walk import walk\n', 'fastraml.views.walk'),
            (
                'fastraml.parser.x',
                'try:\n    import fastraml.views.graph\nexcept ImportError:\n    pass\n',
                'fastraml.views.graph',
            ),
        ],
        ids=['from-package-import-name', 'relative', 'relative-package', 'deferred', 'in-try'],
    )
    def test_an_import_of_the_views_is_found(self, module, source, expected):
        assert expected in {found.module for found in imports(source, module)}

    def test_a_package_resolves_relative_imports_against_itself(self):
        found = imports('from .rules import x\n', 'fastraml.views.lint', is_package=True)
        assert 'fastraml.views.lint.rules.x' in {each.module for each in found}

    def test_type_checking_and_deferred_imports_are_marked(self):
        source = 'from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import a\ndef f():\n    import b\n'
        found = {each.module: (each.deferred, each.type_checking) for each in imports(source, 'fastraml.x')}
        assert (found['a'], found['b']) == ((False, True), (True, False))

    @pytest.mark.parametrize(
        'source',
        ['from fastraml.views.lint.graph import x\n', 'from fastraml.views import tree\n', 'from .tree import x\n'],
        ids=['a-view-module-named-like-a-substrate', 'from-package-import-view', 'relative'],
    )
    def test_a_view_reaching_another_view_is_a_crossing(self, source):
        importer = 'fastraml.views.raml'
        crossings = _view_crossings(importer, [found.module for found in imports(source, importer)])
        assert crossings

    @pytest.mark.parametrize(
        ('importer', 'source'),
        [
            ('fastraml.views.raml', 'from fastraml.views.walk import walk\n'),
            ('fastraml.views.raml', 'from fastraml.views import graph\n'),
            ('fastraml.views.lint.rules.x', 'from ..config import y\n'),
        ],
        ids=['substrate', 'substrate-by-name', 'own-package'],
    )
    def test_a_substrate_or_the_view_itself_is_not(self, importer, source):
        assert _view_crossings(importer, [found.module for found in imports(source, importer)]) == []


class TestThePackageCostsNothingToImport:
    def test_importing_the_package_imports_no_view(self):
        # A package `__init__` that pulled its modules in would make `fastraml`'s
        # lazy export table pointless: reaching `build_tree` would compile the
        # graph, the renderer and the SPARQL catalogue with it.
        import fastraml.views

        assert fastraml.views.__all__ == []

    def test_only_the_substrates_are_shared_between_views(self):
        """A view importing another *view* would mean a second traversal or a
        second vocabulary. Four modules are substrate rather than view and may
        be shared: `walk` (one addressing traversal, docs/16 § 2), `graph`
        (what the later views read), `severity` (the ranking arithmetic
        `backward` and `lint` both need, docs/18 § 1 — they grade on different
        axes and share only the comparisons), and `doclinks` (one reading of
        the links in prose, docs/16 § 11).

        A view that is a package may import *itself*: splitting one view across
        five files is not five views, and `backward` says so thirteen times over
        where the concerns used to interleave.
        """
        unexpected = {
            (module_name(path), module)
            for path in sources('fastraml/views')
            for module in _view_crossings(module_name(path), [found.module for found in imports(path)])
        }
        assert not unexpected, unexpected

    def test_the_shared_ranking_knows_nothing_about_either_vocabulary(self):
        """`severity` holds the arithmetic and no meaning. If it ever names a
        grade, the two scales have started to look like one — which is the
        assumption docs/18 § 1 exists to refuse.
        """
        tree = parse(PACKAGE / 'views' / 'severity.py')
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
            for node in ast.walk(parse(PACKAGE / '__init__.pyi'))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert set(fastraml.__all__) - declared - {'__version__'} == set()

    def test_the_stub_declares_nothing_the_package_does_not_export(self):
        import fastraml

        declared = {
            alias.asname or alias.name
            for node in ast.walk(parse(PACKAGE / '__init__.pyi'))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert declared - set(fastraml.__all__) == set()

    def test_the_stub_points_at_the_module_the_export_table_names(self):
        import fastraml

        stub = {
            (alias.asname or alias.name): node.module
            for node in ast.walk(parse(PACKAGE / '__init__.pyi'))
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        wrong = {
            name: (stub[name], module) for name, (module, _) in fastraml._EXPORTS.items() if stub.get(name) != module
        }
        assert not wrong, wrong


def test_the_view_modules_are_the_ones_the_package_documents():
    root = PACKAGE / 'views'
    on_disk = {path.stem for path in root.glob('*.py')} - {'__init__'}
    on_disk.update(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith('__'))
    assert on_disk == set(_VIEWS)
