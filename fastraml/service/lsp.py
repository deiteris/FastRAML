"""The LSP adapter: the service's queries over pygls (docs/21 § 5).

Protocol only. Each handler converts the client's positions, asks the
workspace for the snapshots serving the file, runs a query, and converts the
answer back. It holds no rule and resolves no name.

Everything runs on the server's one event loop, so the workspace needs no lock.
A change publishes diagnostics after a pause (`DEBOUNCE`); a request never
waits for it, since a query parses whatever is stale.

pygls is the optional extra `fastraml[lsp]`, imported by `fastraml lsp` only.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from fastraml import __version__
from fastraml.positions import Position
from fastraml.service import queries
from fastraml.service.text import Encoding, Lines
from fastraml.service.workspace import Workspace, canonical

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fastraml.config import FastRamlConfig
    from fastraml.service.workspace import Snapshot

    #: A query answering sites for a position, as `definition` does.
    SiteQuery = Callable[[Snapshot, str, int, int], list[queries.Site]]
    #: A query answering the types related to one, as `supertypes` does.
    TypeQuery = Callable[[Snapshot, queries.Symbol], list[queries.Symbol]]


class _AtPosition(Protocol):
    """A request about one position in one document."""

    text_document: types.TextDocumentIdentifier
    position: types.Position


__all__ = ['DEBOUNCE', 'RamlServer']

#: Seconds without a change before diagnostics are published.
DEBOUNCE: Final = 0.3

_SEVERITY: Final = {
    'error': types.DiagnosticSeverity.Error,
    'warning': types.DiagnosticSeverity.Warning,
    'info': types.DiagnosticSeverity.Information,
}

_SYMBOL: Final = {
    queries.SymbolKind.TYPE: types.SymbolKind.Class,
    queries.SymbolKind.ANNOTATION_TYPE: types.SymbolKind.Interface,
    queries.SymbolKind.TRAIT: types.SymbolKind.Function,
    queries.SymbolKind.RESOURCE_TYPE: types.SymbolKind.Struct,
    queries.SymbolKind.SECURITY_SCHEME: types.SymbolKind.Key,
    queries.SymbolKind.LIBRARY: types.SymbolKind.Module,
    queries.SymbolKind.PROPERTY: types.SymbolKind.Property,
    queries.SymbolKind.FACET: types.SymbolKind.Field,
    queries.SymbolKind.RESOURCE: types.SymbolKind.Namespace,
    queries.SymbolKind.METHOD: types.SymbolKind.Method,
    queries.SymbolKind.DOCUMENTATION: types.SymbolKind.String,
}


def _file(uri: str) -> str | None:
    """The canonical spelling of a `file:` URI; `None` for any other scheme
    (`untitled:`, `git:`), which the service does not read.
    """
    try:
        return canonical(uri)
    except ValueError:
        return None


class _Positions:
    """Conversion for one request or one publish: each file split into lines once."""

    __slots__ = ('_encoding', '_lines', '_workspace')

    def __init__(self, workspace: Workspace, encoding: Encoding) -> None:
        self._workspace = workspace
        self._encoding = encoding
        self._lines: dict[str, Lines] = {}

    def lines(self, uri: str) -> Lines:
        found = self._lines.get(uri)
        if found is None:
            found = self._lines[uri] = Lines(self._workspace.text(uri) or '')
        return found

    def to_server(self, uri: str, position: types.Position) -> tuple[int, int]:
        return self.lines(uri).from_protocol(position.line, position.character, self._encoding)

    def range(self, uri: str, span: Position) -> types.Range:
        lines = self.lines(uri)
        start = lines.to_protocol(span.line, span.column, self._encoding)
        end = lines.to_protocol(span.end_line, span.end_column, self._encoding)
        return types.Range(types.Position(*start), types.Position(*end))

    def span(self, uri: str, found: types.Range) -> Position:
        return Position(*self.to_server(uri, found.start), *self.to_server(uri, found.end))


class RamlServer(LanguageServer):
    """A language server over one `Workspace`, built at `initialize`."""

    def __init__(self, config: FastRamlConfig | None = None, http_client: object | None = None) -> None:
        super().__init__('fastraml', __version__)
        self._config = config
        self._http_client = http_client
        #: The `roots` globs the client sent at `initialize` (docs/21 § 2).
        self._globs: tuple[str, ...] = ()
        self.service = Workspace([], config=config)
        #: Files whose diagnostics wait for the pause after a change.
        self._pending: set[str] = set()
        self._timer: asyncio.TimerHandle | None = None
        #: The files each root last published diagnostics for, to clear them.
        self._shown: dict[str, frozenset[str]] = {}
        #: The client's spelling of each URI it opened.
        self._spelling: dict[str, str] = {}
        self._register()

    # -- the workspace and publishing ---------------------------------------

    def reset(self) -> None:
        """A new workspace over the client's folders, holding its open buffers."""
        folders = [folder.uri for folder in self.workspace.folders.values()]
        if not folders and self.workspace.root_uri is not None:
            folders = [self.workspace.root_uri]
        self.service = Workspace(
            [uri for uri in folders if _file(uri) is not None],
            config=self._config,
            roots=self._globs,
            http_client=self._http_client,
        )
        for uri, document in self.workspace.text_documents.items():
            if (file := _file(uri)) is not None:
                self.service.open(file, document.source, document.version or 0)

    def positions(self) -> _Positions:
        return _Positions(self.service, Encoding(self.workspace.position_encoding or Encoding.UTF16))

    def changed(self, uri: str) -> None:
        """Publish `uri`'s diagnostics, and its dependents', after the pause."""
        self._pending.add(uri)
        self._schedule(DEBOUNCE, lint=False)

    def _schedule(self, delay: float, *, lint: bool) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(delay, partial(self._flush, lint=lint))

    def _flush(self, *, lint: bool) -> None:
        """Parser diagnostics first, then lint as a second tier that the next
        change postpones (docs/21 § 5).
        """
        self._timer = None
        self.publish(self._pending, lint=lint)
        if lint:
            self._pending = set()
        else:
            self._schedule(0, lint=True)

    def publish(self, uris: Iterable[str], *, lint: bool = True) -> None:
        """Publish the diagnostics of every file the roots serving `uris` read.

        A file shows the diagnostics of every root that reads it, and a file
        no root reads shows its own only while it is open. A file a root
        stopped reading is cleared.
        """
        found: dict[str, dict[str, list[queries.Diagnostic]]] = {}

        def of(root: str) -> dict[str, list[queries.Diagnostic]]:
            if root not in found:
                found[root] = queries.diagnostics(self.service.snapshot(root), lint=lint)
            return found[root]

        files: set[str] = set()
        for uri in uris:
            # What a file showed as its own root, if it no longer is one.
            files |= self._shown.pop(uri, frozenset())
            for root in self._serving(uri):
                files |= self._shown.get(root, frozenset()) | of(root).keys()
        positions = self.positions()
        for file in sorted(files):
            merged = {(d.site, d.code, d.message): d for root in self._serving(file) for d in of(root).get(file, ())}
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=self._client(file),
                    diagnostics=[self._diagnostic(positions, d) for d in merged.values()],
                )
            )
        for root, diagnostics in found.items():
            self._shown[root] = frozenset(diagnostics)

    def _serving(self, uri: str) -> list[str]:
        roots = self.service.affected(uri)
        return roots or ([uri] if uri in self.service.buffers else [])

    def _client(self, uri: str) -> str:
        return self._spelling.get(uri, uri)

    def _diagnostic(self, positions: _Positions, diagnostic: queries.Diagnostic) -> types.Diagnostic:
        site = diagnostic.site
        return types.Diagnostic(
            range=positions.range(site.uri, site.span),
            message=diagnostic.message,
            severity=_SEVERITY.get(diagnostic.severity, types.DiagnosticSeverity.Error),
            code=diagnostic.code,
            source=diagnostic.source,
            data={key: _plain(value) for key, value in diagnostic.info.items()},
            related_information=[
                types.DiagnosticRelatedInformation(self._location(positions, related.site), related.message)
                for related in diagnostic.related
            ]
            or None,
        )

    def _location(self, positions: _Positions, site: queries.Site) -> types.Location:
        return types.Location(self._client(site.uri), positions.range(site.uri, site.span))

    def _symbol(self, positions: _Positions, symbol: queries.Symbol) -> types.DocumentSymbol:
        return types.DocumentSymbol(
            name=symbol.name,
            kind=_SYMBOL[symbol.kind],
            range=positions.range(symbol.uri, symbol.span),
            selection_range=positions.range(symbol.uri, symbol.selection),
            children=[self._symbol(positions, child) for child in symbol.children] or None,
        )

    def _item(self, positions: _Positions, symbol: queries.Symbol) -> types.TypeHierarchyItem:
        return types.TypeHierarchyItem(
            name=symbol.name,
            kind=_SYMBOL[symbol.kind],
            uri=self._client(symbol.uri),
            range=positions.range(symbol.uri, symbol.span),
            selection_range=positions.range(symbol.uri, symbol.selection),
        )

    # -- handlers -------------------------------------------------------------

    def _register(self) -> None:  # noqa: PLR0915 - one closure per LSP method
        feature = self.feature

        @feature(types.INITIALIZE)
        def initialize(params: types.InitializeParams) -> None:
            options = params.initialization_options
            self._globs = tuple(options.get('roots', ())) if isinstance(options, dict) else ()
            self.reset()

        @feature(types.INITIALIZED)
        def initialized(_params: types.InitializedParams) -> None:
            capabilities = self.client_capabilities.workspace
            files = None if capabilities is None else capabilities.did_change_watched_files
            if files is not None and files.dynamic_registration:
                watcher = types.FileSystemWatcher(glob_pattern='**/*')
                options = types.DidChangeWatchedFilesRegistrationOptions(watchers=[watcher])
                registration = types.Registration('fastraml-files', types.WORKSPACE_DID_CHANGE_WATCHED_FILES, options)
                self.client_register_capability(types.RegistrationParams([registration]))

        @feature(types.WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)
        def folders(_params: types.DidChangeWorkspaceFoldersParams) -> None:
            self.reset()

        @feature(types.TEXT_DOCUMENT_DID_OPEN)
        def did_open(params: types.DidOpenTextDocumentParams) -> None:
            document = params.text_document
            if (uri := _file(document.uri)) is not None:
                self._spelling[uri] = document.uri
                self.service.open(uri, document.text, document.version)
                self.changed(uri)

        @feature(types.TEXT_DOCUMENT_DID_CHANGE)
        def did_change(params: types.DidChangeTextDocumentParams) -> None:
            if (uri := _file(params.text_document.uri)) is not None:
                # pygls has applied the edits to its own copy.
                document = self.workspace.get_text_document(params.text_document.uri)
                self.service.change(uri, document.source, params.text_document.version)
                self.changed(uri)

        @feature(types.TEXT_DOCUMENT_DID_CLOSE)
        def did_close(params: types.DidCloseTextDocumentParams) -> None:
            if (uri := _file(params.text_document.uri)) is not None:
                self.service.close(uri)
                self.changed(uri)

        @feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
        def watched(params: types.DidChangeWatchedFilesParams) -> None:
            for change in params.changes:
                if (uri := _file(change.uri)) is not None:
                    self.service.changed_on_disk(uri)
                    self.changed(uri)

        @feature(types.TEXT_DOCUMENT_DEFINITION)
        def definition(params: types.DefinitionParams) -> list[types.Location] | None:
            return self._sites(params, queries.definition)

        @feature(types.TEXT_DOCUMENT_REFERENCES)
        def references(params: types.ReferenceParams) -> list[types.Location] | None:
            declaration = params.context.include_declaration
            return self._sites(
                params, lambda s, uri, line, column: queries.references(s, uri, line, column, declaration=declaration)
            )

        @feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
        def highlight(params: types.DocumentHighlightParams) -> list[types.DocumentHighlight] | None:
            if (at := self._at(params)) is None:
                return None
            uri, line, column, positions = at
            found = dict.fromkeys(
                spans
                for snapshot in self.service.snapshots(uri)
                for spans in queries.highlights(snapshot, uri, line, column)
            )
            return [
                types.DocumentHighlight(
                    positions.range(uri, span),
                    types.DocumentHighlightKind.Write if definition else types.DocumentHighlightKind.Read,
                )
                for span, definition in found
            ]

        @feature(types.TEXT_DOCUMENT_HOVER)
        def hover(params: types.HoverParams) -> types.Hover | None:
            if (at := self._at(params)) is None:
                return None
            uri, line, column, positions = at
            for snapshot in self.service.snapshots(uri):
                found = queries.hover(snapshot, uri, line, column)
                if found is not None:
                    text, span = found
                    content = types.MarkupContent(types.MarkupKind.Markdown, text)
                    return types.Hover(content, positions.range(uri, span))
            return None

        @feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
        def document_symbols(params: types.DocumentSymbolParams) -> list[types.DocumentSymbol] | None:
            if (uri := _file(params.text_document.uri)) is None:
                return None
            positions = self.positions()
            snapshot = self.service.snapshots(uri)[0]
            return [self._symbol(positions, symbol) for symbol in queries.document_symbols(snapshot, uri)]

        @feature(types.WORKSPACE_SYMBOL)
        def workspace_symbols(params: types.WorkspaceSymbolParams) -> list[types.WorkspaceSymbol]:
            positions = self.positions()
            snapshots = [self.service.snapshot(root) for root in self.service.roots()]
            return [
                types.WorkspaceSymbol(
                    location=self._location(positions, queries.Site(symbol.uri, symbol.selection)),
                    name=symbol.name,
                    kind=_SYMBOL[symbol.kind],
                )
                for symbol in queries.workspace_symbols(snapshots, params.query)
            ]

        @feature(types.TEXT_DOCUMENT_DOCUMENT_LINK)
        def links(params: types.DocumentLinkParams) -> list[types.DocumentLink] | None:
            if (uri := _file(params.text_document.uri)) is None:
                return None
            positions = self.positions()
            return [
                types.DocumentLink(positions.range(uri, site.span), target=self._client(site.uri))
                for site in queries.links(self.service.snapshots(uri)[0], uri)
            ]

        @feature(types.TEXT_DOCUMENT_FOLDING_RANGE)
        def folding(params: types.FoldingRangeParams) -> list[types.FoldingRange] | None:
            if (uri := _file(params.text_document.uri)) is None:
                return None
            text = self.service.text(uri) or ''
            return [types.FoldingRange(start - 1, end - 1) for start, end in queries.folding_ranges(text, uri)]

        @feature(types.TEXT_DOCUMENT_SELECTION_RANGE)
        def selection(params: types.SelectionRangeParams) -> list[types.SelectionRange] | None:
            if (uri := _file(params.text_document.uri)) is None:
                return None
            positions = self.positions()
            text = self.service.text(uri) or ''
            found: list[types.SelectionRange] = []
            for position in params.positions:
                parent: types.SelectionRange | None = None
                for span in reversed(queries.selection_ranges(text, uri, *positions.to_server(uri, position))):
                    parent = types.SelectionRange(positions.range(uri, span), parent)
                found.append(parent or types.SelectionRange(types.Range(position, position)))
            return found

        @feature(types.TEXT_DOCUMENT_PREPARE_TYPE_HIERARCHY)
        def prepare_hierarchy(params: types.TypeHierarchyPrepareParams) -> list[types.TypeHierarchyItem] | None:
            if (at := self._at(params)) is None:
                return None
            uri, line, column, positions = at
            for snapshot in self.service.snapshots(uri):
                symbol = queries.type_at(snapshot, uri, line, column)
                if symbol is not None:
                    return [self._item(positions, symbol)]
            return None

        @feature(types.TYPE_HIERARCHY_SUPERTYPES)
        def supertypes(params: types.TypeHierarchySupertypesParams) -> list[types.TypeHierarchyItem] | None:
            return self._hierarchy(params.item, queries.supertypes)

        @feature(types.TYPE_HIERARCHY_SUBTYPES)
        def subtypes(params: types.TypeHierarchySubtypesParams) -> list[types.TypeHierarchyItem] | None:
            return self._hierarchy(params.item, queries.subtypes)

        @feature(
            types.TEXT_DOCUMENT_CODE_ACTION,
            types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.QuickFix]),
        )
        def code_actions(params: types.CodeActionParams) -> list[types.CodeAction] | None:
            if (uri := _file(params.text_document.uri)) is None:
                return None
            positions = self.positions()
            text = self.service.text(uri) or ''
            found: list[types.CodeAction] = []
            for diagnostic in params.context.diagnostics:
                # A parser diagnostic cannot be suppressed (docs/21 § 4.1).
                if diagnostic.source != queries.LINT_SOURCE or diagnostic.code is None:
                    continue
                line, _ = positions.to_server(uri, diagnostic.range.start)
                start = types.Position(line - 1, 0)
                edit = types.TextEdit(types.Range(start, start), queries.suppression(text, line, str(diagnostic.code)))
                found.append(
                    types.CodeAction(
                        title=f'Suppress {diagnostic.code} on this line',
                        kind=types.CodeActionKind.QuickFix,
                        diagnostics=[diagnostic],
                        edit=types.WorkspaceEdit(changes={params.text_document.uri: [edit]}),
                    )
                )
            return found

    # -- helpers the handlers share ---------------------------------------------

    def _at(self, params: _AtPosition) -> tuple[str, int, int, _Positions] | None:
        """The file and fastRAML position a request is about."""
        uri = _file(params.text_document.uri)
        if uri is None:
            return None
        positions = self.positions()
        line, column = positions.to_server(uri, params.position)
        return uri, line, column, positions

    def _sites(self, params: _AtPosition, query: SiteQuery) -> list[types.Location] | None:
        """A query answering sites, over every snapshot serving the file, once each."""
        if (at := self._at(params)) is None:
            return None
        uri, line, column, positions = at
        sites = dict.fromkeys(
            site for snapshot in self.service.snapshots(uri) for site in query(snapshot, uri, line, column)
        )
        return [self._location(positions, site) for site in sites]

    def _hierarchy(self, item: types.TypeHierarchyItem, query: TypeQuery) -> list[types.TypeHierarchyItem] | None:
        """Supertypes or subtypes of an item, found again by where its name is
        written: the item may come from an earlier snapshot (docs/21 § 4).
        """
        if (uri := _file(item.uri)) is None:
            return None
        positions = self.positions()
        selection = positions.span(uri, item.selection_range)
        symbol = queries.Symbol(item.name, queries.SymbolKind.TYPE, uri, selection, selection)
        found = {
            (parent.uri, parent.selection): parent
            for snapshot in self.service.snapshots(uri)
            for parent in query(snapshot, symbol)
        }
        return [self._item(positions, parent) for parent in found.values()]


def _plain(value: object) -> object:
    """An `info` value as JSON holds it."""
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
