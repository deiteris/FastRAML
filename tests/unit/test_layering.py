"""The model's internal layering and I/O boundary (docs/02-architecture.md § 2),
and the AGENTS.md rules on regex engines and `__slots__`.

Imports under `if TYPE_CHECKING:` never run and are exempt.
"""

from __future__ import annotations

import ast
import enum
import importlib
import inspect
import typing

import pytest

from tests.sources import MODEL, imports, module_name, parse, sources

#: Functions that read a file or URL: every RAML input arrives through the
#: parse's loader, never directly.
_IO_FUNCTIONS = frozenset({'builtins.open', 'io.open', 'io.FileIO', 'os.open', 'codecs.open', 'urllib.request.urlopen'})
#: Methods by that name read a file or URL whatever they are called on
#: (`Path.read_text`, `Path.open`, an opener's `open`).
_IO_ATTRIBUTES = frozenset({'open', 'read_text', 'read_bytes', 'urlopen'})

#: The parser modules the type layer imports at module level. `references`
#: and `substitutions` are leaves: they import nothing of `fastraml` at runtime.
_PARSER_FOR_TYPES = frozenset(
    {
        'fastraml.parser.facets',
        'fastraml.parser.annotations',
        'fastraml.parser.includes',
        'fastraml.parser.references',
        'fastraml.parser.substitutions',
    }
)

#: Every import deferred into a function body under `parser/` and `types/`,
#: as (importer, imported): each breaks a cycle docs/02 § 2 names.
_DEFERRED = frozenset(
    {
        ('fastraml.types.shape', 'fastraml.parser.fragments'),
        ('fastraml.types.schema_compile', 'fastraml.parser.fragments'),
    }
)


def _runtime(*roots: str) -> list[tuple[str, int, str, bool]]:
    """(importer, line, imported module, deferred) for every `fastraml`
    import under `roots` that runs.
    """
    return [
        (module_name(path), found.line, found.module, found.deferred)
        for path in sources(*roots)
        for found in imports(path)
        if not found.type_checking and found.module.startswith('fastraml.')
    ]


def _module(imported: str, of: frozenset[str] | set[str]) -> str | None:
    """The innermost module of `of` that `imported` names, or one of whose
    members it names.
    """
    within = [module for module in of if imported == module or imported.startswith(f'{module}.')]
    return max(within, key=len, default=None)


def test_the_type_layer_imports_only_the_leaf_parser_modules():
    offenders = [
        f'{importer}:{line} imports {imported}'
        for importer, line, imported, deferred in _runtime('fastraml/types')
        if not deferred and imported.startswith('fastraml.parser.') and _module(imported, _PARSER_FOR_TYPES) is None
    ]
    assert not offenders, '\n'.join(offenders)


def test_the_leaf_parser_modules_import_nothing_of_the_package():
    leaves = ('fastraml/parser/references.py', 'fastraml/parser/substitutions.py')
    offenders = [f'{importer}:{line} imports {imported}' for importer, line, imported, _ in _runtime(*leaves)]
    assert not offenders, '\n'.join(offenders)


def test_the_deferred_imports_are_the_documented_ones():
    packages = {module_name(path) for path in sources('fastraml/parser', 'fastraml/types')}
    found = {
        (importer, _module(imported, packages) or imported)
        for importer, _, imported, deferred in _runtime('fastraml/parser', 'fastraml/types')
        if deferred
    }
    assert found == _DEFERRED


def test_the_registry_imports_no_parser_or_type_module():
    offenders = [
        f'{importer}:{line} imports {imported}'
        for importer, line, imported, _ in _runtime('fastraml/registry.py')
        if imported.startswith(('fastraml.parser', 'fastraml.types'))
    ]
    assert not offenders, '\n'.join(offenders)


def _bound_names(tree: ast.Module) -> dict[str, str]:
    """Each name `tree`'s imports bind, mapped to what it names:
    `import re as r` binds `r` to `re`, `from io import FileIO as F` binds `F`
    to `io.FileIO`.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    head = alias.name.split('.')[0]
                    bound[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            bound.update((alias.asname or alias.name, f'{node.module}.{alias.name}') for alias in node.names)
    return bound


def _qualified(node: ast.expr, bound: dict[str, str]) -> str | None:
    """The dotted name `node` reads, its head resolved through `bound`; a bare
    name nothing imports is a builtin.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id, f'builtins.{node.id}')
    if isinstance(node, ast.Attribute):
        head = _qualified(node.value, bound)
        return f'{head}.{node.attr}' if head is not None else None
    return None


