"""The workspace: roots, buffers over the disk, and when a snapshot is stale
(docs/21 § 2).

On disk rather than in memory: finding the roots lists a folder, which only
the sandboxed loader does.
"""

from __future__ import annotations

import errno
import gc
from typing import TYPE_CHECKING

import pytest

from fastraml.config import FastRamlConfig
from fastraml.errors import ErrorKind, RamlError
from fastraml.gctuning import tuned_gc
from fastraml.registry import Raml
from fastraml.service.queries import folding_ranges_of
from fastraml.service.workspace import _HEAD_BYTES, BOM, Workspace, _head, canonical
from fastraml.uris import path_to_file_uri
from fastraml.yamlnode import decode_source, read_head
from tests.diagnostics import leaves, traces
from tests.unit.conftest import write_files

if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.conftest import MemoryWorkspace

API = '#%RAML 1.0\ntitle: T\n'
LIBRARY = '#%RAML 1.0 Library\ntypes:\n  User: string\n'


def _workspace(tmp_path: Path, files: dict[str, str], **options: object) -> tuple[Workspace, str]:
    write_files(tmp_path, files)
    folder = path_to_file_uri(tmp_path)
    return Workspace([folder], **options), folder  # type: ignore[arg-type]


def _models() -> int:
    return sum(isinstance(found, Raml) for found in gc.get_objects())


