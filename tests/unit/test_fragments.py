"""Fragment identification, decoding, the cache, and `uses:` resolution.

See docs/04-fragments-and-namespaces.md.
"""

from __future__ import annotations

import pytest

from pyraml import (
    APIFragment,
    DataTypeFragment,
    DocumentationItemFragment,
    FragmentKind,
    Library,
    NamedExample,
    ParseOptions,
    RamlError,
    SecuritySchemeFragment,
    TraitFragment,
    parse_from_path,
    parse_from_string,
    path_to_file_uri,
)
from pyraml.parser.fragments import HEADS, ReferenceResolver, SecuritySchemeResolver, identify_fragment
from tests.unit.conftest import CountingLoader

API = '#%RAML 1.0\ntitle: Example\n'


def messages(error: RamlError) -> list[str]:
    return error.messages()


def traces(error: RamlError):
    return [chain[-1] for chain in error.chains()]


class TestIdentification:
    @pytest.mark.parametrize(('head', 'kind'), list(HEADS.items()))
    def test_every_documented_head_maps_to_its_kind(self, head: str, kind: FragmentKind):
        assert identify_fragment(head) is kind

    def test_an_unknown_head_is_not_guessed_at(self):
        assert identify_fragment('#%RAML 0.8') is None
        assert identify_fragment('#%RAML 1.0 Library ') is None

    def test_a_document_without_a_raml_header_fails_fast(self, workspace):
        root = workspace({'api.raml': 'title: not raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert traces(caught.value)[0].message == 'unknown fragment kind'

    def test_a_fragment_of_the_wrong_kind_names_both_kinds(self, workspace):
        # `uses:` demands a Library; a DataType there is a fail-fast error.
        root = workspace(
            {
                'api.raml': API + 'uses:\n  t: thing.raml\n',
                'thing.raml': '#%RAML 1.0 DataType\ntype: string\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')

        trace = traces(caught.value)[0]
        assert trace.message == 'unexpected fragment kind'
        assert trace.info == {'expected': 'Library', 'found': 'DataType'}

    def test_an_annotation_type_declaration_is_accepted_where_a_data_type_is_expected(self, workspace):
        # The two are structurally identical, so go-raml accepts either.
        root = workspace(
            {
                'api.raml': API + 'types:\n  T: string\n',
                'at.raml': '#%RAML 1.0 AnnotationTypeDeclaration\ntype: string\n',
            }
        )
        raml = parse_from_path(root / 'api.raml')
        from pyraml.parser.fragments import parse_fragment

        fragment = parse_fragment(raml, path_to_file_uri(root / 'at.raml'), FragmentKind.DATA_TYPE)
        assert isinstance(fragment, DataTypeFragment)


class TestEntryPoints:
    def test_parse_from_path_defaults_the_workspace_root_to_the_entry_directory(self, workspace):
        root = workspace({'project/api.raml': API + '(a): !include /shared.yaml\n', 'project/shared.yaml': 'k: v\n'})
        raml = parse_from_path(root / 'project' / 'api.raml')
        # The RAML-absolute include resolved under project/, not under tmp_path.
        assert raml.workspace_root_uri == path_to_file_uri(root / 'project')
        assert raml.entry_point.annotations['a'].value.raw == {'k': 'v'}

    def test_workspace_root_can_be_widened(self, workspace):
        root = workspace({'project/api.raml': API + '(a): !include /shared.yaml\n', 'shared.yaml': 'k: v\n'})
        raml = parse_from_path(root / 'project' / 'api.raml', ParseOptions(workspace_root=root))
        assert raml.entry_point.annotations['a'].value.raw == {'k': 'v'}

    def test_parse_from_string_resolves_includes_against_base_dir(self, workspace):
        root = workspace({'shared.yaml': 'k: v\n'})
        raml = parse_from_string(API + '(a): !include shared.yaml\n', file_name='api.raml', base_dir=root)
        assert raml.entry_point.annotations['a'].value.raw == {'k': 'v'}
        assert raml.location == path_to_file_uri(root / 'api.raml')

    def test_parse_from_string_requires_an_absolute_base_dir(self):
        with pytest.raises(RamlError) as caught:
            parse_from_string(API, file_name='api.raml', base_dir='relative')
        assert traces(caught.value)[0].message == 'base_dir must be an absolute path'

    def test_an_unreadable_entry_file_fails_fast(self, tmp_path):
        with pytest.raises(RamlError) as caught:
            parse_from_path(tmp_path / 'missing.raml')
        assert 'load resource' in str(caught.value)


class TestApiDecoding:
    def test_the_root_facets_phase_one_owns(self, workspace):
        root = workspace(
            {
                'api.raml': (
                    '#%RAML 1.0\n'
                    'title: Example\n'
                    'description: Some text\n'
                    'version: v1\n'
                    'baseUri: http://example.com/{version}\n'
                )
            }
        )
        api = parse_from_path(root / 'api.raml').entry_point
        assert isinstance(api, APIFragment)
        assert api.title.value == 'Example'
        assert api.description.value == 'Some text'
        assert api.version.value == 'v1'
        assert api.base_uri.value == 'http://example.com/{version}'
        assert api.title.key_pos.line == 2

    def test_title_is_required(self, workspace):
        root = workspace({'api.raml': '#%RAML 1.0\nversion: v1\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'title is required' in messages(caught.value)

    def test_an_empty_title_is_rejected(self, workspace):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle:\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'title must not be empty' in messages(caught.value)

    def test_an_unknown_root_field_is_reported_with_its_name(self, workspace):
        root = workspace({'api.raml': API + 'nonsense: 1\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert traces(caught.value)[0].info == {'field': 'nonsense'}

    def test_independent_root_errors_are_all_reported(self, workspace):
        # One broken key does not discard its siblings (docs/02 section 5).
        root = workspace({'api.raml': API + 'nonsense: 1\nrubbish: 2\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert [trace.info['field'] for trace in traces(caught.value)] == ['nonsense', 'rubbish']

    def test_endpoints_are_retained_in_document_order_not_decoded(self, workspace):
        root = workspace({'api.raml': API + '/users:\n  get:\n/orders:\n  post:\n'})
        api = parse_from_path(root / 'api.raml').entry_point
        assert [key.value for key, _value in api._raw_endpoints] == ['/users', '/orders']

    def test_types_and_schemas_are_mutually_exclusive(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  A: string\nschemas:\n  B: string\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert traces(caught.value)[0].message == 'types and schemas are mutually exclusive'

    def test_declarations_are_retained_for_later_phases(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  A: string\nannotationTypes:\n  B: string\ntraits:\n  t: {}\n'
                + 'resourceTypes:\n  r: {}\nsecuritySchemes:\n  s: {}\nbaseUriParameters:\n  p: string\n'
            }
        )
        api = parse_from_path(root / 'api.raml').entry_point
        # Phase 2 decodes the three type-shaped declarations.
        assert list(api.types) == ['A']
        assert list(api.annotation_types) == ['B']
        assert list(api.base_uri_parameters) == ['p']
        # The rest are still seams, each waiting on the phase named beside it.
        assert api._raw_traits is not None
        assert api._raw_resource_types is not None
        assert api._raw_security_schemes is not None
        assert api.traits == {}


class TestGlobalPrePass:
    def test_globals_are_harvested_before_the_main_loop(self, workspace):
        root = workspace({'api.raml': API + 'mediaType: [application/json, application/xml]\nprotocols: [HTTP]\n'})
        raml = parse_from_path(root / 'api.raml')
        assert raml.global_media_types == ['application/json', 'application/xml']
        assert raml.global_protocols == ['HTTP']

    def test_a_single_media_type_is_accepted_as_a_scalar(self, workspace):
        root = workspace({'api.raml': API + 'mediaType: application/json\n'})
        assert parse_from_path(root / 'api.raml').global_media_types == ['application/json']

    def test_an_invalid_media_type_is_rejected(self, workspace):
        root = workspace({'api.raml': API + 'mediaType: nonsense\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'invalid media type' in messages(caught.value)[0]

    def test_an_unknown_protocol_is_rejected(self, workspace):
        root = workspace({'api.raml': API + 'protocols: [FTP]\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'unknown protocol' in messages(caught.value)[0]

    def test_secured_by_is_retained_for_phase_seven(self, workspace):
        root = workspace({'api.raml': API + 'securedBy: [oauth]\n'})
        api = parse_from_path(root / 'api.raml').entry_point
        assert api._raw_secured_by is not None


class TestLibrary:
    def test_a_library_decodes_usage_uses_and_annotations(self, workspace):
        root = workspace(
            {
                'lib.raml': '#%RAML 1.0 Library\nusage: Shared types\nuses:\n  o: other.raml\n(tag): v\n',
                'other.raml': '#%RAML 1.0 Library\n',
            }
        )
        library = parse_from_path(root / 'lib.raml').entry_point
        assert isinstance(library, Library)
        assert library.usage.value == 'Shared types'
        assert library.uses['o'].link is not None
        assert library.annotations['tag'].value.raw == 'v'

    def test_an_empty_library_is_valid(self, workspace):
        root = workspace({'lib.raml': '#%RAML 1.0 Library\n'})
        assert isinstance(parse_from_path(root / 'lib.raml').entry_point, Library)

    def test_an_unknown_library_field_is_rejected(self, workspace):
        root = workspace({'lib.raml': '#%RAML 1.0 Library\nnonsense: 1\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml')
        assert traces(caught.value)[0].info == {'field': 'nonsense'}


class TestTypedFragments:
    def test_a_data_type_fragment_keeps_its_declaration_and_is_named_after_the_file(self, workspace):
        root = workspace(
            {
                'user.raml': '#%RAML 1.0 DataType\nuses:\n  l: lib.raml\ntype: object\n',
                'lib.raml': '#%RAML 1.0 Library\n',
            }
        )
        fragment = parse_from_path(root / 'user.raml').entry_point
        assert isinstance(fragment, DataTypeFragment)
        assert fragment.declared_name == 'user.raml'
        assert fragment.shape.name == 'user.raml', 'the shape is named after the file'
        assert fragment.shape.type == 'object'
        assert fragment.uses['l'].link is not None

    def test_a_named_example_keeps_each_example_by_name(self, workspace):
        root = workspace({'ex.raml': '#%RAML 1.0 NamedExample\nfirst:\n  a: 1\nsecond:\n  b: 2\n'})
        fragment = parse_from_path(root / 'ex.raml').entry_point
        assert isinstance(fragment, NamedExample)
        assert list(fragment.examples) == ['first', 'second']
        assert fragment.examples['first'].data.raw == {'a': 1}

    @pytest.mark.parametrize(
        ('head', 'cls'),
        [
            ('#%RAML 1.0 Trait', TraitFragment),
            ('#%RAML 1.0 SecurityScheme', SecuritySchemeFragment),
        ],
    )
    def test_a_definition_fragment_keeps_its_body(self, workspace, head: str, cls: type):
        root = workspace({'f.raml': f'{head}\ndescription: d\n'})
        fragment = parse_from_path(root / 'f.raml').entry_point
        assert isinstance(fragment, cls)
        assert fragment._raw_definition is not None
        assert fragment.definition is None, 'the definition itself belongs to a later phase'

    def test_a_documentation_item_fragment_decodes_title_and_content(self, workspace):
        root = workspace({'d.raml': '#%RAML 1.0 DocumentationItem\ntitle: T\ncontent: C\n'})
        fragment = parse_from_path(root / 'd.raml').entry_point
        assert isinstance(fragment, DocumentationItemFragment)
        assert (fragment.item.title.value, fragment.item.content.value) == ('T', 'C')
        assert fragment.item.link is fragment

    def test_documentation_content_may_be_included_from_markdown(self, workspace):
        root = workspace(
            {'api.raml': API + 'documentation:\n  - title: T\n    content: !include c.md\n', 'c.md': '# Body\n'}
        )
        api = parse_from_path(root / 'api.raml').entry_point
        assert api.documentation[0].content.value == '# Body\n'
        assert api.documentation[0].content.include.path == 'c.md'

    def test_a_documentation_item_may_be_a_fragment_include(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'documentation:\n  - !include d.raml\n',
                'd.raml': '#%RAML 1.0 DocumentationItem\ntitle: T\ncontent: C\n',
            }
        )
        raml = parse_from_path(root / 'api.raml')
        assert raml.entry_point.documentation[0].title.value == 'T'
        # Noted, not spliced: the fragment parser did the reading.
        assert path_to_file_uri(root / 'd.raml') in raml.fragments


class TestUsesResolution:
    def test_mutually_importing_libraries_terminate_with_a_cyclic_graph(self, workspace):
        root = workspace(
            {
                'a.raml': '#%RAML 1.0 Library\nuses:\n  b: b.raml\n',
                'b.raml': '#%RAML 1.0 Library\nuses:\n  a: a.raml\n',
            }
        )
        library = parse_from_path(root / 'a.raml').entry_point
        assert library.uses['b'].link.uses['a'].link is library, 'the cycle must close on the same object'

    def test_a_file_referenced_from_five_places_is_decoded_once(self, workspace):
        files = {'shared.raml': '#%RAML 1.0 Library\n'}
        for name in 'abcde':
            files[f'{name}.raml'] = '#%RAML 1.0 Library\nuses:\n  s: shared.raml\n'
        files['api.raml'] = API + 'uses:\n' + ''.join(f'  {n}: {n}.raml\n' for n in 'abcde')
        root = workspace(files)

        loader = CountingLoader(root)
        raml = parse_from_path(root / 'api.raml', ParseOptions(file_loader=loader))

        shared = path_to_file_uri(root / 'shared.raml')
        assert loader.counts[shared] == 1
        links = [raml.entry_point.uses[name].link.uses['s'].link for name in 'abcde']
        assert all(link is links[0] for link in links)

    def test_a_missing_library_is_reported_at_the_uses_key(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  gone: gone.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        chain = next(iter(caught.value.chains()))
        assert chain[0].message == 'parse uses library'
        assert chain[0].position.line == 4, 'the position is the uses: entry, not the uses: key'

    def test_duplicate_library_names_are_rejected(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  l: a.raml\n  l: b.raml\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'duplicate library name' in messages(caught.value)[0]

    def test_uses_may_be_empty(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n'})
        assert parse_from_path(root / 'api.raml').entry_point.uses == {}


class TestProtocolConformance:
    def test_every_fragment_is_a_reference_resolver(self, workspace):
        root = workspace(
            {
                'api.raml': API,
                'lib.raml': '#%RAML 1.0 Library\n',
                'dt.raml': '#%RAML 1.0 DataType\ntype: string\n',
                'ne.raml': '#%RAML 1.0 NamedExample\nfirst: 1\n',
                'tr.raml': '#%RAML 1.0 Trait\n',
                'rt.raml': '#%RAML 1.0 ResourceType\n',
                'ss.raml': '#%RAML 1.0 SecurityScheme\n',
                'di.raml': '#%RAML 1.0 DocumentationItem\ntitle: T\ncontent: C\n',
            }
        )
        for name in ('api', 'lib', 'dt', 'ne', 'tr', 'rt', 'ss', 'di'):
            fragment = parse_from_path(root / f'{name}.raml').entry_point
            assert isinstance(fragment, ReferenceResolver), name

    def test_only_the_declaring_fragments_resolve_security_schemes(self, workspace):
        root = workspace(
            {'api.raml': API, 'lib.raml': '#%RAML 1.0 Library\n', 'dt.raml': '#%RAML 1.0 DataType\ntype: string\n'}
        )
        assert isinstance(parse_from_path(root / 'api.raml').entry_point, SecuritySchemeResolver)
        assert isinstance(parse_from_path(root / 'lib.raml').entry_point, SecuritySchemeResolver)
        assert not isinstance(parse_from_path(root / 'dt.raml').entry_point, SecuritySchemeResolver)


class TestLibraryLinkLookup:
    """docs/06 section 3.2 — the library half of a `lib.Type` reference.

    On the resolver protocol rather than reached through `uses` so that P7 can
    emit it through the anchor it already holds.
    """

    def test_a_uses_prefix_resolves_to_its_link(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  l: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'})
        fragment = parse_from_path(root / 'api.raml').entry_point
        link = fragment.library_link('l')
        assert link is not None
        assert link.link.location == path_to_file_uri(root / 'lib.raml')

    def test_an_unknown_prefix_is_none_rather_than_an_error(self, workspace):
        # P7 emits the type-name ref either way; only the library ref is skipped.
        root = workspace({'api.raml': API + 'uses:\n  l: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'})
        assert parse_from_path(root / 'api.raml').entry_point.library_link('nope') is None

    def test_a_fragment_with_no_uses_has_no_links(self, workspace):
        root = workspace({'dt.raml': '#%RAML 1.0 DataType\ntype: string\n'})
        assert parse_from_path(root / 'dt.raml').entry_point.library_link('l') is None


class TestResolverIndex:
    """docs/04 section 4.2 — P7's fallback for a shape with no anchor."""

    def test_every_decoded_fragment_is_indexed_by_its_location(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  l: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'})
        raml = parse_from_path(root / 'api.raml')
        for name in ('api', 'lib'):
            uri = path_to_file_uri(root / f'{name}.raml')
            assert raml.resolver_at(uri) is raml.fragments[uri], name

    def test_an_unknown_location_has_no_resolver(self, workspace):
        root = workspace({'api.raml': API})
        assert parse_from_path(root / 'api.raml').resolver_at('file:///nowhere.raml') is None


class TestParseCtx:
    def test_the_stack_is_empty_again_after_a_parse(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  l: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'})
        raml = parse_from_path(root / 'api.raml')
        assert raml.current_ctx().anchor is None

    def test_an_annotation_captures_the_fragment_it_was_written_in(self, workspace):
        # Not the includer's scope: a fragment means one thing everywhere.
        root = workspace(
            {
                'api.raml': API + 'uses:\n  l: lib.raml\n(here): 1\n',
                'lib.raml': '#%RAML 1.0 Library\n(there): 2\n',
            }
        )
        raml = parse_from_path(root / 'api.raml')
        anchors = {extension.name: extension.anchor.location for extension in raml.domain_extensions}
        assert anchors == {
            'here': path_to_file_uri(root / 'api.raml'),
            'there': path_to_file_uri(root / 'lib.raml'),
        }


class TestSourceRetention:
    def test_source_nodes_are_kept_only_on_request(self, workspace):
        root = workspace({'api.raml': API})
        uri = path_to_file_uri(root / 'api.raml')
        assert parse_from_path(root / 'api.raml').source_node(uri) is None
        retained = parse_from_path(root / 'api.raml', ParseOptions(retain_source=True))
        assert retained.source_node(uri) is not None


def _pairs(node):
    from pyraml.yamlnode import pairs

    return list(pairs(node))
