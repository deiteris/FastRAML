"""`!include`: resolution, caching, limits and cycles.

See docs/03-yaml-and-io.md section 4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path, path_to_file_uri
from fastraml.parser.includes import resolve_include, resolve_ref_uri
from fastraml.registry import Raml
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind
from tests.unit.conftest import CountingLoader

if TYPE_CHECKING:
    from pathlib import Path


def include_node(argument: str) -> Node:
    return Node(NodeKind.SCALAR, TAG_INCLUDE, argument, None, 3, 5, 3, 5 + len(argument))


#: These tests hang their `!include`s off annotation keys, because an annotation
#: accepts any value and so makes the smallest document that includes anything.
#: P8 binds every application to a declaration, so the declarations are here.
API = '#%RAML 1.0\ntitle: T\nannotationTypes:\n' + ''.join(f'  {name}: any\n' for name in 'abcde')


class TestUriResolution:
    def test_a_relative_argument_resolves_against_the_including_file(self):
        raml = Raml(workspace_root_uri='file:///w')
        assert resolve_ref_uri(raml, '../b.raml', 'file:///w/x/a.raml') == 'file:///w/b.raml'

    def test_a_raml_absolute_argument_resolves_against_the_workspace_root(self):
        # Not the filesystem root: that rule is what makes an "absolute" RAML
        # path portable between checkouts.
        raml = Raml(workspace_root_uri='file:///w/project')
        assert resolve_ref_uri(raml, '/types/x.raml', 'file:///w/project/a/b.raml') == (
            'file:///w/project/types/x.raml'
        )

    def test_a_url_argument_is_used_as_written(self):
        raml = Raml(workspace_root_uri='file:///w')
        assert resolve_ref_uri(raml, 'https://e.com/t.raml', 'file:///w/a.raml') == 'https://e.com/t.raml'


class TestIncludeContent:
    def test_a_non_yaml_extension_becomes_a_string_scalar(self, workspace):
        # This is how `content: !include legal.md` works.
        root = workspace({'legal.md': '# Terms\n'})
        raml = Raml(loader=CountingLoader(root), workspace_root_uri=path_to_file_uri(root))
        _target, content = resolve_include(raml, include_node('legal.md'), path_to_file_uri(root / 'a.raml'))
        assert (content.kind, content.tag, content.value) == (NodeKind.SCALAR, TAG_STR, '# Terms\n')

    def test_a_yaml_extension_is_composed(self, workspace):
        root = workspace({'t.yaml': 'a: 1\n'})
        raml = Raml(loader=CountingLoader(root), workspace_root_uri=path_to_file_uri(root))
        _target, content = resolve_include(raml, include_node('t.yaml'), path_to_file_uri(root / 'a.raml'))
        assert content.kind is NodeKind.MAPPING

    def test_a_json_pointer_suffix_does_not_hide_the_extension(self, workspace):
        root = workspace({'s.json': '{"a": 1}\n'})
        raml = Raml(loader=CountingLoader(root), workspace_root_uri=path_to_file_uri(root))
        _target, content = resolve_include(
            raml, include_node('s.json#/definitions/A'), path_to_file_uri(root / 'a.raml')
        )
        assert content.kind is NodeKind.MAPPING

    def test_a_non_include_node_is_returned_unchanged(self):
        node = Node(NodeKind.SCALAR, TAG_STR, 'plain')
        target, content = resolve_include(Raml(), node, 'file:///a.raml')
        assert (target, content) == ('', node)


class TestCachingAndLimits:
    def test_a_target_referenced_five_times_is_read_once(self, workspace):
        root = workspace(
            {
                'api.raml': API + '(a): !include shared.yaml\n'
                '(b): !include shared.yaml\n(c): !include shared.yaml\n'
                '(d): !include shared.yaml\n(e): !include shared.yaml\n',
                'shared.yaml': 'k: v\n',
            }
        )
        loader = CountingLoader(root)
        parse_from_path(root / 'api.raml', ParseOptions(file_loader=loader))
        assert loader.counts[path_to_file_uri(root / 'shared.yaml')] == 1

    def test_the_reference_is_recorded_once_per_occurrence(self, workspace):
        # The cache is about I/O; tooling still wants every document link.
        root = workspace(
            {
                'api.raml': API + '(a): !include shared.yaml\n(b): !include shared.yaml\n',
                'shared.yaml': 'k: v\n',
            }
        )
        raml = parse_from_path(root / 'api.raml')
        refs = raml.include_refs_in(path_to_file_uri(root / 'api.raml'))
        assert [ref.path for ref in refs] == ['shared.yaml', 'shared.yaml']
        # Two occurrences, two positions: the point of recording each one.
        assert refs[0].position.line == API.count('\n') + 1
        assert refs[1].position.line == refs[0].position.line + 1

    def test_an_oversized_include_is_rejected_without_being_read_whole(self, workspace):
        root = workspace({'api.raml': API + '(a): !include big.yaml\n', 'big.yaml': 'k: ' + 'x' * 5000})
        loader = CountingLoader(root)
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(file_loader=loader, max_include_size=64))

        assert 'include file exceeds size limit' in caught.value.messages()[0]
        limits = [limit for uri, limit in loader.calls if uri.endswith('big.yaml')]
        assert limits == [64], 'the loader must be asked for limit + 1 bytes, not for the whole file'

    def test_a_limit_of_zero_disables_the_check(self, workspace):
        root = workspace({'api.raml': API + '(a): !include big.yaml\n', 'big.yaml': 'k: ' + 'x' * 5000})
        raml = parse_from_path(root / 'api.raml', ParseOptions(max_include_size=0))
        assert raml.entry_point.annotations['a'].value.raw['k'].endswith('x')


class TestCycles:
    def test_a_scalar_include_cycle_is_reported_with_a_position(self, workspace):
        root = workspace(
            {
                'api.raml': API + '(a): !include one.yaml\n',
                'one.yaml': 'v: !include two.yaml\n',
                'two.yaml': 'v: !include one.yaml\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')

        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'circular include detected'
        assert trace.info['path'] == path_to_file_uri(root / 'one.yaml')
        assert trace.position is not None

    def test_a_diamond_include_is_not_a_cycle(self, workspace):
        root = workspace(
            {
                'api.raml': API + '(a): !include one.yaml\n',
                'one.yaml': 'l: !include leaf.yaml\nr: !include leaf.yaml\n',
                'leaf.yaml': 'v: 1\n',
            }
        )
        raml = parse_from_path(root / 'api.raml')
        assert raml.entry_point.annotations['a'].value.raw == {'l': {'v': 1}, 'r': {'v': 1}}


class TestMissingTargets:
    def test_a_missing_include_names_the_file_it_could_not_read(self, workspace):
        root = workspace({'api.raml': API + '(a): !include gone.yaml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert any('gone.yaml' in message for message in caught.value.messages())

    def test_an_include_outside_the_workspace_is_refused(self, workspace, tmp_path: Path):
        root = workspace({'project/api.raml': API + '(a): !include ../secret.yaml\n'})
        (tmp_path / 'secret.yaml').write_text('k: v\n', encoding='utf-8')
        with pytest.raises(RamlError):
            parse_from_path(root / 'project' / 'api.raml')


class TestATemplateParameterIsNotAPath:
    """Spec section Resource Type and Trait Parameters.

    "Parameters cannot be used within any file location that is used in the
    context of modularization, that is, any file location defined in the
    `!include` tag or as a value of any of the `uses` or `extends` nodes."

    Composition is P1 and templates expand in P6, so an unchecked
    `!include <<version>>.raml` reaches the loader as a literal name: illegal on
    Windows, legal and merely absent on POSIX. Both report a missing file, which
    names the wrong mistake — and the TCK fixture for this
    (`Libraries/include-01/invalid-dynamic-inclusion.raml`) is then
    indistinguishable from `invalid-include-inexisting.raml` beside it.

    The loader is what proves the rule is the parser's: it serves the
    parameterised name, so nothing about the filesystem is doing the work.
    """

    class Serving(CountingLoader):
        """Answers a parameterised name, so the filesystem is not the check."""

        def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
            if '%3C%3C' in uri:
                return b'#%RAML 1.0 DataType\ntype: string\n'
            return super().load(uri, max_bytes=max_bytes)

    def test_an_include_argument_is_refused(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T: !include <<version>>.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(file_loader=self.Serving(root)))
        assert caught.value.head.message == 'path must not contain a template parameter'
        assert caught.value.head.info == {'path': '<<version>>.raml'}

    def test_a_uses_value_is_refused(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  lib: <<version>>.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(file_loader=self.Serving(root)))
        assert any('must not contain a template parameter' in m for m in caught.value.messages())

    def test_the_position_is_the_argument_and_not_the_document(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T: !include <<version>>.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(file_loader=self.Serving(root)))
        assert caught.value.head.position is not None
        assert caught.value.head.position.line > 1

    def test_one_angle_bracket_is_still_a_path(self):
        # The rule is about `<<`, not about punctuation, and this is where
        # overreaching would show. Asserted against `resolve_ref_uri` rather
        # than a real file because Windows will not create a name containing
        # `<`, and the rule belongs to the parser on either platform.
        raml = Raml(workspace_root_uri='file:///w')
        assert resolve_ref_uri(raml, 'a<b.yaml', 'file:///w/api.raml') == 'file:///w/a%3Cb.yaml'