def _parses(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The roots `workspace` parses from now on, in order."""
    parsed: list[str] = []
    parse = workspace._parse

    def counted(root: str) -> object:
        parsed.append(root)
        return parse(root)

    monkeypatch.setattr(workspace, '_parse', counted)
    return parsed


class TestRoots:
    def test_a_document_nothing_includes_is_a_root(self, tmp_path):
        workspace, folder = _workspace(
            tmp_path,
            {
                'api.raml': API,
                'overlay.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n',
                'extension.raml': '#%RAML 1.0 Extension\nextends: api.raml\n',
                'lib.raml': LIBRARY,
                'type.raml': '#%RAML 1.0 DataType\ntype: string\n',
            },
        )
        assert workspace.roots() == [f'{folder}/{name}' for name in ('api.raml', 'extension.raml', 'overlay.raml')]

    def test_globs_replace_discovery(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'main/root.raml': API}, roots=['main/*.raml'])
        assert workspace.roots() == [f'{folder}/main/root.raml']

    def test_a_hidden_folder_is_not_searched(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, '.git/api.raml': API})
        assert workspace.roots() == [f'{folder}/api.raml']

    def test_a_buffer_whose_header_changes_changes_the_roots(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        assert workspace.roots() == [f'{folder}/api.raml']
        workspace.open(f'{folder}/lib.raml', API, 1)
        assert workspace.roots() == [f'{folder}/api.raml', f'{folder}/lib.raml']

    def test_a_closed_buffer_is_decided_by_its_file_again(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        workspace.open(f'{folder}/lib.raml', API, 1)
        assert workspace.roots() == [f'{folder}/api.raml', f'{folder}/lib.raml']
        workspace.close(f'{folder}/lib.raml')
        assert workspace.roots() == [f'{folder}/api.raml']

    def test_a_buffer_decides_for_its_own_file_without_listing_the_folders(self, tmp_path, monkeypatch):
        # Listing read every header on each open: 0.23 s on the TCK's 1011 files.
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        workspace.roots()
        monkeypatch.setattr(workspace, '_discover', lambda: pytest.fail('the folders were listed again'))
        workspace.open(f'{folder}/lib.raml', API, 1)
        workspace.close(f'{folder}/lib.raml')
        assert workspace.roots() == [f'{folder}/api.raml']

    def test_an_editors_spelling_of_a_uri_is_the_parsers(self, tmp_path):
        uri = path_to_file_uri(tmp_path / 'api.raml')
        assert canonical(uri.replace(':', '%3A', 1).replace('file%3A', 'file:')) == uri


class TestHeaderLine:
    """A root is decided by the header line the parser reads (docs/03 § 3)."""

    @pytest.mark.parametrize(
        'text',
        [
            '#%RAML 1.0\ntitle: T\n',
            '#%RAML 1.0 \t\r\ntitle: T\n',
            f'{BOM}#%RAML 1.0\ntitle: T\n',
            f'{BOM}{BOM}#%RAML 1.0\ntitle: T\n',
            '#%RAML 1.0\x0c\ntitle: T\n',
            '#%RAML 1.0\xa0\ntitle: T\n',
        ],
    )
    def test_the_head_is_the_parsers(self, text):
        assert _head(text) == read_head(decode_source(text.encode()))

    @staticmethod
    def _is_root(memory_workspace: MemoryWorkspace, data: bytes) -> bool:
        """Whether a file holding `data` is a root, read in memory: `_is_root`
        only loads, so the folder's loader is swapped for the in-memory one.
        """
        folder = memory_workspace({})
        uri = path_to_file_uri(folder / 'api.raml')
        memory_workspace.files[uri] = data
        workspace = Workspace([path_to_file_uri(folder)])
        workspace._disks = dict.fromkeys(workspace._disks, memory_workspace)  # type: ignore[arg-type]
        return workspace._is_root(canonical(uri))

    def test_a_file_with_a_byte_order_mark_is_a_root(self, memory_workspace):
        assert self._is_root(memory_workspace, (BOM + API).encode())

    def test_a_file_that_is_not_utf8_is_no_root(self, memory_workspace):
        data = API.encode() + b'description: \xff\n'
        folder = memory_workspace({})
        memory_workspace.files[path_to_file_uri(folder / 'api.raml')] = data
        with pytest.raises(RamlError):
            memory_workspace.parse(folder / 'api.raml')
        assert not self._is_root(memory_workspace, data)

    def test_a_character_cut_by_the_head_read_is_not_an_error(self, memory_workspace):
        prefix = (API + 'description: ').encode()
        # A loader returns `_HEAD_BYTES + 1` bytes of a longer file; the last
        # is the first byte of a two-byte `é`.
        prefix += b'x' * ((_HEAD_BYTES - len(prefix)) % 2)
        data = prefix + ('é' * 200 + '\n').encode()
        with pytest.raises(UnicodeDecodeError):
            data[: _HEAD_BYTES + 1].decode('utf-8')
        assert self._is_root(memory_workspace, data)

    def test_a_head_of_exactly_the_limit_is_not_taken_for_the_whole_file(self, memory_workspace):
        # A loader that returns `max_bytes` of a longer file, not the extra
        # byte, has not signalled the end: a character it cuts is held back.
        prefix = (API + 'description: ').encode()
        prefix += b'x' * ((_HEAD_BYTES - 1 - len(prefix)) % 2)
        data = prefix + ('é' * 200 + '\n').encode()
        with pytest.raises(UnicodeDecodeError):
            data[:_HEAD_BYTES].decode('utf-8')

        class Exact:
            def load(self, uri, *, max_bytes=None):
                whole = memory_workspace.load(uri)
                return whole if max_bytes is None else whole[:max_bytes]

        folder = memory_workspace({})
        uri = path_to_file_uri(folder / 'api.raml')
        memory_workspace.files[uri] = data
        workspace = Workspace([path_to_file_uri(folder)])
        workspace._disks = dict.fromkeys(workspace._disks, Exact())  # type: ignore[arg-type]
        assert workspace._is_root(canonical(uri))


class TestSourceTrees:
    """The composed tree of a file's current text, once per text (docs/21 § 4)."""

    STRUCTURE = API + 'types:\n  Thing:\n    properties:\n      name: string\n'

    def test_a_unchanged_text_composes_once(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': self.STRUCTURE})
        uri = f'{folder}/api.raml'
        workspace.open(uri, self.STRUCTURE, 1)
        first = workspace.source(uri)
        assert first is not None
        assert workspace.source(uri) is first

    def test_a_changed_text_composes_again(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': self.STRUCTURE})
        uri = f'{folder}/api.raml'
        workspace.open(uri, self.STRUCTURE, 1)
        first = workspace.source(uri)
        workspace.change(uri, self.STRUCTURE + '  extra:\n    properties:\n      age: integer\n', 2)
        second = workspace.source(uri)
        assert second is not first
        assert len(folding_ranges_of(second)) > len(folding_ranges_of(first))

    def test_a_closed_buffer_composes_again(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': self.STRUCTURE})
        uri = f'{folder}/api.raml'
        workspace.open(uri, self.STRUCTURE, 1)
        first = workspace.source(uri)
        workspace.close(uri)
        assert workspace.source(uri) is not first


class TestSnapshots:
    FILES = {  # noqa: RUF012 - read once per test
        'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  Admin: lib.User\n',
        'lib.raml': LIBRARY,
        'other.raml': API,
    }

    def test_a_file_is_served_from_every_root_that_read_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        assert [snapshot.root for snapshot in workspace.serving(f'{folder}/lib.raml')] == [f'{folder}/api.raml']

    def test_a_root_is_served_by_itself_first(self, tmp_path):
        # `aa.raml` sorts first and reads `api.raml` as an Overlay's master.
        files = {**self.FILES, 'aa.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n'}
        workspace, folder = _workspace(tmp_path, files)
        roots = [snapshot.root for snapshot in workspace.serving(f'{folder}/api.raml')]
        assert roots == [f'{folder}/api.raml', f'{folder}/aa.raml']

    def test_a_file_no_root_reads_is_parsed_alone(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        (snapshot,) = workspace.serving(f'{folder}/lib.raml')
        assert snapshot.root == f'{folder}/lib.raml'
        assert snapshot.error is None

    def test_an_os_error_is_keyed_by_its_errno(self, tmp_path, monkeypatch):
        """docs/11 § 6: an `OSError` past the parse reports a key, not the OS text."""
        workspace, folder = _workspace(tmp_path, {'api.raml': API})

        def refused(path: str, options: object) -> None:
            raise PermissionError(errno.EACCES, 'Access is denied', path)

        monkeypatch.setattr('fastraml.service.workspace.parse_lenient', refused)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        (chain,) = snapshot.error.chains()
        assert [(frame.message, frame.location, frame.kind) for frame in chain] == [
            ('load resource', f'{folder}/api.raml', ErrorKind.READING),
            ('permission denied', f'{folder}/api.raml', ErrorKind.READING),
        ]

    def test_a_snapshot_is_kept_until_a_file_it_read_changes(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        root = f'{folder}/api.raml'
        first = workspace.snapshot(root)
        workspace.change(f'{folder}/other.raml', API + 'version: v1\n', 2)
        assert workspace.snapshot(root) is first
        workspace.change(f'{folder}/lib.raml', LIBRARY + '  Guest: string\n', 2)
        assert workspace.snapshot(root) is not first

    def test_a_buffer_is_read_instead_of_its_file(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        workspace.open(f'{folder}/lib.raml', LIBRARY.replace('User', 'Person'), 1)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        assert {frame.info.get('type') for frame in traces(snapshot.error)} >= {'lib.User'}

    def test_a_buffer_outside_the_folder_is_not_served(self, tmp_path):
        workspace, folder = _workspace(tmp_path / 'inside', {'api.raml': API + 'uses:\n  lib: ../lib.raml\n'})
        outside = path_to_file_uri(tmp_path / 'lib.raml')
        workspace.open(outside, LIBRARY, 1)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        assert [(t.message, t.info['path']) for t in leaves(snapshot.error)] == [
            ('path is outside the workspace root', str(tmp_path / 'lib.raml'))
        ]

    def test_a_file_that_appears_refreshes_a_snapshot_that_wanted_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API + 'uses:\n  lib: lib.raml\n'})
        root = f'{folder}/api.raml'
        assert workspace.snapshot(root).error is not None
        workspace.open(f'{folder}/lib.raml', LIBRARY, 1)
        assert workspace.snapshot(root).error is None

    def test_opening_a_file_on_its_own_text_keeps_every_snapshot(self, tmp_path):
        # Failed ones too: each open reparsed 437 of the TCK's 1011 roots.
        workspace, folder = _workspace(tmp_path, {**self.FILES, 'broken.raml': API + 'types:\n  A: Nope\n'})
        kept = [workspace.snapshot(f'{folder}/{name}') for name in ('api.raml', 'broken.raml')]
        assert kept[1].error is not None
        workspace.open(f'{folder}/lib.raml', LIBRARY, 1)
        workspace.open(f'{folder}/other.raml', API, 1)
        assert [workspace.snapshot(f'{folder}/{name}') for name in ('api.raml', 'broken.raml')] == kept

    def test_a_file_changed_on_disk_is_read_again(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        root = f'{folder}/api.raml'
        first = workspace.snapshot(root)
        (tmp_path / 'lib.raml').write_text(LIBRARY.replace('User', 'Person'), encoding='utf-8')
        workspace.changed_on_disk(f'{folder}/lib.raml')
        assert workspace.snapshot(root) is not first
        assert workspace.snapshot(root).error is not None

    def test_the_text_of_a_uri_is_its_buffer_or_else_its_file(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        assert workspace.text(f'{folder}/lib.raml') == LIBRARY
        workspace.open(f'{folder}/lib.raml', API, 1)
        assert workspace.text(f'{folder}/lib.raml') == API
        assert workspace.text(path_to_file_uri(tmp_path.parent / 'elsewhere.raml')) is None

    def test_a_parse_does_not_wait_for_a_collection(self, tmp_path, monkeypatch):
        # The host collects when idle (docs/21 § 2); a request's parse never does.
        workspace, folder = _workspace(tmp_path, self.FILES)
        root = f'{folder}/api.raml'
        workspace.snapshot(root)
        workspace.change(root, f'{self.FILES["api.raml"]}# 2\n', 2)
        monkeypatch.setattr(gc, 'collect', lambda *_: pytest.fail('a parse collected'))
        assert workspace.snapshot(root).error is None

    def test_collect_frees_the_dropped_snapshots(self, tmp_path):
        # A server defers full collections for its whole run (docs/12 § 6), and
        # a model is cyclic garbage: without the collection, every edit would
        # keep the model it replaced.
        workspace, folder = _workspace(tmp_path, self.FILES)
        root = f'{folder}/api.raml'
        gc.collect()
        before = _models()
        with tuned_gc():
            for version in range(1, 4):
                workspace.change(root, f'{self.FILES["api.raml"]}# {version}\n', version)
                workspace.snapshot(root)
                workspace.collect()
            assert _models() - before == 1

    def test_the_yaml_trees_are_kept_only_for_a_lint_rule_that_reads_them(self, tmp_path):
        files = {'api.raml': API}
        reading, folder = _workspace(tmp_path, files)
        raml = reading.snapshot(f'{folder}/api.raml').raml
        assert (raml.retain_source, raml.retain_text) == (False, True)
        config = FastRamlConfig(lint={'rules': [{'id': 'prefer-array-expression'}]})
        syntax = Workspace([folder], config=config)
        raml = syntax.snapshot(f'{folder}/api.raml').raml
        assert (raml.retain_source, raml.retain_text) == (True, True)

    def test_the_first_snapshot_serving_a_file_parses_one_root(self, tmp_path, monkeypatch):
        # After an edit to a library, the outline read one snapshot but brought
        # every root current first.
        workspace, folder = _workspace(tmp_path, {**self.FILES, 'second.raml': self.FILES['api.raml']})
        workspace.readers()
        workspace.change(f'{folder}/lib.raml', LIBRARY + '  Guest: string\n', 2)
        parsed = _parses(workspace, monkeypatch)
        assert next(workspace.serving(f'{folder}/lib.raml')).root == f'{folder}/api.raml'
        assert parsed == [f'{folder}/api.raml']

    def test_the_roots_that_read_a_file_last_are_tried_first(self, tmp_path, monkeypatch):
        # Both roots read `common.raml`; only `b.raml` reads `lib.raml`. In
        # path order, `a.raml` would be parsed to learn it does not.
        common = '#%RAML 1.0 Library\ntypes:\n  C: string\n'
        files = {
            'a.raml': API + 'uses:\n  common: common.raml\n',
            'b.raml': API + 'uses:\n  common: common.raml\n  lib: lib.raml\n',
            'common.raml': common,
            'lib.raml': LIBRARY,
        }
        workspace, folder = _workspace(tmp_path, files)
        workspace.readers()
        workspace.change(f'{folder}/common.raml', common + '  D: string\n', 2)
        parsed = _parses(workspace, monkeypatch)
        assert next(workspace.serving(f'{folder}/lib.raml')).root == f'{folder}/b.raml'
        assert parsed == [f'{folder}/b.raml']

    def test_every_snapshot_serving_a_file_brings_every_root_current(self, tmp_path, monkeypatch):
        workspace, folder = _workspace(tmp_path, {**self.FILES, 'second.raml': self.FILES['api.raml']})
        parsed = _parses(workspace, monkeypatch)
        served = [snapshot.root for snapshot in workspace.serving(f'{folder}/lib.raml')]
        assert served == [f'{folder}/api.raml', f'{folder}/second.raml']
        assert sorted(parsed) == workspace.roots()

    def test_what_a_change_moves_is_every_root_that_read_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {**self.FILES, 'second.raml': self.FILES['api.raml']})
        readers = workspace.readers()
        assert readers[f'{folder}/lib.raml'] == [f'{folder}/api.raml', f'{folder}/second.raml']
        assert readers[f'{folder}/other.raml'] == [f'{folder}/other.raml']

    def test_a_buffers_lines_are_split_once_per_version(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        uri = f'{folder}/api.raml'
        workspace.open(uri, API, 1)
        first = workspace.lines(uri)
        assert workspace.lines(uri) is first
        workspace.change(uri, API + 'version: v1\n', 2)
        assert workspace.lines(uri) is not first

    def test_a_closed_files_text_is_the_one_its_snapshot_read(self, tmp_path):
        # Positions in a snapshot are in the text it read, even if the disk
        # moved on without a notification.
        workspace, folder = _workspace(tmp_path, self.FILES)
        workspace.snapshot(f'{folder}/api.raml')
        (tmp_path / 'lib.raml').write_text('changed', encoding='utf-8')
        assert workspace.text(f'{folder}/lib.raml') == LIBRARY

    @pytest.mark.parametrize(
        'text',
        [pytest.param('title: no header\n', id='no header'), pytest.param('#%RAML 1.0\n- a list\n', id='not a map')],
    )
    def test_an_entry_that_is_no_raml_has_no_model_and_says_why(self, tmp_path, text):
        workspace, folder = _workspace(tmp_path, {'api.raml': API})
        workspace.change(f'{folder}/api.raml', text, 2)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.raml is None
        assert snapshot.error is not None
