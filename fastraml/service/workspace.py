"""The workspace: open buffers over the files on disk, the roots, and one
snapshot per root (docs/21 § 2).

A root is a document nothing else includes: an API, an Overlay or an
Extension, found by its header (docs/03 § 3), or the files `roots` names. Each
root is parsed leniently with every buffer visible, and its snapshot records
the files it read, so a change to one of them makes that snapshot stale. A
file no root reads is parsed on its own.

Parsing is lazy: a snapshot is built when a query asks for it and reused
until something it read changes.

A dropped snapshot is cyclic garbage the size of a model, and a server defers
full collections for its whole run (docs/12 § 6), so nothing would free it.
The next parse collects it first: one full collection per edit, and no second
model alive while the next is built (docs/21 § 2).
"""

from __future__ import annotations

import gc
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Final

from fastraml.errors import RamlError
from fastraml.loaders import SafeFileLoader
from fastraml.parser.entry import ParseOptions, parse_lenient
from fastraml.parser.fragments import FragmentKind, identify_fragment
from fastraml.service.text import Lines
from fastraml.uris import file_uri_to_path, path_to_file_uri, relative_to
from fastraml.views.lint import Linter, builtin_registry, discover_plugins, parse_config
from fastraml.views.occurrences import build_occurrences

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from fastraml.config import FastRamlConfig
    from fastraml.registry import Raml
    from fastraml.views.lint import Finding
    from fastraml.views.occurrences import Occurrences

__all__ = ['Buffer', 'Snapshot', 'Workspace', 'canonical']

#: The documents that are parsed as roots: nothing includes them.
ROOT_KINDS: Final = frozenset({FragmentKind.API, FragmentKind.OVERLAY, FragmentKind.EXTENSION})

#: A byte order mark, which a buffer may still hold.
BOM: Final = chr(0xFEFF)

#: Enough of a file to read its header line.
_HEAD_BYTES: Final = 256


def canonical(uri: str) -> str:
    """One spelling of a `file://` URI, as the parser writes it.

    Editors percent-encode differently (`file:///c%3A/api.raml`), and every
    lookup here is by URI.
    """
    return path_to_file_uri(file_uri_to_path(uri))


@dataclass(slots=True, eq=False)
class Buffer:
    """An open document's text, as the editor holds it, and its lines, split
    when a position is first converted in this version.
    """

    text: str
    version: int
    _lines: Lines | None = field(default=None, repr=False)

    @property
    def lines(self) -> Lines:
        if self._lines is None:
            self._lines = Lines(self.text)
        return self._lines


@dataclass(slots=True, eq=False)
class Snapshot:
    """One lenient parse of one root, and what it read.

    `raml` is `None` when the entry itself could not be read as RAML; `error`
    then says why. Findings are the second tier (docs/21 § 2): computed on
    first request, and only on an unwrapped model.
    """

    root: str
    raml: Raml | None
    error: RamlError | None
    #: Every file the parse read or tried to read.
    read: frozenset[str]
    linter: Linter | None = None
    _occurrences: Occurrences | None = field(default=None, repr=False)
    _findings: list[Finding] | None = field(default=None, repr=False)

    @property
    def occurrences(self) -> Occurrences | None:
        if self._occurrences is None and self.raml is not None:
            self._occurrences = build_occurrences(self.raml)
        return self._occurrences

    @property
    def findings(self) -> list[Finding]:
        """The lint findings, or none where the model did not reach unwrap."""
        if self._findings is None:
            raml, linter = self.raml, self.linter
            self._findings = linter.run(raml) if raml is not None and raml.unwrapped and linter is not None else []
        return self._findings


class _Overlay:
    """The loader a parse reads through: open buffers first, then the disk.

    A buffer is served only where the sandbox would serve its file, so an open
    editor tab does not widen what a document may include.
    """

    __slots__ = ('_buffers', '_disk')

    def __init__(self, buffers: Mapping[str, Buffer], disk: SafeFileLoader) -> None:
        self._buffers = buffers
        self._disk = disk

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        buffer = self._buffers.get(uri)
        if buffer is not None and self._disk.contains(uri):
            data = buffer.text.encode('utf-8')
            return data if max_bytes is None else data[: max_bytes + 1]
        return self._disk.load(uri, max_bytes=max_bytes)