def _io_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Each place in `tree` that reaches a function opening a file or URL,
    called or passed as a value, with its line.
    """
    bound = _bound_names(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
            continue
        name = _qualified(node, bound)
        if name in _IO_FUNCTIONS or (isinstance(node, ast.Attribute) and node.attr in _IO_ATTRIBUTES):
            found.append((node.lineno, name or ast.unparse(node)))
    return found


def test_no_model_module_opens_a_file_or_url():
    offenders = [
        f'{module_name(path)}:{line} calls {name}' for path in sources(*MODEL) for line, name in _io_calls(parse(path))
    ]
    assert not offenders, '\n'.join(offenders)


@pytest.mark.parametrize(
    'source',
    [
        'open(p)',
        'io.open(p)',
        'Path(p).read_text()',
        'p.read_bytes()',
        'import urllib.request\nurllib.request.urlopen(u)',
        'from urllib.request import urlopen\nurlopen(u)',
        'from urllib.request import urlopen as u\nu(x)',
        'p.open()',
        'import io\nio.FileIO(p)',
        'from io import FileIO\nFileIO(p)',
        'import os as o\no.open(p, 0)',
        'map(open, ps)',
    ],
)
def test_the_io_check_sees_each_spelling(source):
    assert _io_calls(ast.parse(source))


def test_a_name_that_only_looks_like_io_is_not_flagged():
    assert _io_calls(ast.parse('from fastraml.loaders import load\nload(u)\nopened = 1')) == []


#: The `re` functions that take a pattern first.
_RE_CALLS = frozenset({'compile', 'search', 'match', 'fullmatch', 'finditer', 'findall', 'sub', 'subn', 'split'})

#: The calls that compile a pattern that is not RAML's, as (module, scope),
#: the scope being the enclosing function or, at module level, the name
#: assigned: a configuration file's regex, one built from `re.escape`d pieces,
#: the media-type grammars, and the built-in date/time grammars, composed from
#: constant pieces and shared with the schema exports. Those end in
#: `(?![\s\S])`, which re2 cannot compile. A RAML regex goes through
#: `compile_pattern` / `regex_engine` instead, so `regex_engine='re2'` covers
#: it (docs/13 § 2).
_NON_RAML_PATTERNS = frozenset(
    {
        ('fastraml.config', '_compatibility_rule'),
        ('fastraml.parser.facets', 'MEDIA_RANGE'),
        ('fastraml.parser.facets', 'MEDIA_TYPE'),
        ('fastraml.parser.facets', '_MEDIA_HEAD'),
        ('fastraml.parser.facets', '_PARAMETER'),
        ('fastraml.types.values', 'DATE_ONLY'),
        ('fastraml.types.values', 'TIME_ONLY'),
        ('fastraml.types.values', 'DATETIME_ONLY'),
        ('fastraml.types.values', '_RFC3339'),
        ('fastraml.types.values', '_RFC2616'),
        ('fastraml.views.lint.config', '_setting'),
        ('fastraml.views.lint.rules.content', '_Segment.__init__'),
    }
)


def _regex_calls(tree: ast.Module) -> list[tuple[int, str, str]]:
    """(line, scope, call) for each `re` function reached other than by a call
    with a string literal as its pattern (called with a computed one, or passed
    as a value), each `from re import` of one, and each import of `re2`. The
    scope is the enclosing qualified name; at module level, the single name an
    assignment binds, or `''`.
    """
    aliases = {name for name, target in _bound_names(tree).items() if target == 're'}
    literal = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(_pattern(node), ast.Constant)
        and isinstance(_pattern(node).value, str)
    }
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f'{scope}.{node.name}' if scope else node.name
        elif not scope and (target := _assigned_name(node)):
            scope = target
        if isinstance(node, ast.Import) and any(alias.name.split('.')[0] == 're2' for alias in node.names):
            found.append((node.lineno, scope, 'import re2'))
        elif isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 're2':
            found.append((node.lineno, scope, 'from re2 import'))
        elif isinstance(node, ast.ImportFrom) and node.module == 're':
            found.extend((node.lineno, scope, f'from re import {a.name}') for a in node.names if a.name in _RE_CALLS)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr in _RE_CALLS
            and id(node) not in literal
        ):
            found.append((node.lineno, scope, f're.{node.attr}'))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, '')
    return found


def _pattern(call: ast.Call) -> ast.expr | None:
    """The pattern argument of an `re` call: first positional, or `pattern=`."""
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg == 'pattern'), None)


def _assigned_name(node: ast.AST) -> str | None:
    """The one name an assignment binds, if it binds exactly one."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target: ast.expr = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def _regex_offenders(module: str, tree: ast.Module) -> list[str]:
    return [
        f'{module}:{line} {call} in {scope or "<module>"}'
        for line, scope, call in _regex_calls(tree)
        if not (module == 'fastraml.parser.facets' and call == 'import re2')
        and (module, scope) not in _NON_RAML_PATTERNS
    ]


