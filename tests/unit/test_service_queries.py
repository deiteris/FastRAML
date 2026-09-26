"""What an editor asks of a snapshot (docs/21 § 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml import Stage
from fastraml.service import queries
from fastraml.service.queries import SymbolKind
from fastraml.service.workspace import Workspace
from fastraml.uris import path_to_file_uri
from tests.unit.conftest import write_files
from tests.unit.test_lenient import TestTheModelSaysHowFarItGot as _Lenient

if TYPE_CHECKING:
    from pathlib import Path

    from fastraml.service.workspace import Snapshot

API = """#%RAML 1.0
title: Demo
uses:
  lib: lib.raml
types:
  Entity:
    properties:
      id: string
  Book:
    type: Entity
    properties:
      author: lib.Person
      cover: !include cover.raml
traits:
  paged:
    queryParameters:
      limit: <<max>>
  spare:
    description: Never applied.
resourceTypes:
  collection:
    get:
      description: Lists them.
/books:
  type: collection
  is: [{paged: {max: integer}}]
  post:
    body:
      application/json:
        type: Book
"""

LIBRARY = """#%RAML 1.0 Library
types:
  Person:
    properties:
      name: string
"""

COVER = '#%RAML 1.0 DataType\ntype: file\n'


@pytest.fixture
def parsed(tmp_path: Path) -> tuple[Snapshot, str]:
    write_files(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
    folder = path_to_file_uri(tmp_path)
    (snapshot,) = Workspace([folder]).snapshots(f'{folder}/api.raml')
    return snapshot, folder


def _where(text: str, needle: str, nth: int = 0) -> tuple[int, int]:
    """The 1-based line and column of the `nth` `needle` in `text`."""
    offset = -1
    for _ in range(nth + 1):
        offset = text.index(needle, offset + 1)
    return text.count('\n', 0, offset) + 1, offset - text.rfind('\n', 0, offset)


def _starts(sites: list[queries.Site]) -> list[tuple[str, int, int]]:
    return [(site.uri.rsplit('/', 1)[-1], site.span.line, site.span.column) for site in sites]


class TestNames:
    def test_a_type_name_goes_to_its_declaration(self, parsed):
        snapshot, folder = parsed
        found = queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n', 0))
        assert _starts(found) == [('api.raml', *_where(API, 'Book:'))]

    def test_a_qualified_name_goes_to_the_library_and_its_prefix_to_the_uses_entry(self, parsed):
        snapshot, folder = parsed
        line, column = _where(API, 'lib.Person')
        assert _starts(queries.definition(snapshot, f'{folder}/api.raml', line, column)) == [
            ('api.raml', *_where(API, 'lib:'))
        ]
        found = queries.definition(snapshot, f'{folder}/api.raml', line, column + len('lib.'))
        assert _starts(found) == [('lib.raml', *_where(LIBRARY, 'Person'))]

    def test_a_path_goes_to_the_file(self, parsed):
        snapshot, folder = parsed
        found = queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'cover.raml'))
        assert _starts(found) == [('cover.raml', 1, 1)]

    def test_references_list_every_use_and_optionally_the_declaration(self, parsed):
        snapshot, folder = parsed
        at = _where(API, 'Entity:')
        uses = [('api.raml', *_where(API, 'Entity\n'))]
        assert _starts(queries.references(snapshot, f'{folder}/api.raml', *at, declaration=False)) == uses
        assert _starts(queries.references(snapshot, f'{folder}/api.raml', *at)) == sorted([('api.raml', *at), *uses])

    def test_highlights_mark_the_definition(self, parsed):
        snapshot, folder = parsed
        found = queries.highlights(snapshot, f'{folder}/api.raml', *_where(API, 'Entity\n'))
        assert sorted((span.line, is_definition) for span, is_definition in found) == [
            (_where(API, 'Entity:')[0], True),
            (_where(API, 'Entity\n')[0], False),
        ]

    def test_nothing_is_under_a_cursor_on_no_name(self, parsed):
        snapshot, folder = parsed
        assert queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'title')) == []


class TestHover:
    def test_a_type_shows_its_effective_declaration(self, parsed):
        snapshot, folder = parsed
        text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n'))
        assert 'author:' in text
        assert 'id:' in text, 'an inherited property is in the effective form'
        assert (span.line, span.column) == _where(API, 'Book\n')

    def test_a_trait_shows_its_parameters(self, parsed):
        snapshot, folder = parsed
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'paged: {'))
        assert '`max`' in text

    def test_a_built_in_says_so(self, parsed):
        snapshot, folder = parsed
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'string'))
        assert text == 'built-in type `string`'


class TestSymbols:
    def test_the_outline_holds_declarations_and_resources_with_their_methods(self, parsed):
        snapshot, folder = parsed
        found = queries.document_symbols(snapshot, f'{folder}/api.raml')
        assert [(symbol.name, symbol.kind) for symbol in found] == [
            ('Entity', SymbolKind.TYPE),
            ('Book', SymbolKind.TYPE),
            ('paged', SymbolKind.TRAIT),
            ('spare', SymbolKind.TRAIT),
            ('collection', SymbolKind.RESOURCE_TYPE),
            ('/books', SymbolKind.RESOURCE),
        ]
        # `get` came from the resource type: it is written in its declaration.
        assert [child.name for child in found[-1].children] == ['post']

    def test_a_symbol_spans_its_value_and_selects_its_name(self, parsed):
        snapshot, folder = parsed
        book = next(
            symbol for symbol in queries.document_symbols(snapshot, f'{folder}/api.raml') if symbol.name == 'Book'
        )
        line, column = _where(API, 'Book:')
        assert (book.selection.line, book.selection.column, book.selection.end_column) == (line, column, column + 4)
        assert (book.span.line, book.span.end_line) == (line, _where(API, 'cover.raml')[0])

    def test_workspace_symbols_match_part_of_a_name_in_any_case(self, parsed):
        snapshot, _ = parsed
        found = queries.workspace_symbols([snapshot, snapshot], 'PERS')
        assert [(symbol.name, symbol.uri.rsplit('/', 1)[-1]) for symbol in found] == [('Person', 'lib.raml')]


class TestStructure:
    def test_links_are_each_path_with_its_target(self, parsed):
        snapshot, folder = parsed
        found = queries.links(snapshot, f'{folder}/api.raml')
        assert sorted((site.uri.rsplit('/', 1)[-1], site.span.line) for site in found) == [
            ('cover.raml', _where(API, 'cover.raml')[0]),
            ('lib.raml', _where(API, 'lib.raml')[0]),
        ]

    def test_a_block_folds_from_its_key_to_its_last_line(self):
        folded = (_where(API, 'traits:')[0], _where(API, 'Never applied')[0])
        assert folded in queries.folding_ranges(API, 'file:///api.raml')

    def test_a_selection_grows_from_the_token_outward(self):
        line, column = _where(API, 'Lists')
        found = queries.selection_ranges(API, 'file:///api.raml', line, column)
        assert (found[0].line, found[0].column) == (line, column)
        assert [span.line for span in found[1:4]] == [line, line, _where(API, 'get:')[0]]
        assert found[-1].line == 2, 'the document'

    def test_text_that_does_not_compose_has_no_structure(self):
        assert queries.folding_ranges('a: [', 'file:///api.raml') == []
        assert queries.selection_ranges('a: [', 'file:///api.raml', 1, 1) == []


class TestTypeHierarchy:
    def test_a_type_names_its_supertypes_and_its_subtypes(self, parsed):
        snapshot, folder = parsed
        book = queries.type_at(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n'))
        assert [symbol.name for symbol in queries.supertypes(snapshot, book)] == ['Entity']
        entity = queries.supertypes(snapshot, book)[0]
        assert [symbol.name for symbol in queries.subtypes(snapshot, entity)] == ['Book']

    def test_an_item_is_found_again_in_a_later_snapshot_by_where_it_is_written(self, tmp_path):
        write_files(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        folder = path_to_file_uri(tmp_path)
        workspace = Workspace([folder])
        root = f'{folder}/api.raml'
        book = queries.type_at(workspace.snapshot(root), root, *_where(API, 'Book:'))
        workspace.change(root, API + 'version: v2\n', 2)
        assert [symbol.name for symbol in queries.supertypes(workspace.snapshot(root), book)] == ['Entity']


class TestDiagnostics:
    def test_a_chain_is_reported_at_its_innermost_frame_with_a_position(self, tmp_path):
        write_files(
            tmp_path, {'api.raml': API.replace('lib.Person', 'lib.Nobody'), 'lib.raml': LIBRARY, 'cover.raml': COVER}
        )
        folder = path_to_file_uri(tmp_path)
        snapshot = Workspace([folder]).snapshot(f'{folder}/api.raml')
        (found,) = queries.diagnostics(snapshot, lint=False)[f'{folder}/api.raml']
        assert (found.site.span.line, found.site.span.column) == _where(API, 'lib.Person')
        assert found.info.get('type') == 'lib.Nobody'
        assert found.source == queries.SOURCE

    def test_every_file_read_has_an_entry_so_its_old_ones_clear(self, parsed):
        snapshot, folder = parsed
        found = queries.diagnostics(snapshot, lint=False)
        assert {f'{folder}/{name}' for name in ('api.raml', 'lib.raml', 'cover.raml')} <= set(found)
        assert not any(found.values())

    def test_a_finding_is_a_diagnostic_with_its_rule_as_the_code(self, parsed):
        snapshot, folder = parsed
        (found,) = (d for d in queries.diagnostics(snapshot)[f'{folder}/api.raml'] if d.source != queries.SOURCE)
        assert (found.code, found.site.span.line) == ('unused-trait', _where(API, 'spare:')[0])

    def test_the_suppression_line_silences_the_finding(self, parsed):
        snapshot, folder = parsed
        root = f'{folder}/api.raml'
        (finding,) = (d for d in queries.diagnostics(snapshot)[root] if d.source == queries.LINT_SOURCE)
        lines = API.splitlines(keepends=True)
        line = finding.site.span.line
        lines.insert(line - 1, queries.suppression(API, line, finding.code))
        workspace = Workspace([folder])
        workspace.change(root, ''.join(lines), 2)
        assert not [d for d in queries.diagnostics(workspace.snapshot(root))[root] if d.source == queries.LINT_SOURCE]


#: One mistake that stops the parse at each stage.
FAILURES = _Lenient.FAILURES


class TestAStoppedParseAnswersFromItsStages:
    """docs/21 § 4: a query on a snapshot that stopped early answers from the
    stages it completed, and never raises.
    """

    @pytest.mark.parametrize('stage', list(FAILURES), ids=[s.value for s in FAILURES])
    def test_every_query_answers(self, tmp_path, stage):
        failure = FAILURES[stage]
        # A second `types:` would be a duplicate key: the mistake joins the first.
        text = (
            API.replace('types:\n', failure, 1)
            if failure.startswith('types:\n')
            else API.replace('uses:\n', failure + 'uses:\n', 1)
        )
        write_files(tmp_path, {'api.raml': text, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        folder = path_to_file_uri(tmp_path)
        workspace = Workspace([folder])
        root = f'{folder}/api.raml'
        snapshot = workspace.snapshot(root)
        assert snapshot.raml is not None
        assert snapshot.raml.stopped_at is stage
        for line, column in (_where(text, 'Book\n'), _where(text, 'paged: {'), _where(text, 'lib.Person')):
            queries.definition(snapshot, root, line, column)
            queries.references(snapshot, root, line, column)
            queries.highlights(snapshot, root, line, column)
            queries.hover(snapshot, root, line, column)
            item = queries.type_at(snapshot, root, line, column)
            if item is not None:
                queries.supertypes(snapshot, item)
                queries.subtypes(snapshot, item)
        queries.document_symbols(snapshot, root)
        queries.workspace_symbols([snapshot], 'b')
        queries.links(snapshot, root)
        assert queries.diagnostics(snapshot)[root]

    def test_a_declaration_answers_before_the_names_that_use_it_are_bound(self, tmp_path):
        # P4 stops at the unknown trait; P7, which binds type names, never runs.
        text = API.replace('is: [{paged', 'is: [nosuch, {paged')
        write_files(tmp_path, {'api.raml': text, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        folder = path_to_file_uri(tmp_path)
        root = f'{folder}/api.raml'
        snapshot = Workspace([folder]).snapshot(root)
        assert snapshot.raml.stopped_at is Stage.ENDPOINTS
        assert _starts(queries.definition(snapshot, root, *_where(text, 'Entity:'))) == [
            ('api.raml', *_where(text, 'Entity:'))
        ]
        assert queries.definition(snapshot, root, *_where(text, 'Entity\n')) == []