class Workspace:
    """The buffers, roots and snapshots of one editor session."""

    def __init__(
        self,
        folders: Sequence[str],
        *,
        config: FastRamlConfig | None = None,
        roots: Sequence[str] = (),
        http_client: object | None = None,
    ) -> None:
        """`folders` are the workspace folders' URIs; each is a sandbox root.
        `roots`, when given, are globs over paths relative to a folder, and
        replace discovery (docs/21 § 2).
        """
        from fastraml.config import FastRamlConfig  # noqa: PLC0415 - only for the default

        self.config = config or FastRamlConfig()
        self._folders = sorted((canonical(folder).rstrip('/') + '/' for folder in folders), key=len, reverse=True)
        self._disks = {folder: SafeFileLoader(file_uri_to_path(folder)) for folder in self._folders}
        self._globs = tuple(roots)
        self._http_client = http_client
        self.buffers: dict[str, Buffer] = {}
        self._roots: list[str] | None = None
        self._snapshots: dict[str, Snapshot] = {}
        #: Whether a snapshot was dropped since the last collection.
        self._garbage = False
        self._linter: Linter | None = None

    # -- buffers --------------------------------------------------------------

    def open(self, uri: str, text: str, version: int) -> None:
        self._put(canonical(uri), Buffer(text, version))

    def change(self, uri: str, text: str, version: int) -> None:
        self._put(canonical(uri), Buffer(text, version))

    def close(self, uri: str) -> None:
        uri = canonical(uri)
        if self.buffers.pop(uri, None) is not None:
            # The file on disk may say otherwise than the buffer did.
            self._classify(uri)
            self._touched(uri, appeared=False)

    def changed_on_disk(self, uri: str) -> None:
        """A file was created, changed or deleted outside the editor."""
        self._roots = None
        self._touched(canonical(uri), appeared=True)

    def text(self, uri: str) -> str | None:
        """The text a parse reads for `uri`: its buffer, or else the file, as
        a current snapshot kept it or as the disk holds it.
        """
        uri = canonical(uri)
        buffer = self.buffers.get(uri)
        if buffer is not None:
            return buffer.text
        for snapshot in self._snapshots.values():
            if snapshot.raml is not None and (kept := snapshot.raml.source_texts.get(uri)) is not None:
                return kept
        disk = self._disk(uri)
        if disk is None:
            return None
        try:
            return disk.load(uri).decode('utf-8-sig')
        except (OSError, UnicodeDecodeError):
            return None

    def lines(self, uri: str) -> Lines:
        """The lines of `uri`'s text, for converting positions in it: split once
        per version of a buffer, and on each call for any other file.
        """
        buffer = self.buffers.get(canonical(uri))
        return buffer.lines if buffer is not None else Lines(self.text(uri) or '')

    def _put(self, uri: str, buffer: Buffer) -> None:
        """Hold `buffer`, and drop what read other text for `uri`.

        A buffer opened on its file's text changes nothing a parse reads. One
        for a file no parse could read may be what a failed include was
        looking for; dropping every failed snapshot on each open instead
        reparsed 437 of the TCK's 1011 roots.
        """
        old = self.buffers.get(uri)
        before = old.text if old is not None else self.text(uri)
        self.buffers[uri] = buffer
        if old is None or _head(old.text) != _head(buffer.text):
            self._classify(uri)
        if before != buffer.text:
            self._touched(uri, appeared=before is None)

    def _classify(self, uri: str) -> None:
        """Whether `uri` is a root, decided again by its header, without
        listing the folders again: opening a file in a thousand-file folder
        otherwise reads every header. `roots` globs do not read headers.
        """
        if self._roots is None or self._globs:
            return
        if self._is_root(uri) != (uri in self._roots):
            self._roots = sorted({*self._roots} ^ {uri})

    def _touched(self, uri: str, *, appeared: bool) -> None:
        """Drop every snapshot that read `uri`.

        A file that appeared may be one a failed include was looking for, so a
        snapshot that ended in an error is dropped then too.
        """
        for root, snapshot in list(self._snapshots.items()):
            if uri in snapshot.read or (appeared and snapshot.error is not None):
                del self._snapshots[root]
                self._garbage = True

    # -- roots and snapshots --------------------------------------------------

    def roots(self) -> list[str]:
        """Every root document, in path order."""
        if self._roots is None:
            self._roots = self._discover()
        return self._roots

    def snapshots(self, uri: str) -> list[Snapshot]:
        """The snapshots `uri` is served from: every root that read it, or a
        parse of `uri` alone when none did.
        """
        uri = canonical(uri)
        found = [snapshot for snapshot in map(self.snapshot, self.roots()) if uri in snapshot.read]
        return found or [self.snapshot(uri)]

    def snapshot(self, root: str) -> Snapshot:
        """The current snapshot of `root`, parsed now if it is stale."""
        root = canonical(root)
        snapshot = self._snapshots.get(root)
        if snapshot is None:
            self._collect()
            snapshot = self._snapshots[root] = self._parse(root)
        return snapshot

    def _collect(self) -> None:
        """Free the snapshots dropped since the last parse.

        Whether or not automatic collection is on: the host's switch governs
        when the collector runs by itself, and this frees what the service
        itself discarded.
        """
        if self._garbage:
            gc.collect()
            self._garbage = False

    def readers(self) -> dict[str, list[str]]:
        """Every file a root read, with the roots that read it: those whose
        diagnostics a change to the file can move.
        """
        found: dict[str, list[str]] = {}
        for root in self.roots():
            for uri in self.snapshot(root).read:
                found.setdefault(uri, []).append(root)
        return found

    def _parse(self, root: str) -> Snapshot:
        disk = self._disk(root)
        if disk is None:
            refused = RamlError.new('path is outside the workspace root', root, info={'path': root})
            return Snapshot(root, None, refused, frozenset({root}))
        parser = self.config.parser
        options = ParseOptions(
            unwrap=True,
            validate=True,
            # The index needs the texts; the YAML trees only for a lint rule
            # that reads them.
            retain_text=True,
            retain_source=self._lint_reads_source,
            workspace_root=disk.root,
            max_include_size=parser.max_include_size,
            file_loader=_Overlay(self.buffers, disk),
            http_client=self._http_client,
            regex_engine=parser.regex_engine,
            max_depth=parser.max_depth,
        )
        try:
            raml, error = parse_lenient(file_uri_to_path(root), options)
        except (RamlError, OSError) as err:
            failure = err if isinstance(err, RamlError) else RamlError.new(str(err), root)
            return Snapshot(root, None, failure, frozenset({root}))
        return Snapshot(root, raml, error, _read(raml, root), linter=self.linter)

    @property
    def _lint_reads_source(self) -> bool:
        return any(getattr(rule, 'requires_source', False) for rule in self.linter.rules)

    @property
    def linter(self) -> Linter:
        """The linter the configuration's `lint:` section describes."""
        if self._linter is None:
            import yaml  # noqa: PLC0415 - the lint section's hand-off, as the CLI does it

            registry = builtin_registry()
            plugins = discover_plugins(registry)
            config = parse_config(yaml.safe_dump(dict(self.config.lint)), registry, plugins=plugins)
            self._linter = Linter(registry, config)
        return self._linter

    def _disk(self, uri: str) -> SafeFileLoader | None:
        """The sandbox of the innermost folder holding `uri`."""
        for folder in self._folders:
            if uri.startswith(folder):
                return self._disks[folder]
        return None

    def _discover(self) -> list[str]:
        found: list[str] = []
        for folder in reversed(self._folders):
            candidates = self._disks[folder].files('.raml')
            if self._globs:
                found += [uri for uri in candidates if any(fnmatch(relative_to(uri, folder), g) for g in self._globs)]
            else:
                found += [uri for uri in candidates if self._is_root(uri)]
        # Buffers not yet saved are documents too.
        found += [uri for uri in self.buffers if uri not in found and not self._globs and self._is_root(uri)]
        return sorted(set(found))

    def _is_root(self, uri: str) -> bool:
        buffer = self.buffers.get(uri)
        if buffer is not None:
            head = buffer.text
        else:
            disk = self._disk(uri)
            try:
                head = '' if disk is None else disk.load(uri, max_bytes=_HEAD_BYTES).decode('utf-8-sig', 'replace')
            except OSError:
                return False
        return identify_fragment(_head(head)) in ROOT_KINDS


def _head(text: str) -> str:
    """The header line, as the parser matches it (docs/03 § 3), from the first
    `_HEAD_BYTES` only: a buffer's is compared on every change.
    """
    return text[:_HEAD_BYTES].lstrip(BOM).split('\n', 1)[0].rstrip()


def _read(raml: Raml, root: str) -> frozenset[str]:
    """What a parse read: every file whose text it kept, and every include it
    tried, found or not.
    """
    read: set[str] = {root, *raml.source_texts, *raml.fragments}
    for refs in raml.include_refs.values():
        read.update(ref.abs_uri for ref in refs)
    return frozenset(_files(read))


def _files(uris: Iterable[str]) -> Iterable[str]:
    """Without the `#fragment` an include may carry."""
    return (uri.split('#', 1)[0] for uri in uris)
