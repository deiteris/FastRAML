"""The workspace: roots, buffers over the disk, and when a snapshot is stale
(docs/21 § 2).

On disk rather than in memory: finding the roots lists a folder, which only
the sandboxed loader does.
"""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import pytest

from fastraml.config import FastRamlConfig
from fastraml.gctuning import tuned_gc
from fastraml.registry import Raml
from fastraml.service.workspace import Workspace, canonical
from fastraml.uris import path_to_file_uri
from tests.unit.conftest import write_files

if TYPE_CHECKING:
    from pathlib import Path

API = '#%RAML 1.0\ntitle: T\n'
LIBRARY = '#%RAML 1.0 Library\ntypes:\n  User: string\n'


def _workspace(tmp_path: Path, files: dict[str, str], **options: object) -> tuple[Workspace, str]:
    write_files(tmp_path, files)
    folder = path_to_file_uri(tmp_path)
    return Workspace([folder], **options), folder  # type: ignore[arg-type]


def _models() -> int:
    return sum(isinstance(found, Raml) for found in gc.get_objects())


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

    def test_an_editors_spelling_of_a_uri_is_the_parsers(self, tmp_path):
        uri = path_to_file_uri(tmp_path / 'api.raml')
        assert canonical(uri.replace(':', '%3A', 1).replace('file%3A', 'file:')) == uri


class TestSnapshots:
    FILES = {  # noqa: RUF012 - read once per test
        'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  Admin: lib.User\n',
        'lib.raml': LIBRARY,
        'other.raml': API,
    }

    def test_a_file_is_served_from_every_root_that_read_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, self.FILES)
        assert [snapshot.root for snapshot in workspace.snapshots(f'{folder}/lib.raml')] == [f'{folder}/api.raml']

    def test_a_file_no_root_reads_is_parsed_alone(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API, 'lib.raml': LIBRARY})
        (snapshot,) = workspace.snapshots(f'{folder}/lib.raml')
        assert snapshot.root == f'{folder}/lib.raml'
        assert snapshot.error is None

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
        assert {frame.info.get('type') for chain in snapshot.error.chains() for frame in chain} >= {'lib.User'}

    def test_a_buffer_outside_the_folder_is_not_served(self, tmp_path):
        workspace, folder = _workspace(tmp_path / 'inside', {'api.raml': API + 'uses:\n  lib: ../lib.raml\n'})
        outside = path_to_file_uri(tmp_path / 'lib.raml')
        workspace.open(outside, LIBRARY, 1)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        assert snapshot.error is not None
        assert any(
            'outside the workspace root' in frame.message for chain in snapshot.error.chains() for frame in chain
        )

    def test_a_file_that_appears_refreshes_a_snapshot_that_wanted_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {'api.raml': API + 'uses:\n  lib: lib.raml\n'})
        root = f'{folder}/api.raml'
        assert workspace.snapshot(root).error is not None
        workspace.open(f'{folder}/lib.raml', LIBRARY, 1)
        assert workspace.snapshot(root).error is None

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

    def test_a_dropped_snapshot_is_freed_before_the_next_parse(self, tmp_path):
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
            assert _models() - before == 1

    def test_the_yaml_trees_are_kept_only_for_a_lint_rule_that_reads_them(self, tmp_path):
        files = {'api.raml': API}
        reading, folder = _workspace(tmp_path, files)
        assert reading.snapshot(f'{folder}/api.raml').raml.retain_source
        config = FastRamlConfig(lint={'rules': [{'id': 'deprecated-schemas', 'disabled': True}]})
        plain = Workspace([folder], config=config)
        raml = plain.snapshot(f'{folder}/api.raml').raml
        assert (raml.retain_source, raml.retain_text) == (False, True)

    def test_what_a_change_moves_is_every_root_that_read_it(self, tmp_path):
        workspace, folder = _workspace(tmp_path, {**self.FILES, 'second.raml': self.FILES['api.raml']})
        assert workspace.affected(f'{folder}/lib.raml') == [f'{folder}/api.raml', f'{folder}/second.raml']
        assert workspace.affected(f'{folder}/other.raml') == [f'{folder}/other.raml']

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
