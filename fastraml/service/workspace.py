"""The workspace: open buffers over the files on disk, the roots, and one
snapshot per root (docs/21 § 2).

A root is a document nothing else includes: an API, an Overlay or an
Extension, found by its header (docs/03 § 3), or the files `roots` names. Each
root is parsed leniently with every buffer visible, and its snapshot records
the files it read, so a change to one of them makes that snapshot stale. A
file no root reads is parsed on its own.

Parsing is lazy: a snapshot is built when a query asks for it and reused
until something it read changes, and a query brings current only the roots
it reads (`serving`).

A dropped snapshot is cyclic garbage the size of a model, and a server defers
full collections for its whole run (docs/12 § 6), so nothing would free it.
The host calls `collect` between a change and the parse it leads to; a parse
never collects (docs/21 § 2).
"""

from __future__ import annotations

import gc
from codecs import getincrementaldecoder
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Final
from weakref import ref

from fastraml.errors import ErrorKind, RamlError
from fastraml.loaders import SafeFileLoader
from fastraml.parser.entry import ParseOptions, parse_lenient
from fastraml.parser.fragments import FragmentKind, identify_fragment
from fastraml.service.hover import Hover
from fastraml.service.lenses import EffectiveViews
from fastraml.service.source import Sources, original_tree
from fastraml.service.text import Lines
from fastraml.uris import file_uri_to_path, path_to_file_uri, relative_to
from fastraml.views.lint import configured_linter
from fastraml.views.occurrences import build_occurrences
from fastraml.yamlnode import backend_name, read_head

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from weakref import ReferenceType

    from fastraml.config import FastRamlConfig
    from fastraml.registry import Raml
    from fastraml.views.lint import Finding, Linter
    from fastraml.views.occurrences import Occurrences
    from fastraml.yamlnode import Node

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
    _hover: Hover | None = field(default=None, repr=False)
    _effective_views: EffectiveViews | None = field(default=None, repr=False)
    sources: ReferenceType[Sources] | None = field(default=None, repr=False)
    source_generation: int = field(default=0, repr=False)
    source_backend: str = field(default='', repr=False)

    @property
    def effective_views(self) -> EffectiveViews | None:
        if self._effective_views is None and self.raml is not None:
            self._effective_views = EffectiveViews(self.raml)
        return self._effective_views

    @property
    def hover(self) -> Hover | None:
        """The source and model indices used by author-facing hover."""
        occurrences = self.occurrences
        if self._hover is None and self.raml is not None and occurrences is not None:
            self._hover = Hover(
                self.raml,
                self.root,
                occurrences,
                sources=self.sources,
                source_generation=self.source_generation,
                source_backend=self.source_backend,
            )
        return self._hover

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
        linter: Linter | None = None,
    ) -> None:
        """`folders` are the workspace folders' URIs; each is a sandbox root.
        `roots`, when given, are globs over paths relative to a folder, and
        replace discovery (docs/21 § 2). `linter`, when given, is the one
        `config`'s `lint:` section describes, already built.
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
        self._sources = Sources(self.config.parser.max_depth)
        #: What each dropped snapshot read: the order `serving` tries roots in.
        self._last_read: dict[str, frozenset[str]] = {}
        #: Whether a snapshot was dropped since the last collection.
        self._garbage = False
        self._linter = linter

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

    def source(self, uri: str) -> Node | None:
        """The composed tree of `uri`'s current text, composed once per text (docs/21 § 4)."""
        uri = canonical(uri)
        text = self.text(uri)
        if text is None:
            return None
        depth = self.config.parser.max_depth
        self._sources.configure(depth)
        original = None
        normalized = text.removeprefix(BOM)
        for snapshot in self._snapshots.values():
            raml = snapshot.raml
            if (
                raml is not None
                and raml.retain_source
                and raml.max_depth == depth
                and snapshot.source_backend == self._sources.backend
                and raml.source_texts.get(uri) == normalized
            ):
                original = original_tree(uri, raml.source_nodes.get(uri))
                if original is not None:
                    break
        return self._sources.node(uri, text, max_depth=depth, original=original)

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
        read = False
        for root, snapshot in list(self._snapshots.items()):
            read |= uri in snapshot.read
            if uri in snapshot.read or (appeared and snapshot.error is not None):
                del self._snapshots[root]
                self._last_read[root] = snapshot.read
                self._garbage = True
        self._sources.discard(uri, read=read)

    # -- roots and snapshots --------------------------------------------------

    def roots(self) -> list[str]:
        """Every root document, in path order."""
        if self._roots is None:
            self._roots = self._discover()
        return self._roots

    def serving(self, uri: str) -> Iterator[Snapshot]:
        """The snapshots `uri` is served from, lazily (docs/21 § 2): its own
        root's first, then every other root that reads it, then a parse of
        `uri` alone when none does.

        A root is brought current only when the iteration reaches it. Which
        roots read `uri` is known only from their snapshots, so the roots that
        read it when last parsed are tried first: after an edit, the first
        root parsed is one that reads the file.
        """
        uri = canonical(uri)
        served = False
        for root in sorted(self.roots(), key=lambda root: self._order(root, uri)):
            snapshot = self._current(root)
            if uri in snapshot.read:
                served = True
                yield snapshot
        if not served:
            yield self._current(uri)

    def _order(self, root: str, uri: str) -> int:
        """Where `root` is tried in `serving(uri)`: `uri`'s own root, then the
        roots known to read it, those not parsed yet, and those known not to.
        """
        if root == uri:
            return 0
        snapshot = self._snapshots.get(root)
        read = snapshot.read if snapshot is not None else self._last_read.get(root)
        if read is None:
            return 2
        return 1 if uri in read else 3

    def snapshot(self, root: str) -> Snapshot:
        """The current snapshot of `root`, parsed now if it is stale."""
        return self._current(canonical(root))

    def _current(self, root: str) -> Snapshot:
        """`snapshot` for a URI already canonical, as every root is: parsing
        one to spell it again cost 13 ms per request over the TCK's 1011.
        """
        snapshot = self._snapshots.get(root)
        if snapshot is None:
            snapshot = self._snapshots[root] = self._parse(root)
        return snapshot

    def collect(self) -> None:
        """Free the snapshots dropped since the last call (docs/21 § 2).

        Whether or not automatic collection is on: the host's switch governs
        when the collector runs by itself, and this frees what the service
        itself discarded. The host calls it between a change and the parse it
        leads to, outside any request: a full collection over a large
        workspace costs more than most requests.
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
            for uri in self._current(root).read:
                found.setdefault(uri, []).append(root)
        return found

    def _parse(self, root: str) -> Snapshot:
        self._sources.configure(self.config.parser.max_depth)
        source_backend = backend_name()
        disk = self._disk(root)
        if disk is None:
            refused = RamlError.new('path is outside the workspace root', root, info={'path': root})
            return Snapshot(root, None, refused, frozenset({root}))
        options = self.config.parser.limits(
            ParseOptions(
                unwrap=True,
                validate=True,
                # The index needs the texts; the YAML trees only for a lint
                # rule that reads them.
                retain_text=True,
                retain_source=self.linter.requires_source,
                workspace_root=disk.root,
                file_loader=_Overlay(self.buffers, disk),
                http_client=self._http_client,
            )
        )
        try:
            raml, error = parse_lenient(file_uri_to_path(root), options)
        except (RamlError, OSError) as err:
            # An `OSError` is keyed by its errno, as `_open` keys one (docs/11 § 6).
            failure = (
                err
                if isinstance(err, RamlError)
                else RamlError.wrap('load resource', err, root, kind=ErrorKind.READING)
            )
            return Snapshot(root, None, failure, frozenset({root}))
        return Snapshot(
            root,
            raml,
            error,
            _read(raml, root),
            linter=self.linter,
            sources=ref(self._sources),
            source_generation=self._sources.generation,
            source_backend=source_backend,
        )

    @property
    def linter(self) -> Linter:
        """The linter the configuration's `lint:` section describes, or the
        one the host built from it (`configured_linter`).
        """
        if self._linter is None:
            self._linter = configured_linter(self.config.lint)
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
                decoded = '' if disk is None else _decoded_head(disk.load(uri, max_bytes=_HEAD_BYTES))
            except OSError:
                return False
            if decoded is None:
                return False
            head = decoded
        return identify_fragment(_head(head)) in ROOT_KINDS


def _decoded_head(data: bytes) -> str | None:
    """A file's first bytes as text, or `None` where they are not UTF-8, which
    the parser rejects (`decode_source`). A character the head read cuts is
    held back, not an error; bytes past the head are not read, so they are
    the parser's to reject.

    Final only where the loader returned fewer bytes than asked for: that is
    the end of the file whether it honours the extra byte `max_bytes` allows
    or stops at the limit.
    """
    try:
        return getincrementaldecoder('utf-8')().decode(data, final=len(data) < _HEAD_BYTES)
    except UnicodeDecodeError:
        return None


def _head(text: str) -> str:
    """The header line, as the parser matches it (docs/03 § 3), from the first
    `_HEAD_BYTES` only: a buffer's is compared on every change. One byte order
    mark goes, as `decode_source` drops one, and the line is `read_head`'s.
    """
    return read_head(text[:_HEAD_BYTES].removeprefix(BOM))


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