def test_every_computed_regex_is_raml_s_or_listed():
    offenders = [found for path in sources('fastraml') for found in _regex_offenders(module_name(path), parse(path))]
    assert not offenders, '\n'.join(offenders)


def test_a_listed_module_level_grammar_does_not_exempt_its_neighbours():
    source = 'import re\nDATE_ONLY: Final = re.compile(rf"^{_DATE}")\nOTHER = re.compile(p)\nre.compile(p)\n'
    assert _regex_offenders('fastraml.types.values', ast.parse(source)) == [
        'fastraml.types.values:3 re.compile in OTHER',
        'fastraml.types.values:4 re.compile in <module>',
    ]


@pytest.mark.parametrize(
    'source',
    [
        'import re\nre.compile(p)',
        'import re\nre.search(p, s)',
        'import re\nre.sub(f"{p}", "", s)',
        'import re as regex\nregex.fullmatch(p, s)',
        'import re\nre.compile(pattern=p)',
        'from re import compile',
        'import re\nimport functools\nfunctools.lru_cache(re.compile)',
        'import re\nmap(re.compile, xs)',
        'import re\ncompile_ = re.compile',
        'import re2',
        'from re2 import compile',
    ],
)
def test_the_regex_check_sees_each_spelling(source):
    assert _regex_calls(ast.parse(source))


def test_a_literal_pattern_is_not_flagged():
    assert _regex_calls(ast.parse('import re\nre.compile("a+")\nre.search(r"b", s)')) == []


#: The packages outside the model: composition roots and consumers of it.
_NOT_MODEL = ('fastraml.views', 'fastraml.service', 'fastraml.join', 'fastraml.cli', 'fastraml.skilldata')

#: Classes that keep an instance `__dict__`, and why.
_DICT_ALLOWED = frozenset(
    {
        # A PyYAML loader: its bases (`CParser`, `SafeConstructor`, `Resolver`)
        # define no `__slots__` and set attributes per instance, so a slot
        # here would save nothing.
        'fastraml.yamlnode._RamlLoader',
    }
)


def _model_classes() -> list[type]:
    """Every class a model module defines at its top level, other than an
    exception, an `Enum`, a `Protocol`, a `TypedDict` or a `NamedTuple`.
    """
    found: list[type] = []
    for path in sources('fastraml'):
        name = module_name(path)
        if any(name == root or name.startswith(f'{root}.') for root in _NOT_MODEL):
            continue
        for _, cls in inspect.getmembers(importlib.import_module(name), inspect.isclass):
            if (
                cls.__module__ != name
                or issubclass(cls, (BaseException, enum.Enum))
                or getattr(cls, '_is_protocol', False)
                or typing.is_typeddict(cls)
                or (issubclass(cls, tuple) and hasattr(cls, '_fields'))
            ):
                continue
            found.append(cls)
    return found


def test_every_model_class_declares_its_slots():
    """AGENTS.md: `__slots__` on every model class. A class whose instances
    carry a `__dict__` has a base, its own or inherited, that forgot them.
    """
    classes = _model_classes()
    assert len(classes) > 50, 'the walk found too few classes to mean anything'
    with_dict = {f'{cls.__module__}.{cls.__qualname__}' for cls in classes if cls.__dictoffset__}
    assert with_dict == _DICT_ALLOWED
