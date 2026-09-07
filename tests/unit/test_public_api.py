"""What `pyraml` exports — docs/13-public-api.md section 4.

The surface is not stable before 1.0, so this file does not pin it exhaustively.
It pins the two properties that would be defects at any version: every name in
`__all__` resolves, and every concrete shape class is reachable from the top
level, because `isinstance` against them is the documented way to narrow a shape
(§ 6). Missing one means a consumer has to import `pyraml.types.complex_` for
that kind alone and will not guess it.
"""

from __future__ import annotations

import subprocess
import sys

import pyraml
from pyraml.types.base import BUILTIN_TYPES


def test_every_exported_name_resolves():
    assert [name for name in pyraml.__all__ if not hasattr(pyraml, name)] == []


def test_all_is_sorted_and_free_of_duplicates():
    assert pyraml.__all__ == sorted(set(pyraml.__all__))


def test_lazy_export_table_matches_all():
    assert set(pyraml._EXPORTS) == set(pyraml.__all__) - {'__version__'}


def test_bare_import_does_not_load_the_parser_or_its_dependencies():
    code = (
        'import pyraml, sys; '
        "unexpected = {'pyraml.parser.entry', 'pyraml.views.graph', 'yaml', 'jsonschema', 'pluralizer'} & sys.modules.keys(); "
        'assert not unexpected, unexpected'
    )
    subprocess.run([sys.executable, '-c', code], check=True)  # noqa: S603 - this interpreter, fixed code


def test_lazy_export_is_cached_and_listed_by_dir():
    assert 'ParseOptions' in dir(pyraml)
    first = pyraml.ParseOptions
    assert pyraml.__dict__['ParseOptions'] is first
    assert pyraml.ParseOptions is first


def test_wildcard_import_still_resolves_the_whole_public_surface():
    code = 'from pyraml import *; assert ParseOptions and JsonShape and build_graph and __version__'
    subprocess.run([sys.executable, '-c', code], check=True)  # noqa: S603 - this interpreter, fixed code


class TestNarrowing:
    """The classes a consumer needs to walk the model without reaching inward."""

    SHAPES = (
        'AnyShape',
        'ArrayShape',
        'BooleanShape',
        'DateOnlyShape',
        'DateTimeOnlyShape',
        'DateTimeShape',
        'FileShape',
        'IntegerShape',
        'JsonShape',
        'NilShape',
        'NumberShape',
        'ObjectShape',
        'RecursiveShape',
        'StringShape',
        'TimeOnlyShape',
        'UnionShape',
        'UnknownShape',
    )

    def test_all_seventeen_kinds_are_exported(self):
        assert set(self.SHAPES) <= set(pyraml.__all__)

    def test_seventeen_is_the_right_number(self):
        """Guards the list above against a kind being added and not exported.

        `BUILTIN_TYPES` is the type model's own count of named kinds; the two
        extra classes are `UnknownShape`, which no document can name, and
        `RecursiveShape`, which only P9 produces.
        """
        assert len(self.SHAPES) == len(BUILTIN_TYPES) + 2

    def test_the_api_structure_is_exported(self):
        expected = {'BaseShape', 'Body', 'EndPoint', 'Operation', 'PatternProperty', 'Property', 'Request', 'Response'}
        assert expected <= set(pyraml.__all__)

    def test_narrowing_reads_the_way_the_doc_writes_it(self, workspace):
        """docs/13 § 6's example, run rather than quoted."""
        root = workspace({'lib.raml': '#%RAML 1.0 Library\ntypes:\n  T:\n    properties:\n      a: string\n'})
        raml = pyraml.parse_from_path(root / 'lib.raml', pyraml.ParseOptions(unwrap=True))
        shape = raml.entry_point.types['T'].shape
        assert isinstance(shape, pyraml.ObjectShape)
        assert isinstance(shape.properties['a'].base.shape, pyraml.StringShape)


class TestEntryPoints:
    def test_the_three_are_exported(self):
        assert {'parse_from_path', 'parse_from_string', 'parse_lenient'} <= set(pyraml.__all__)

    def test_the_docstring_does_not_claim_a_phase(self):
        """It claimed "Phases 0 and 1 are complete" for eight phases after that.

        A version-ish assertion in a module docstring rots silently; the rule
        that replaced it is that the docstring states contracts, which do not.
        """
        assert 'Phases 0 and 1' not in (pyraml.__doc__ or '')

    def test_the_docstring_says_the_api_is_unstable(self):
        assert 'not stable' in (pyraml.__doc__ or '')
