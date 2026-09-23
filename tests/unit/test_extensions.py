"""Overlays and Extensions end to end: the chain, the model, namespaces (docs/19).

Each test writes a small workspace and parses the entry document. Diagnostics
are asserted by message key and `info`, located by file name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path, parse_lenient
from fastraml.domains import DomainLocation
from fastraml.parser.fragments import APIFragment, ExtensionFragment, FragmentKind

if TYPE_CHECKING:
    from pathlib import Path

API = '#%RAML 1.0\ntitle: Books\n'
VALIDATE = ParseOptions(unwrap=True, validate=True)


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


def parse(root: Path, files: dict[str, str], entry: str = 'entry.raml', options: ParseOptions = VALIDATE):
    write(root, files)
    return parse_from_path(root / entry, options)


def failure(root: Path, files: dict[str, str], entry: str = 'entry.raml', options: ParseOptions = VALIDATE):
    """The innermost frame of every chain, as (message, info, file name)."""
    with pytest.raises(RamlError) as caught:
        parse(root, files, entry, options)
    return [
        (frames[-1].message, dict(frames[-1].info or {}), frames[-1].location.rsplit('/', 1)[-1])
        for frames in caught.value.chains()
    ]


class TestLoadingTheChain:
    def test_extends_is_required(self, tmp_path):
        head = failure(tmp_path, {'entry.raml': '#%RAML 1.0 Overlay\ntitle: x\n'})
        assert head == [('extends is required', {}, 'entry.raml')]

    def test_extends_must_be_a_string(self, tmp_path):
        head = failure(tmp_path, {'entry.raml': '#%RAML 1.0 Extension\nextends: [a.raml]\n'})
        assert head == [('extends must be a string', {}, 'entry.raml')]

    def test_a_missing_master_is_reported_at_the_entrys_extends(self, tmp_path):
        write(tmp_path, {'entry.raml': '#%RAML 1.0 Extension\nextends: missing.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(tmp_path / 'entry.raml')
        head = caught.value.head
        assert head.message == 'resolve extends'
        assert head.location.endswith('/entry.raml')
        assert (head.position.line, head.position.column) == (2, 10)

    def test_a_cycle_is_reported(self, tmp_path):
        files = {
            'entry.raml': '#%RAML 1.0 Extension\nextends: b.raml\n',
            'b.raml': '#%RAML 1.0 Overlay\nextends: entry.raml\n',
        }
        ((message, info, where),) = failure(tmp_path, files)
        assert message == 'extends cycle'
        assert info['chain'].endswith('entry.raml')
        assert where == 'entry.raml'

    def test_a_library_is_not_a_master(self, tmp_path):
        files = {'entry.raml': '#%RAML 1.0 Extension\nextends: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'}
        assert failure(tmp_path, files) == [
            ('unexpected fragment kind', {'expected': 'API, Overlay or Extension', 'found': 'Library'}, 'lib.raml')
        ]

    def test_the_root_api_must_have_a_title_of_its_own(self, tmp_path):
        # docs/19 § 2: the target tree takes its title from the root API.
        files = {'api.raml': '#%RAML 1.0\n/a:\n', 'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\ntitle: X\n'}
        assert ('title is required', {}, 'api.raml') in failure(tmp_path, files)

    def test_the_chain_is_reached_through_every_master(self, tmp_path):
        files = {
            'api.raml': API + '/a:\n  displayName: A\n',
            'one.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n/a:\n  displayName: B\n',
            'two.raml': '#%RAML 1.0 Extension\nextends: one.raml\n/b:\n',
            'entry.raml': '#%RAML 1.0 Extension\nextends: two.raml\n/a:\n  displayName: C\n',
        }
        raml = parse(tmp_path, files)
        assert [(e.location.rsplit('/', 1)[-1], e.kind, e.position) for e in raml.extensions] == [
            ('one.raml', FragmentKind.OVERLAY, 1),
            ('two.raml', FragmentKind.EXTENSION, 2),
            ('entry.raml', FragmentKind.EXTENSION, 3),
        ]
        assert raml.endpoints['/a'].display_name.value == 'C'
        assert '/b' in raml.endpoints


class TestResultModel:
    def test_the_entry_point_is_the_root_apis_target_tree(self, tmp_path):
        files = {
            'api.raml': API + '/books:\n  get:\n',
            'entry.raml': '#%RAML 1.0 Extension\nusage: admin\nextends: api.raml\n/books:\n  post:\n',
        }
        raml = parse(tmp_path, files)
        assert isinstance(raml.entry_point, APIFragment)
        assert raml.location.endswith('/api.raml')
        assert set(raml.endpoints['/books'].operations) == {'get', 'post'}
        extension = raml.extensions[0]
        assert isinstance(extension, ExtensionFragment)
        assert extension.usage.value == 'admin'
        assert extension.extends.endswith('/api.raml')
        assert raml.get_fragment(extension.location) is extension

    def test_an_entity_is_located_in_the_file_that_wrote_it(self, tmp_path):
        files = {
            'api.raml': API + '/books:\n  description: master\n  get:\n',
            'entry.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n/books:\n  description: translated\n',
        }
        endpoint = parse(tmp_path, files).endpoints['/books']
        assert endpoint.description.value == 'translated'
        assert endpoint.description.location.endswith('/entry.raml')
        assert endpoint.operations['get'].location.endswith('/api.raml')

    def test_an_include_resolves_relative_to_the_document_that_wrote_it(self, tmp_path):
        files = {
            'api.raml': API,
            'overlays/docs/intro.md': 'Hola',
            'overlays/entry.raml': (
                '#%RAML 1.0 Overlay\nextends: ../api.raml\n'
                'documentation:\n  - title: Intro\n    content: !include docs/intro.md\n'
            ),
        }
        raml = parse(tmp_path, files, 'overlays/entry.raml', ParseOptions(workspace_root=tmp_path))
        assert raml.entry_point.documentation[0].content.value == 'Hola'

    def test_an_unknown_root_key_is_reported_in_the_extension(self, tmp_path):
        files = {'api.raml': API, 'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\nhi: 1\n'}
        assert failure(tmp_path, files) == [('unknown field', {'field': 'hi'}, 'entry.raml')]


class TestOverlayRestrictions:
    def test_a_violation_fails_the_parse(self, tmp_path):
        files = {'api.raml': API + 'version: 1\n', 'entry.raml': '#%RAML 1.0 Overlay\nextends: api.raml\nversion: 2\n'}
        assert failure(tmp_path, files) == [
            ('not allowed in an overlay', {'field': 'version', 'change': 'changed'}, 'entry.raml')
        ]

    def test_each_overlay_is_checked_against_the_tree_it_is_applied_to(self, tmp_path):
        # An Extension earlier in the chain may add what a later Overlay then describes.
        files = {
            'api.raml': API,
            'ext.raml': '#%RAML 1.0 Extension\nextends: api.raml\n/new:\n',
            'entry.raml': '#%RAML 1.0 Overlay\nextends: ext.raml\n/new:\n  description: d\n',
        }
        assert parse(tmp_path, files).endpoints['/new'].description.value == 'd'


class TestVisibility:
    def test_the_root_api_cannot_name_what_only_an_extension_declares(self, tmp_path):
        # docs/19 § 5.2: the spec's "Master Tree ... validated".
        files = {
            'api.raml': API + '/a:\n  get:\n    body:\n      application/json:\n        type: Foo\n',
            'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  Foo: string\n',
        }
        messages = [(message, where) for message, _, where in failure(tmp_path, files)]
        assert ('reference not found', 'api.raml') in messages

    def test_an_extension_names_its_own_and_its_masters_declarations(self, tmp_path):
        files = {
            'api.raml': API + 'types:\n  User: {properties: {name: string}}\n',
            'entry.raml': (
                '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  Admin: {type: User}\n'
                '/admins:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n'
                '            type: Admin\n'
            ),
        }
        raml = parse(tmp_path, files)
        body = raml.endpoints['/admins'].operations['get'].responses['200'].bodies['application/json']
        assert body.shape.inherits[0].name == 'Admin'

    def test_an_earlier_document_cannot_name_a_later_documents_declaration(self, tmp_path):
        files = {
            'api.raml': API,
            'one.raml': '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  A: Later\n',
            'entry.raml': '#%RAML 1.0 Extension\nextends: one.raml\ntypes:\n  Later: string\n',
        }
        messages = [(message, where) for message, _, where in failure(tmp_path, files)]
        assert ('reference not found', 'one.raml') in messages

    def test_an_extension_may_use_its_masters_library_prefix(self, tmp_path):
        files = {
            'lib.raml': '#%RAML 1.0 Library\ntypes:\n  T: string\n',
            'api.raml': API + 'uses:\n  lib: lib.raml\n',
            'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  B: lib.T\n',
        }
        assert parse(tmp_path, files).entry_point.types['B'] is not None

    def test_one_prefix_for_two_libraries_is_a_conflict(self, tmp_path):
        # Spec: the trees "MUST NOT have uses instructions with the same
        # namespace referring to different files".
        files = {
            'one.raml': '#%RAML 1.0 Library\ntypes:\n  T: string\n',
            'two.raml': '#%RAML 1.0 Library\ntypes:\n  T: integer\n',
            'api.raml': API + 'uses:\n  lib: one.raml\n',
            'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\nuses:\n  lib: two.raml\n',
        }
        ((message, info, where),) = failure(tmp_path, files)
        assert (message, info['library'], where) == ('library namespace conflict', 'lib', 'entry.raml')

    def test_the_same_library_under_the_same_prefix_is_not_a_conflict(self, tmp_path):
        files = {
            'lib.raml': '#%RAML 1.0 Library\nannotationTypes:\n  note: string\n',
            'api.raml': API + 'uses:\n  lib: lib.raml\n/a:\n',
            'entry.raml': '#%RAML 1.0 Overlay\nextends: api.raml\nuses:\n  lib: lib.raml\n/a:\n  (lib.note): hi\n',
        }
        assert parse(tmp_path, files).endpoints['/a'].annotations['lib.note'].value.raw == 'hi'

    def test_a_scheme_an_extension_declares_secures_the_whole_api(self, tmp_path):
        files = {
            'api.raml': API + '/a:\n  get:\n',
            'entry.raml': (
                '#%RAML 1.0 Extension\nextends: api.raml\n'
                'securitySchemes:\n  basic:\n    type: Basic Authentication\nsecuredBy: [basic]\n'
            ),
        }
        operation = parse(tmp_path, files).endpoints['/a'].operations['get']
        assert [scheme.name for scheme in operation.secured_by] == ['basic']


class TestRootAnnotations:
    ANNOTATION = 'annotationTypes:\n  note:\n    type: string\n    allowedTargets: {targets}\n'

    def test_an_annotation_at_an_overlay_root_targets_the_overlay(self, tmp_path):
        files = {
            'api.raml': API + self.ANNOTATION.format(targets='Overlay'),
            'entry.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n(note): hi\n',
        }
        extension = parse(tmp_path, files).entry_point.annotations['note']
        assert extension.target is DomainLocation.OVERLAY

    def test_an_api_only_annotation_is_rejected_there(self, tmp_path):
        files = {
            'api.raml': API + self.ANNOTATION.format(targets='API'),
            'entry.raml': '#%RAML 1.0 Extension\nextends: api.raml\n(note): hi\n',
        }
        messages = [message for message, _, _ in failure(tmp_path, files)]
        assert 'annotation not allowed at this target' in messages


class TestLenient:
    def test_an_unloadable_chain_raises(self, tmp_path):
        write(tmp_path, {'entry.raml': '#%RAML 1.0 Overlay\nextends: missing.raml\n'})
        with pytest.raises(RamlError):
            parse_lenient(tmp_path / 'entry.raml')

    def test_a_violation_returns_the_partial_api(self, tmp_path):
        write(tmp_path, {'api.raml': API + '/a:\n', 'entry.raml': '#%RAML 1.0 Overlay\nextends: api.raml\n/b:\n'})
        raml, error = parse_lenient(tmp_path / 'entry.raml')
        assert error is not None
        assert error.head.message == 'not allowed in an overlay'
        assert isinstance(raml.entry_point, APIFragment)
