"""The LSP adapter, driven over stdio by pygls' client (docs/21 § 5).

One server process for the module: `fastraml lsp`, as an editor starts it.
Assertions are on codes, `data` and ranges, never on message text.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

pytest.importorskip('pygls')

from lsprotocol import types
from pygls.lsp.client import LanguageClient

from fastraml.cli import main
from fastraml.gctuning import FULL_COLLECTION_THRESHOLD
from fastraml.service.lsp import EFFECTIVE_TYPE, SHOW_EFFECTIVE, TREE, RamlServer
from fastraml.uris import path_to_file_uri
from tests.unit.conftest import write_files

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

API = """#%RAML 1.0
title: T
uses:
  lib: lib.raml
traits:
  paged: {}
  spare: {}
types:
  Admin:
    type: lib.User
  Emoji: {description: "\U0001f600", type: lib.User}
  Tags: string[]
/users:
  is: [paged]
  get:
"""
LIBRARY = '#%RAML 1.0 Library\ntypes:\n  User:\n    properties:\n      name: string\n'

#: Long enough for the debounce, the parse and both tiers on any runner.
WAIT = 10.0


def _position(text: str, needle: str, offset: int = 0) -> types.Position:
    """Where `needle` starts in `text`, in UTF-16 units, as the client counts."""
    before = text[: text.index(needle) + offset]
    line = before.count('\n')
    column = before.rpartition('\n')[2]
    return types.Position(line, len(column.encode('utf-16-le')) // 2)


class _Client:
    """A pygls client on its own loop, with the diagnostics it was sent."""

    def __init__(self, folder: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.client = LanguageClient('test', '1')
        self.published: dict[str, list[types.Diagnostic]] = {}
        self.arrived = asyncio.Event()
        self.folder = folder

        @self.client.feature(types.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS)
        def published(params: types.PublishDiagnosticsParams) -> None:
            self.published[params.uri] = params.diagnostics
            self.arrived.set()

    def run[T](self, work: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(asyncio.wait_for(work, WAIT))

    def start(self, *, commands: tuple[str, ...] = (SHOW_EFFECTIVE,)) -> types.InitializeResult:
        self.run(self.client.start_io(sys.executable, '-m', 'fastraml.cli', 'lsp'))
        result = self.run(
            self.client.initialize_async(
                types.InitializeParams(
                    capabilities=types.ClientCapabilities(),
                    workspace_folders=[types.WorkspaceFolder(self.folder, 'root')],
                    initialization_options={'commands': list(commands)},
                )
            )
        )
        self.client.initialized(types.InitializedParams())
        return result

    def stop(self) -> None:
        self.run(self.client.shutdown_async(None))
        self.client.exit(None)
        self.run(self.client.stop())
        self.loop.close()

    def open(self, uri: str, text: str) -> None:
        document = types.TextDocumentItem(uri, 'raml', 1, text)
        self.client.text_document_did_open(types.DidOpenTextDocumentParams(document))

    def change(self, uri: str, text: str, version: int) -> None:
        self.client.text_document_did_change(
            types.DidChangeTextDocumentParams(
                types.VersionedTextDocumentIdentifier(version, uri),
                [types.TextDocumentContentChangeWholeDocument(text)],
            )
        )

    def diagnostics(self, uri: str, until: Callable[[list[types.Diagnostic]], bool]) -> list[types.Diagnostic]:
        """The diagnostics for `uri` once they satisfy `until`."""

        async def wait() -> list[types.Diagnostic]:
            while uri not in self.published or not until(self.published[uri]):
                self.arrived.clear()
                await self.arrived.wait()
            return self.published[uri]

        return self.run(wait())


@pytest.fixture(scope='module')
def lsp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Client]:
    folder = tmp_path_factory.mktemp('lsp')
    write_files(folder, {'api.raml': API, 'lib.raml': LIBRARY})
    client = _Client(path_to_file_uri(folder))
    client.start()
    client.open(f'{client.folder}/api.raml', API)
    yield client
    client.stop()


def _uri(lsp: _Client, name: str) -> str:
    return f'{lsp.folder}/{name}'


def _document(uri: str) -> types.TextDocumentIdentifier:
    return types.TextDocumentIdentifier(uri)


def _lint(diagnostics: list[types.Diagnostic]) -> list[types.Diagnostic]:
    return [d for d in diagnostics if d.source == 'fastraml-lint']


class TestDiagnostics:
    def test_binary_include_is_reported_without_stopping_the_server(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/blob:\n  post:\n    body:\n'
            '      application/octet-stream:\n        example: !include blob.bin\n'
        )
        write_files(tmp_path, {'api.raml': source})
        (tmp_path / 'blob.bin').write_bytes(b'\x00\xee\xff')
        folder = path_to_file_uri(tmp_path)
        client = _Client(folder)
        client.start()
        uri = f'{folder}/api.raml'
        try:
            client.open(uri, source)
            (error,) = [d for d in client.diagnostics(uri, bool) if d.source == 'fastraml']
            assert error.code == 'include is not UTF-8'
            assert error.data == {'path': f'{folder}/blob.bin'}
            assert error.range.start == _position(source, '!include')
        finally:
            client.stop()

    def test_a_lint_finding_is_published_under_its_rule(self, lsp):
        found = lsp.diagnostics(_uri(lsp, 'api.raml'), lambda ds: bool(_lint(ds)))
        (spare,) = [d for d in _lint(found) if d.code == 'unused-trait']
        assert spare.range.start.line == _position(API, 'spare:').line
        assert [d for d in found if d.source == 'fastraml'] == []
        # A file with nothing to show is never sent.
        assert _uri(lsp, 'lib.raml') not in lsp.published

    def test_an_error_is_published_under_its_message_key_and_cleared_when_fixed(self, lsp):
        uri = _uri(lsp, 'api.raml')
        lsp.diagnostics(uri, lambda ds: bool(_lint(ds)))
        broken = API.replace('type: lib.User', 'type: lib.Nobody')
        lsp.change(uri, broken, 2)
        (error,) = lsp.diagnostics(uri, lambda ds: any(d.source == 'fastraml' for d in ds))[:1]
        assert error.code == 'reference not found'
        assert error.data == {'type': 'lib.Nobody', 'missing': 'Nobody'}
        assert error.range.start == _position(broken, 'lib.Nobody')
        lsp.change(uri, API, 3)
        # Times out unless the lint tier arrives with the error gone.
        lsp.diagnostics(uri, lambda ds: bool(_lint(ds)) and not any(d.source == 'fastraml' for d in ds))

    def test_a_closed_file_the_root_stops_reading_is_cleared(self, lsp, tmp_path):
        # In a folder added after `initialize`, with the error on disk only.
        write_files(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY.replace('string', 'strin')})
        folder = path_to_file_uri(tmp_path)
        added = types.WorkspaceFoldersChangeEvent(added=[types.WorkspaceFolder(folder, 'added')], removed=[])
        lsp.client.workspace_did_change_workspace_folders(types.DidChangeWorkspaceFoldersParams(added))
        api, lib = f'{folder}/api.raml', f'{folder}/lib.raml'
        lsp.open(api, API)
        (error,) = lsp.diagnostics(lib, bool)
        assert error.code == 'reference not found'
        lsp.change(api, API.replace('uses:\n  lib: lib.raml\n', '').replace('lib.User', 'string'), 2)
        # Times out unless an empty list is published for the file.
        assert not lsp.diagnostics(lib, lambda ds: not ds)

    def test_a_file_no_root_reads_shows_its_own_diagnostics_until_it_closes(self, lsp):
        uri = _uri(lsp, 'orphan.raml')
        lsp.open(uri, LIBRARY.replace('string', 'strin'))
        (error,) = lsp.diagnostics(uri, bool)
        assert error.code == 'reference not found'
        lsp.client.text_document_did_close(types.DidCloseTextDocumentParams(_document(uri)))
        # Times out unless an empty list is published for the file.
        assert not lsp.diagnostics(uri, lambda ds: not ds)

    def test_a_suppress_fix_is_offered_for_a_finding_and_not_for_an_error(self, lsp):
        uri = _uri(lsp, 'api.raml')
        (finding,) = [d for d in _lint(lsp.diagnostics(uri, lambda ds: bool(_lint(ds)))) if d.code == 'unused-trait']
        error = types.Diagnostic(finding.range, 'k', code='k', source='fastraml')
        actions = lsp.run(
            lsp.client.text_document_code_action_async(
                types.CodeActionParams(_document(uri), finding.range, types.CodeActionContext([finding, error]))
            )
        )
        (action,) = actions
        (edit,) = action.edit.changes[uri]
        assert edit.range.start == types.Position(finding.range.start.line, 0)
        assert edit.new_text == '  # fastraml: ignore unused-trait\n'


class TestNavigation:
    def test_inlay_hints_use_utf16_positions_and_clickable_type_labels(self, lsp):
        uri = _uri(lsp, 'hints.raml')
        document = (
            '#%RAML 1.0\ntitle: T\nannotationTypes:\n  Info:\n    properties:\n'
            '      name: string\n(Info): {"😀": 0, name: Ada}\n'
        )
        lsp.open(uri, document)
        start = _position(document, '(Info):')
        end = _position(document, 'Ada', len('Ada'))
        found = lsp.run(
            lsp.client.text_document_inlay_hint_async(
                types.InlayHintParams(_document(uri), types.Range(start, end)),
            )
        )
        (hint,) = found
        assert hint.position == _position(document, 'name: Ada', len('name'))
        assert hint.kind == types.InlayHintKind.Type
        assert hint.label[0].value == '[string]'
        assert hint.label[0].location.uri == uri
        assert hint.label[0].location.range.start == _position(document, 'name: string')

    def test_clients_without_the_effective_command_get_no_dead_lenses(self, tmp_path):
        write_files(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        client = _Client(path_to_file_uri(tmp_path))
        client.start(commands=())
        try:
            uri = _uri(client, 'api.raml')
            client.open(uri, API)
            assert not client.run(client.client.text_document_code_lens_async(types.CodeLensParams(_document(uri))))
        finally:
            client.stop()

    def test_code_lens_opens_an_on_demand_effective_type(self, lsp):
        uri = _uri(lsp, 'api.raml')
        found = lsp.run(lsp.client.text_document_code_lens_async(types.CodeLensParams(_document(uri))))
        lens = next(lens for lens in found if lens.command.arguments[0]['name'] == 'Admin')
        assert lens.command.title == 'Show effective type'
        assert lens.command.command == SHOW_EFFECTIVE
        at = lens.command.arguments[0]

        async def request():
            return await lsp.client.protocol.send_request_async(
                EFFECTIVE_TYPE,
                {
                    'textDocument': {'uri': at['uri']},
                    'root': at['root'],
                    'position': at['position'],
                    'name': at['name'],
                },
            )

        text = lsp.run(request())
        assert text.startswith('#%RAML 1.0 DataType\n')
        assert 'name:' in text

    def test_definition_across_files(self, lsp):
        (found,) = lsp.run(
            lsp.client.text_document_definition_async(
                types.DefinitionParams(_document(_uri(lsp, 'api.raml')), _position(API, 'User'))
            )
        )
        assert (found.uri, found.range.start) == (_uri(lsp, 'lib.raml'), _position(LIBRARY, 'User:'))

    def test_a_column_after_an_astral_character_is_counted_in_utf16_units(self, lsp):
        # The emoji is two UTF-16 units and one code point.
        (found,) = lsp.run(
            lsp.client.text_document_definition_async(
                types.DefinitionParams(_document(_uri(lsp, 'api.raml')), _position(API, 'User}', 3))
            )
        )
        assert found.range.start == _position(LIBRARY, 'User:')

    def test_references_include_the_declaration_when_asked(self, lsp):
        def references(declaration: bool) -> list[types.Location]:
            return lsp.run(
                lsp.client.text_document_references_async(
                    types.ReferenceParams(
                        text_document=_document(_uri(lsp, 'lib.raml')),
                        position=_position(LIBRARY, 'User:'),
                        context=types.ReferenceContext(include_declaration=declaration),
                    )
                )
            )

        assert [(r.uri, r.range.start.line) for r in references(False)] == [
            (_uri(lsp, 'api.raml'), _position(API, 'lib.User').line),
            (_uri(lsp, 'api.raml'), _position(API, 'Emoji').line),
        ]
        assert len(references(True)) == 3

    def test_hover_covers_the_name(self, lsp):
        found = lsp.run(
            lsp.client.text_document_hover_async(
                types.HoverParams(_document(_uri(lsp, 'api.raml')), _position(API, 'Admin'))
            )
        )
        assert found.contents.kind == types.MarkupKind.Markdown
        assert 'data type' in found.contents.value
        assert 'Effective summary' not in found.contents.value
        assert 'Specializes `User`' in found.contents.value
        assert (found.range.start, found.range.end) == (_position(API, 'Admin'), _position(API, 'Admin', 5))

    def test_hover_explains_a_raml_key_with_an_exact_range(self, lsp):
        found = lsp.run(
            lsp.client.text_document_hover_async(
                types.HoverParams(_document(_uri(lsp, 'api.raml')), _position(API, 'type: lib.User'))
            )
        )
        assert found.contents.kind == types.MarkupKind.Markdown
        assert 'base data type or type expression' in found.contents.value
        assert (found.range.start, found.range.end) == (
            _position(API, 'type: lib.User'),
            _position(API, 'type: lib.User', len('type')),
        )

    def test_the_type_hierarchy_reaches_a_library_type(self, lsp):
        (item,) = lsp.run(
            lsp.client.text_document_prepare_type_hierarchy_async(
                types.TypeHierarchyPrepareParams(_document(_uri(lsp, 'api.raml')), _position(API, 'Admin'))
            )
        )
        (parent,) = lsp.run(lsp.client.type_hierarchy_supertypes_async(types.TypeHierarchySupertypesParams(item)))
        assert (parent.name, parent.uri) == ('User', _uri(lsp, 'lib.raml'))
        children = lsp.run(lsp.client.type_hierarchy_subtypes_async(types.TypeHierarchySubtypesParams(parent)))
        assert sorted(child.name for child in children) == ['Admin', 'Emoji']


class TestStructure:
    def test_the_outline(self, lsp):
        found = lsp.run(
            lsp.client.text_document_document_symbol_async(types.DocumentSymbolParams(_document(_uri(lsp, 'api.raml'))))
        )
        assert [(s.name, s.kind) for s in found] == [
            ('title', types.SymbolKind.String),
            ('uses', types.SymbolKind.Package),
            ('traits', types.SymbolKind.Package),
            ('types', types.SymbolKind.Package),
            ('/users', types.SymbolKind.Namespace),
        ]
        # A type's icon is its kind; its detail, its type as written.
        assert [(s.name, s.kind, s.detail) for s in found[3].children] == [
            ('Admin', types.SymbolKind.Class, 'lib.User'),
            ('Emoji', types.SymbolKind.Class, 'lib.User'),
            ('Tags', types.SymbolKind.Array, 'string[]'),
        ]
        assert [child.name for child in found[-1].children] == ['is', 'get']

    def test_workspace_symbols(self, lsp):
        found = lsp.run(lsp.client.workspace_symbol_async(types.WorkspaceSymbolParams('user')))
        assert [(s.name, s.location.uri) for s in found] == [('User', _uri(lsp, 'lib.raml'))]

    def test_a_uses_path_links_to_its_file(self, lsp):
        (link,) = lsp.run(
            lsp.client.text_document_document_link_async(types.DocumentLinkParams(_document(_uri(lsp, 'api.raml'))))
        )
        assert (link.target, link.range.start) == (_uri(lsp, 'lib.raml'), _position(API, 'lib.raml'))

    def test_folding_and_selection(self, lsp):
        uri = _uri(lsp, 'api.raml')
        folds = lsp.run(lsp.client.text_document_folding_range_async(types.FoldingRangeParams(_document(uri))))
        assert (_position(API, 'types:').line, _position(API, 'Tags').line) in {
            (f.start_line, f.end_line) for f in folds
        }
        (selection,) = lsp.run(
            lsp.client.text_document_selection_range_async(
                types.SelectionRangeParams(_document(uri), [_position(API, 'Admin')])
            )
        )
        assert selection.range.start == _position(API, 'Admin')
        assert selection.parent is not None

    def test_a_document_that_is_not_a_file_is_not_served(self, lsp):
        lsp.open('untitled:Untitled-1', API)
        found = lsp.run(
            lsp.client.text_document_hover_async(
                types.HoverParams(_document('untitled:Untitled-1'), _position(API, 'Admin'))
            )
        )
        assert found is None


def test_the_server_runs_with_full_collections_deferred(monkeypatch):
    seen: list[tuple[int, int, int]] = []
    monkeypatch.setattr(RamlServer, 'start_io', lambda self: seen.append(gc.get_threshold()))
    assert main(['lsp']) == 0
    assert seen[0][2] >= FULL_COLLECTION_THRESHOLD


def test_a_lint_section_naming_no_ruleset_stops_the_server_before_it_serves(tmp_path, monkeypatch, capsys):
    """Its type admits any name; the linter every parse needs does not, so
    the server would answer nothing (docs/21 § 5).
    """
    started: list[RamlServer] = []
    monkeypatch.setattr(RamlServer, 'start_io', started.append)
    config = tmp_path / 'fastraml.yaml'
    config.write_text('lint:\n  extends: nope\n', encoding='utf-8')
    assert main(['lsp', '--config', str(config)]) == 1
    assert not started
    assert 'unknown ruleset: nope' in capsys.readouterr().err


class TestTree:
    def tree(self, lsp: _Client, uri: str) -> dict[str, object] | None:
        async def ask() -> str | None:
            # Called on the loop, where pygls' future belongs.
            return await lsp.client.protocol.send_request_async(TREE, {'textDocument': {'uri': uri}})

        found = lsp.run(ask())
        return None if found is None else json.loads(found)

    def test_a_root_previews_itself_and_a_library_the_root_reading_it(self, lsp):
        root = self.tree(lsp, _uri(lsp, 'api.raml'))
        assert root is not None
        assert {'Admin', 'Emoji'} <= root['types']['api.raml'].keys()
        assert self.tree(lsp, _uri(lsp, 'lib.raml')) == root

    def test_a_parse_that_stops_before_unwrap_has_none(self, lsp):
        uri = _uri(lsp, 'broken.raml')
        lsp.open(uri, LIBRARY.replace('string', 'strin'))
        assert self.tree(lsp, uri) is None


def test_a_remote_document_is_neither_published_nor_linked(tmp_path, monkeypatch):
    """`--remote` reads `https:` documents the service holds no lines for, and
    an editor cannot open: converting a position in one raised mid-publish.
    """
    from fastraml.positions import Position
    from fastraml.service import queries
    from fastraml.service.lsp import _Positions
    from fastraml.service.text import Encoding
    from fastraml.service.workspace import Workspace

    write_files(tmp_path, {'api.raml': API})
    folder = path_to_file_uri(tmp_path)
    api, remote = f'{folder}/api.raml', 'https://example.com/lib.raml'
    at = Position(1, 1, 1, 2)
    here = queries.Diagnostic(
        queries.Site(api, at),
        'error',
        'k',
        'k',
        queries.SOURCE,
        related=(queries.Related(queries.Site(remote, at), 'r'),),
    )
    there = queries.Diagnostic(queries.Site(remote, at), 'error', 'k', 'k', queries.SOURCE)
    server = RamlServer()
    server.service = Workspace([folder])
    sent: list[types.PublishDiagnosticsParams] = []
    monkeypatch.setattr(queries, 'diagnostics', lambda snapshot, lint=True: {api: [here], remote: [there]})
    monkeypatch.setattr(RamlServer, 'positions', lambda self: _Positions(self.service, Encoding.UTF16))
    monkeypatch.setattr(server, 'text_document_publish_diagnostics', sent.append)
    server.publish([api])
    assert [params.uri for params in sent] == [api]
    assert sent[0].diagnostics[0].related_information is None


class _RemoteClient:
    def get(self, url):
        content = b'#%RAML 1.0 Library\ntypes:\n  Hosted: string\n' if url.endswith('/lib.raml') else b'{}'
        return SimpleNamespace(status_code=200, content=content)


def test_definition_of_remote_include_has_no_local_location_but_keeps_document_link(tmp_path, monkeypatch):
    from fastraml.service.lsp import _Positions
    from fastraml.service.text import Encoding
    from fastraml.service.workspace import Workspace

    remote = 'https://json-schema.org/draft-07/schema'
    source = f'#%RAML 1.0\ntitle: T\ntypes:\n  Remote: !include {remote}\n'
    write_files(tmp_path, {'api.raml': source})
    uri = path_to_file_uri(tmp_path / 'api.raml')
    server = RamlServer()
    server.service = Workspace([path_to_file_uri(tmp_path)], http_client=_RemoteClient())
    monkeypatch.setattr(RamlServer, 'positions', lambda self: _Positions(self.service, Encoding.UTF16))

    definition = server.protocol.fm.features[types.TEXT_DOCUMENT_DEFINITION]
    assert definition(types.DefinitionParams(_document(uri), _position(source, remote))) == []
    document_links = server.protocol.fm.features[types.TEXT_DOCUMENT_DOCUMENT_LINK]
    (link,) = document_links(types.DocumentLinkParams(_document(uri)))
    assert (link.target, link.range.start) == (remote, _position(source, remote))


def test_remote_library_declarations_are_not_local_navigation_targets(tmp_path, monkeypatch):
    from fastraml.service.lsp import _Positions
    from fastraml.service.text import Encoding
    from fastraml.service.workspace import Workspace

    source = (
        '#%RAML 1.0\ntitle: T\nuses:\n  lib: https://example.test/lib.raml\ntypes:\n  Local:\n    type: lib.Hosted\n'
    )
    write_files(tmp_path, {'api.raml': source})
    uri = path_to_file_uri(tmp_path / 'api.raml')
    server = RamlServer()
    server.service = Workspace([path_to_file_uri(tmp_path)], http_client=_RemoteClient())
    monkeypatch.setattr(RamlServer, 'positions', lambda self: _Positions(self.service, Encoding.UTF16))

    definition = server.protocol.fm.features[types.TEXT_DOCUMENT_DEFINITION]
    assert definition(types.DefinitionParams(_document(uri), _position(source, 'lib.Hosted', 4))) == []
    symbols = server.protocol.fm.features[types.WORKSPACE_SYMBOL]
    assert symbols(types.WorkspaceSymbolParams('Hosted')) == []
    prepare = server.protocol.fm.features[types.TEXT_DOCUMENT_PREPARE_TYPE_HIERARCHY]
    assert prepare(types.TypeHierarchyPrepareParams(_document(uri), _position(source, 'lib.Hosted', 4))) is None
    (local,) = prepare(types.TypeHierarchyPrepareParams(_document(uri), _position(source, 'Local')))
    supertypes = server.protocol.fm.features[types.TYPE_HIERARCHY_SUPERTYPES]
    assert supertypes(types.TypeHierarchySupertypesParams(local)) == []


def test_the_server_collects_after_the_pause_and_before_the_parse(monkeypatch):
    """Not in a request: a full collection over the TCK's 1011 snapshots cost
    14 ms, and each open and close made one pending. Not after the lint tier
    either: the snapshot a change drops is still current then, and the next
    parse would build its model beside it (docs/21 § 2).
    """
    events: list[str] = []
    server = RamlServer()
    monkeypatch.setattr(server, 'publish', lambda uris, lint=True: events.append(f'publish lint={lint}'))
    monkeypatch.setattr(server.service, 'collect', lambda: events.append('collect'))

    async def flush() -> None:
        server._flush(lint=False)
        await asyncio.sleep(0.05)  # the lint tier it schedules

    asyncio.run(flush())
    assert events == ['collect', 'publish lint=False', 'publish lint=True']


@pytest.mark.parametrize(
    ('method', 'parsed'),
    [
        pytest.param(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL, ['a.raml'], id='outline: the first'),
        pytest.param(types.TEXT_DOCUMENT_HOVER, ['a.raml'], id='hover: the first that answers'),
        pytest.param(types.TEXT_DOCUMENT_DEFINITION, ['a.raml'], id='definition: the first that answers'),
        pytest.param(types.TEXT_DOCUMENT_REFERENCES, ['a.raml', 'b.raml'], id='references: every one'),
    ],
)
def test_a_request_parses_only_the_snapshots_it_reads(tmp_path, monkeypatch, method, parsed):
    # docs/21 § 5: both roots read the library; the outline read one snapshot
    # but brought both current.
    from fastraml.service.lsp import _Positions
    from fastraml.service.text import Encoding
    from fastraml.service.workspace import Workspace

    root = '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  Admin: lib.User\n'
    write_files(tmp_path, {'a.raml': root, 'b.raml': root, 'lib.raml': LIBRARY})
    folder = path_to_file_uri(tmp_path)
    server = RamlServer()
    server.service = Workspace([folder])
    monkeypatch.setattr(RamlServer, 'positions', lambda self: _Positions(self.service, Encoding.UTF16))
    seen: list[str] = []
    parse = server.service._parse
    monkeypatch.setattr(server.service, '_parse', lambda uri: seen.append(uri.rsplit('/', 1)[1]) or parse(uri))
    document = _document(f'{folder}/lib.raml')
    at = _position(LIBRARY, 'User')
    params = {
        types.TEXT_DOCUMENT_DOCUMENT_SYMBOL: types.DocumentSymbolParams(document),
        types.TEXT_DOCUMENT_HOVER: types.HoverParams(document, at),
        types.TEXT_DOCUMENT_DEFINITION: types.DefinitionParams(document, at),
        types.TEXT_DOCUMENT_REFERENCES: types.ReferenceParams(
            context=types.ReferenceContext(include_declaration=True), text_document=document, position=at
        ),
    }[method]
    assert server.protocol.fm.features[method](params)
    assert seen == parsed
