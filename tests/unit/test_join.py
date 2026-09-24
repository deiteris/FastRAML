"""`fastraml join`: combining API documents (docs/20).

Each test writes a small workspace, joins it, and reads the result back through
the parser, or asserts the diagnostics by message key and `info`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from fastraml import ParseOptions, RamlError, parse_from_string
from fastraml.cli import EXIT_INVALID, EXIT_OK, main
from fastraml.join import BaseUriOverride, JoinOptions, join
from fastraml.join.baseuri import common_segments, plan_created, split_base_uri, uri_variables
from fastraml.join.paths import Rebaser
from fastraml.join.writer import write_raml
from fastraml.parser.structural_merge import node_value_equal
from fastraml.uris import path_to_file_uri
from fastraml.yamlnode import compose
from tests.unit.conftest import write_files

if TYPE_CHECKING:
    from pathlib import Path

HEAD = '#%RAML 1.0\ntitle: Shop\n'


class _Loader(yaml.SafeLoader):
    """Reads `!include x` as the string `!include x`."""


_Loader.add_constructor('!include', lambda loader, node: f'!include {loader.construct_scalar(node)}')


def run(root: Path, files: dict[str, str], inputs: tuple[str, ...] = ('a.raml', 'b.raml'), **options: Any) -> str:
    write_files(root, files)
    options.setdefault('output', root / 'out' / 'joined.raml')
    return join([root / name for name in inputs], JoinOptions(**options))


def document(root: Path, files: dict[str, str], inputs: tuple[str, ...] = ('a.raml', 'b.raml'), **options: Any) -> Any:
    """The joined text as plain YAML data."""
    return yaml.load(run(root, files, inputs, **options), Loader=_Loader)  # noqa: S506 - a safe loader


def failures(root: Path, files: dict[str, str], inputs: tuple[str, ...] = ('a.raml', 'b.raml'), **options: Any):
    """The innermost frame of every chain, as (message, info, file name)."""
    with pytest.raises(RamlError) as caught:
        run(root, files, inputs, **options)
    return [
        (frames[-1].message, dict(frames[-1].info or {}), frames[-1].location.rsplit('/', 1)[-1])
        for frames in caught.value.chains()
    ]


def messages(root: Path, files: dict[str, str], **options: Any) -> list[str]:
    return [message for message, _, _ in failures(root, files, **options)]


class TestInputs:
    def test_only_api_documents_are_joined(self, tmp_path):
        # docs/20 § 2: Libraries, Overlays and Extensions are rejected.
        files = {
            'a.raml': HEAD,
            'b.raml': '#%RAML 1.0 Library\n',
            'c.raml': '#%RAML 1.0 Overlay\nextends: a.raml\n',
        }
        assert failures(tmp_path, files, ('a.raml', 'b.raml', 'c.raml')) == [
            ('unexpected fragment kind', {'expected': 'API', 'found': 'Library'}, 'b.raml'),
            ('unexpected fragment kind', {'expected': 'API', 'found': 'Overlay'}, 'c.raml'),
        ]

    def test_an_input_that_fails_to_parse_stops_the_join_with_its_own_diagnostic(self, tmp_path):
        files = {'a.raml': HEAD, 'b.raml': HEAD + 'types:\n  T: Missing\n'}
        ((message, _, where),) = failures(tmp_path, files)
        assert where == 'b.raml'
        assert message != 'join conflict'


class TestDeclarations:
    def test_names_from_every_input_are_added_in_input_order(self, tmp_path):
        files = {
            'a.raml': HEAD + 'types:\n  B: string\n  A: string\n',
            'b.raml': HEAD + 'types:\n  C: integer\n',
        }
        assert list(document(tmp_path, files)['types']) == ['B', 'A', 'C']

    def test_an_identical_duplicate_is_kept_once(self, tmp_path):
        declaration = 'types:\n  User:\n    properties:\n      id: string\n'
        files = {'a.raml': HEAD + declaration, 'b.raml': HEAD + declaration}
        assert document(tmp_path, files)['types'] == {'User': {'properties': {'id': 'string'}}}

    def test_a_different_declaration_under_one_name_is_a_conflict(self, tmp_path):
        files = {
            'a.raml': HEAD + 'types:\n  User:\n    properties:\n      id: string\n      name: string\n',
            'b.raml': HEAD + 'types:\n  User:\n    properties:\n      id: integer\n      name: string\n',
        }
        ((message, info, where),) = failures(tmp_path, files)
        assert (message, where) == ('join conflict', 'b.raml')
        assert info['kind'] == 'type'
        assert info['name'] == 'User'
        assert info['at'] == 'properties/id'
        assert info['other'].endswith('/a.raml:4:3')

    def test_the_comparison_is_strict_about_spelling(self, tmp_path):
        # docs/20 § 4: equivalent spellings are different.
        files = {'a.raml': HEAD + 'types:\n  Id: string\n', 'b.raml': HEAD + 'types:\n  Id:\n    type: string\n'}
        assert messages(tmp_path, files) == ['join conflict']

    def test_types_and_schemas_are_one_namespace(self, tmp_path):
        files = {'a.raml': HEAD + 'types:\n  Id: string\n', 'b.raml': HEAD + 'schemas:\n  Id: integer\n'}
        assert failures(tmp_path, files)[0][1]['kind'] == 'type'

    @pytest.mark.parametrize(
        ('key', 'kind', 'a', 'b'),
        [
            ('traits', 'trait', '{description: a}', '{description: b}'),
            ('resourceTypes', 'resourceType', '{description: a}', '{description: b}'),
            ('annotationTypes', 'annotationType', 'string', 'integer'),
            ('securitySchemes', 'securityScheme', '{type: Basic Authentication}', '{type: Digest Authentication}'),
        ],
    )
    def test_every_name_map_reports_its_kind(self, tmp_path, key, kind, a, b):
        files = {'a.raml': HEAD + f'{key}:\n  x: {a}\n', 'b.raml': HEAD + f'{key}:\n  x: {b}\n'}
        assert failures(tmp_path, files)[0][1]['kind'] == kind

    def test_a_referenced_name_that_differs_is_itself_a_conflict(self, tmp_path):
        # docs/20 § 4: identical `User` referring to a different `Address`.
        user = 'types:\n  User:\n    properties:\n      home: Address\n'
        files = {
            'a.raml': HEAD + user + '  Address:\n    properties:\n      street: string\n',
            'b.raml': HEAD + user + '  Address:\n    properties:\n      street: integer\n',
        }
        assert [info['name'] for _, info, _ in failures(tmp_path, files)] == ['Address']


class TestIncludes:
    def test_two_spellings_of_one_file_are_identical(self, tmp_path):
        files = {
            'a/api.raml': HEAD + 'types:\n  User: !include ../shared/user.raml\n',
            'b/api.raml': HEAD + 'types:\n  User: !include /shared/user.raml\n',
            'shared/user.raml': '#%RAML 1.0 DataType\ntype: string\n',
        }
        joined = document(tmp_path, files, ('a/api.raml', 'b/api.raml'), parse=ParseOptions(workspace_root=tmp_path))
        assert list(joined['types']) == ['User']

    def test_one_spelling_in_two_directories_names_two_files(self, tmp_path):
        files = {
            'a/api.raml': HEAD + 'types:\n  User: !include user.raml\n',
            'a/user.raml': '#%RAML 1.0 DataType\ntype: string\n',
            'b/api.raml': HEAD + 'types:\n  User: !include user.raml\n',
            'b/user.raml': '#%RAML 1.0 DataType\ntype: integer\n',
        }
        assert messages(tmp_path, files, inputs=('a/api.raml', 'b/api.raml')) == ['join conflict']

    def test_an_include_compares_equal_to_the_same_content_inline(self, tmp_path):
        files = {
            'a.raml': HEAD + 'types:\n  Point:\n    example: !include point.yaml\n',
            'point.yaml': 'x: 1\ny: 2\n',
            'b.raml': HEAD + 'types:\n  Point:\n    example:\n      x: 1\n      y: 2\n',
        }
        assert document(tmp_path, files)['types'] == {'Point': {'example': '!include ../point.yaml'}}

    def test_include_arguments_are_written_relative_to_the_output(self, tmp_path):
        # docs/20 § 7.2.
        files = {
            'a/api.raml': HEAD + 'types:\n  User: !include types/user.raml\n',
            'a/types/user.raml': '#%RAML 1.0 DataType\ntype: string\n',
            'b/api.raml': HEAD,
        }
        text = run(tmp_path, files, ('a/api.raml', 'b/api.raml'), output=tmp_path / 'out' / 'joined.raml')
        assert 'User: !include ../a/types/user.raml\n' in text


class TestLibraries:
    def test_one_alias_naming_one_library_is_kept_once(self, tmp_path):
        files = {
            'a/api.raml': HEAD + 'uses:\n  lib: ../lib.raml\n',
            'b/api.raml': HEAD + 'uses:\n  lib: /lib.raml\n',
            'lib.raml': '#%RAML 1.0 Library\ntypes:\n  T: string\n',
        }
        joined = document(tmp_path, files, ('a/api.raml', 'b/api.raml'), parse=ParseOptions(workspace_root=tmp_path))
        assert joined['uses'] == {'lib': '../lib.raml'}

    def test_one_alias_naming_two_libraries_is_a_namespace_conflict(self, tmp_path):
        files = {
            'a/api.raml': HEAD + 'uses:\n  lib: lib.raml\n',
            'a/lib.raml': '#%RAML 1.0 Library\n',
            'b/api.raml': HEAD + 'uses:\n  lib: lib.raml\n',
            'b/lib.raml': '#%RAML 1.0 Library\n',
        }
        ((message, info, where),) = failures(tmp_path, files, ('a/api.raml', 'b/api.raml'))
        assert (message, where) == ('library namespace conflict', 'api.raml')
        assert info['library'] == 'lib'
        assert info['uri'].endswith('/b/lib.raml')
        assert info['other'].endswith('/a/lib.raml')


class TestEndpoints:
    def test_operations_on_one_endpoint_are_combined(self, tmp_path):
        files = {'a.raml': HEAD + '/users:\n  get:\n', 'b.raml': HEAD + '/users:\n  post:\n'}
        assert list(document(tmp_path, files)['/users']) == ['get', 'post']

    def test_an_endpoint_is_identified_by_its_full_path(self, tmp_path):
        # docs/20 § 3.3: flat `/users/{id}` and nested `/{id}` are one endpoint.
        files = {
            'a.raml': HEAD + '/users:\n  /{id}:\n    get:\n',
            'b.raml': HEAD + '/users/{id}:\n  delete:\n',
        }
        joined = document(tmp_path, files)
        assert list(joined['/users']['/{id}']) == ['get', 'delete']
        assert '/users/{id}' not in joined

    def test_a_new_endpoint_keeps_the_nesting_its_input_wrote(self, tmp_path):
        files = {'a.raml': HEAD + '/users:\n  get:\n', 'b.raml': HEAD + '/users/{id}:\n  get:\n'}
        joined = document(tmp_path, files)
        assert list(joined) == ['title', '/users', '/users/{id}']

    def test_two_different_operations_on_one_endpoint_conflict(self, tmp_path):
        files = {
            'a.raml': HEAD + '/users:\n  get:\n    description: a\n',
            'b.raml': HEAD + '/users:\n  get:\n    description: b\n',
        }
        ((message, info, _),) = failures(tmp_path, files)
        assert message == 'join conflict'
        assert (info['kind'], info['name'], info['at']) == ('operation', 'GET /users', 'description')

    def test_an_endpoints_own_properties_are_one_entry(self, tmp_path):
        files = {
            'a.raml': HEAD + '/users:\n  description: a\n  get:\n',
            'b.raml': HEAD + '/users:\n  post:\n',
        }
        ((_, info, _),) = failures(tmp_path, files)
        assert (info['kind'], info['name']) == ('endpoint', '/users')


class TestRootValues:
    def test_a_shared_value_is_kept(self, tmp_path):
        files = {'a.raml': HEAD + 'version: v1\n', 'b.raml': HEAD + 'version: v1\n'}
        assert document(tmp_path, files)['version'] == 'v1'

    def test_differing_values_need_an_option(self, tmp_path):
        files = {'a.raml': HEAD + 'version: v1\n', 'b.raml': HEAD + 'version: v2\n'}
        ((message, info, _),) = failures(tmp_path, files)
        assert message == 'join root value differs'
        assert info['property'] == 'version'
        assert document(tmp_path, files, version='v3')['version'] == 'v3'

    def test_an_option_wins_over_a_shared_value(self, tmp_path):
        files = {'a.raml': HEAD, 'b.raml': HEAD}
        assert document(tmp_path, files, title='Joined')['title'] == 'Joined'

    def test_an_empty_description_omits_it(self, tmp_path):
        files = {'a.raml': HEAD + 'description: a\n', 'b.raml': HEAD + 'description: b\n'}
        assert 'description' not in document(tmp_path, files, description='')

    def test_documentation_items_are_added_by_title(self, tmp_path):
        item = 'documentation:\n  - title: Intro\n    content: Hello\n'
        files = {
            'a.raml': HEAD + item,
            'b.raml': HEAD + item + '  - title: More\n    content: Text\n',
        }
        assert [entry['title'] for entry in document(tmp_path, files)['documentation']] == ['Intro', 'More']

    def test_an_included_documentation_item_is_identified_by_its_title(self, tmp_path):
        files = {
            'a.raml': HEAD + 'documentation:\n  - !include intro.raml\n',
            'intro.raml': '#%RAML 1.0 DocumentationItem\ntitle: Intro\ncontent: Hello\n',
            'b.raml': HEAD + 'documentation:\n  - title: Intro\n    content: Hello\n',
        }
        assert document(tmp_path, files)['documentation'] == [{'title': 'Intro', 'content': 'Hello'}]

    def test_documentation_under_one_title_with_other_content_conflicts(self, tmp_path):
        files = {
            'a.raml': HEAD + 'documentation:\n  - title: Intro\n    content: Hello\n',
            'b.raml': HEAD + 'documentation:\n  - title: Intro\n    content: Bye\n',
        }
        assert failures(tmp_path, files)[0][1]['kind'] == 'documentation'


SCHEMES = 'securitySchemes:\n  basic:\n    type: Basic Authentication\n  digest:\n    type: Digest Authentication\n'


class TestRootDefaults:
    def test_an_identical_default_stays_at_the_root(self, tmp_path):
        files = {'a.raml': HEAD + 'mediaType: application/json\n', 'b.raml': HEAD + 'mediaType: application/json\n'}
        assert document(tmp_path, files)['mediaType'] == 'application/json'

    def test_differing_security_is_written_onto_each_inputs_own_methods(self, tmp_path):
        # docs/20 § 5.1: never the union at the root.
        files = {
            'a.raml': HEAD + SCHEMES + 'securedBy: [basic]\n/a:\n  get:\n  post:\n    securedBy: [null]\n',
            'b.raml': HEAD + SCHEMES + '/b:\n  get:\n',
        }
        joined = document(tmp_path, files)
        assert 'securedBy' not in joined
        assert joined['/a']['get'] == {'securedBy': ['basic']}
        assert joined['/a']['post'] == {'securedBy': [None]}
        assert joined['/b']['get'] is None

    def test_a_resource_with_its_own_security_is_left_alone(self, tmp_path):
        files = {
            'a.raml': HEAD + SCHEMES + 'securedBy: [basic]\n/a:\n  securedBy: [digest]\n  get:\n',
            'b.raml': HEAD + SCHEMES,
        }
        assert document(tmp_path, files)['/a']['get'] is None

    def test_a_media_type_wraps_each_body_without_one(self, tmp_path):
        files = {
            'a.raml': HEAD
            + 'mediaType: [application/json, application/xml]\n'
            + '/a:\n  post:\n    body:\n      type: string\n'
            + '    responses:\n      200:\n        body:\n          text/plain: string\n'
            + '      201:\n        body: string\n',
            'b.raml': HEAD + '/b:\n  get:\n',
        }
        post = document(tmp_path, files)['/a']['post']
        assert post['body'] == {'application/json': {'type': 'string'}, 'application/xml': {'type': 'string'}}
        assert post['responses'][200]['body'] == {'text/plain': 'string'}
        assert post['responses'][201]['body'] == {'application/json': 'string', 'application/xml': 'string'}

    def test_protocols_are_written_onto_methods(self, tmp_path):
        files = {'a.raml': HEAD + 'protocols: [HTTPS]\n/a:\n  get:\n', 'b.raml': HEAD + '/b:\n  get:\n'}
        assert document(tmp_path, files)['/a']['get'] == {'protocols': ['HTTPS']}

    def test_a_template_setting_the_default_is_refused(self, tmp_path):
        # docs/20 § 5.3, reason `sets`.
        files = {
            'a.raml': HEAD
            + SCHEMES
            + 'securedBy: [basic]\nresourceTypes:\n  secured:\n    securedBy: [digest]\n/a:\n  type: secured\n  get:\n',
            'b.raml': HEAD + SCHEMES,
        }
        ((message, info, where),) = failures(tmp_path, files)
        assert (message, where) == ('join default reaches template', 'a.raml')
        assert info == {'property': 'securedBy', 'template': 'secured', 'reason': 'sets'}

    def test_a_trait_setting_protocols_is_refused(self, tmp_path):
        files = {
            'a.raml': HEAD
            + 'protocols: [HTTPS]\ntraits:\n  plain:\n    protocols: [HTTP]\n/a:\n  get:\n    is: [plain]\n',
            'b.raml': HEAD,
        }
        assert failures(tmp_path, files)[0][1] == {'property': 'protocols', 'template': 'plain', 'reason': 'sets'}

    def test_a_template_body_without_a_media_type_is_refused(self, tmp_path):
        files = {
            'a.raml': HEAD
            + 'mediaType: application/json\ntraits:\n  paged:\n    responses:\n      200:\n        body:\n          type: string\n'
            + '/a:\n  get:\n    is: [paged]\n',
            'b.raml': HEAD,
        }
        assert failures(tmp_path, files)[0][1] == {'property': 'mediaType', 'template': 'paged', 'reason': 'body'}

    def test_a_method_only_a_resource_type_writes_is_refused(self, tmp_path):
        files = {
            'a.raml': HEAD
            + SCHEMES
            + 'securedBy: [basic]\nresourceTypes:\n  collection:\n    get:\n    post?:\n/a:\n  type: collection\n',
            'b.raml': HEAD + SCHEMES,
        }
        assert failures(tmp_path, files)[0][1] == {
            'property': 'securedBy',
            'template': 'collection',
            'reason': 'method',
        }

    def test_a_resource_with_its_own_security_needs_nothing_from_a_contributed_method(self, tmp_path):
        files = {
            'a.raml': HEAD
            + SCHEMES
            + 'securedBy: [basic]\nresourceTypes:\n  collection:\n    get:\n'
            + '/a:\n  type: collection\n  securedBy: [digest]\n',
            'b.raml': HEAD + SCHEMES,
        }
        assert '/a' in document(tmp_path, files)

    def test_a_template_name_holding_a_parameter_is_refused(self, tmp_path):
        # docs/20 § 5.3, reason `parameter`: the name cannot be followed here.
        files = {
            'a.raml': HEAD
            + 'protocols: [HTTPS]\nresourceTypes:\n  base:\n    get:\n'
            + '  wrapper:\n    type: <<inner>>\n/a:\n  type: {wrapper: {inner: base}}\n  get:\n',
            'b.raml': HEAD,
        }
        infos = [info for _, info, _ in failures(tmp_path, files)]
        assert {'property': 'protocols', 'template': '<<inner>>', 'reason': 'parameter'} in infos

    def test_an_optional_method_is_not_contributed(self, tmp_path):
        files = {
            'a.raml': HEAD
            + SCHEMES
            + 'securedBy: [basic]\nresourceTypes:\n  collection:\n    get?:\n/a:\n  type: collection\n',
            'b.raml': HEAD + SCHEMES,
        }
        assert '/a' in document(tmp_path, files)


class TestBaseUri:
    def test_the_common_path_is_taken_by_whole_segments(self):
        uris = [split_base_uri('https://x/v1/user'), split_base_uri('https://x/v1/users')]
        assert common_segments(uris) == ('v1',)

    def test_uri_variables_drop_operators_and_modifiers(self):
        assert uri_variables('https://{+host}/{a,b*}/{c:3}') == ['host', 'a', 'b', 'c']

    def test_pass_through_segments_become_one_key(self):
        assert [[step.key for step in chain] for chain in plan_created([('orders', 'v2'), ()])] == [['/orders/v2'], []]

    def test_shared_remainder_segments_share_a_created_endpoint(self):
        chains = plan_created([('x', 'a'), ('x', 'b'), ('y',)])
        assert [[step.key for step in chain] for chain in chains] == [['/x', '/a'], ['/x', '/b'], ['/y']]

    def test_each_inputs_resources_go_under_its_remainder(self, tmp_path):
        files = {
            'a.raml': HEAD + 'baseUri: https://api.example.com/v1/users\n/list:\n  get:\n',
            'b.raml': HEAD + 'baseUri: https://api.example.com/v1/orders\n/list:\n  get:\n',
        }
        joined = document(tmp_path, files)
        assert joined['baseUri'] == 'https://api.example.com/v1'
        assert list(joined['/users']) == ['/list']
        assert list(joined['/orders']) == ['/list']

    def test_an_input_at_the_common_path_keeps_its_resources_at_the_root(self, tmp_path):
        files = {
            'a.raml': HEAD + 'baseUri: https://x/v1\n/a:\n  get:\n',
            'b.raml': HEAD + 'baseUri: https://x/v1/b\n/c:\n  get:\n',
        }
        joined = document(tmp_path, files)
        assert list(joined['/a']) == ['get']
        assert list(joined['/b']['/c']) == ['get']

    def test_a_different_authority_is_a_conflict(self, tmp_path):
        files = {
            'a.raml': HEAD + 'baseUri: https://a.example.com\n',
            'b.raml': HEAD + 'baseUri: https://b.example.com\n',
        }
        ((message, info, _),) = failures(tmp_path, files)
        assert message == 'join base uri conflict'
        assert info['values'] == ['https://a.example.com', 'https://b.example.com']

    def test_a_base_uri_on_only_some_inputs_is_an_error(self, tmp_path):
        files = {'a.raml': HEAD + 'baseUri: https://x\n', 'b.raml': HEAD}
        ((message, info, _),) = failures(tmp_path, files)
        assert message == 'join missing base uri'
        assert info['inputs'][0].endswith('b.raml')

    def test_no_base_uri_anywhere_is_none_in_the_output(self, tmp_path):
        assert 'baseUri' not in document(tmp_path, {'a.raml': HEAD, 'b.raml': HEAD})

    def test_an_override_supplies_a_missing_base_uri(self, tmp_path):
        files = {'a.raml': HEAD + 'baseUri: https://x/a\n/r:\n  get:\n', 'b.raml': HEAD + '/r:\n  get:\n'}
        override = {path_to_file_uri(tmp_path / 'b.raml'): BaseUriOverride('https://x/b')}
        joined = document(tmp_path, files, base_uris=override)
        assert joined['baseUri'] == 'https://x'
        assert list(joined) == ['title', 'baseUri', '/a', '/b']

    def test_a_moved_variable_becomes_a_uri_parameter_of_the_created_endpoint(self, tmp_path):
        files = {
            'a.raml': HEAD
            + 'baseUri: https://x/{tenant}/a\nbaseUriParameters:\n  tenant:\n    pattern: ^[a-z]+$\n/r:\n  get:\n',
            'b.raml': HEAD + 'baseUri: https://x/b\n/r:\n  get:\n',
        }
        joined = document(tmp_path, files)
        assert joined['/{tenant}/a']['uriParameters'] == {'tenant': {'pattern': '^[a-z]+$'}}
        assert 'baseUriParameters' not in joined

    def test_a_shared_variable_declared_in_only_one_input_is_a_conflict(self, tmp_path):
        files = {
            'a.raml': HEAD + 'baseUri: https://{region}.x/a\nbaseUriParameters:\n  region:\n    enum: [eu]\n',
            'b.raml': HEAD + 'baseUri: https://{region}.x/b\n',
        }
        ((message, info, _),) = failures(tmp_path, files)
        assert (message, info['kind'], info['name']) == ('join conflict', 'baseUriParameter', 'region')

    def test_version_stays_a_variable_when_every_input_means_the_output_version(self, tmp_path):
        files = {
            'a.raml': HEAD + 'version: v1\nbaseUri: https://x/{version}/a\n',
            'b.raml': HEAD + 'version: v1\nbaseUri: https://x/{version}/b\n',
        }
        assert document(tmp_path, files)['baseUri'] == 'https://x/{version}'

    def test_version_is_substituted_where_the_inputs_differ(self, tmp_path):
        files = {
            'a.raml': HEAD + 'version: v1\nbaseUri: https://x/{version}\n/a:\n  get:\n',
            'b.raml': HEAD + 'version: v2\nbaseUri: https://x/{version}\n/b:\n  get:\n',
        }
        joined = document(tmp_path, files, version='v3')
        assert joined['baseUri'] == 'https://x'
        assert list(joined['/v1']) == ['/a']
        assert list(joined['/v2']) == ['/b']

    def test_a_moved_input_whose_template_reads_resource_path_is_refused(self, tmp_path):
        # docs/20 § 6.4.
        files = {
            'a.raml': HEAD
            + 'baseUri: https://x/a\nresourceTypes:\n  item:\n    description: at <<resourcePath>>\n'
            + '/r:\n  type: item\n',
            'b.raml': HEAD + 'baseUri: https://x/b\n',
        }
        ((message, info, _),) = failures(tmp_path, files)
        assert message == 'join resource path changes'
        assert info['template'] == 'item'


class TestOutput:
    def test_the_result_parses(self, tmp_path):
        files = {
            'a.raml': HEAD + 'baseUri: https://x/a\ntypes:\n  T: string\n/r:\n  get:\n    responses:\n      200:\n',
            'b.raml': HEAD + 'baseUri: https://x/b\ntypes:\n  T: string\n/r:\n  post:\n',
        }
        text = run(tmp_path, files)
        parse_from_string(text, file_name='joined.raml', base_dir=tmp_path / 'out')

    def test_root_keys_follow_the_documented_order(self, tmp_path):
        files = {
            'a.raml': HEAD
            + 'uses:\n  l: lib.raml\n/r:\n  get:\ntypes:\n  T: string\n(ann): 1\n'
            + 'annotationTypes:\n  ann: integer\nversion: v1\n',
            'lib.raml': '#%RAML 1.0 Library\n',
            'b.raml': HEAD + 'version: v1\n',
        }
        assert list(document(tmp_path, files)) == [
            'title',
            'version',
            'uses',
            'types',
            'annotationTypes',
            '(ann)',
            '/r',
        ]

    @pytest.mark.parametrize(
        'value',
        ['no', "'1e3'", '0o17', "'0o17'", '~', "''", '2020-01-01', "'2020-01-01'", '.inf', "'true'", '12:30:00'],
    )
    def test_every_scalar_reads_back_with_its_tag(self, value):
        # docs/20 § 7.1: quoted exactly where YAML 1.2 would read it otherwise.
        source = compose(f'key: {value}\n', uri='file:///x.raml')
        assert node_value_equal(compose(write_raml(source), uri='file:///y.raml'), source)

    def test_a_multi_line_string_is_a_literal_block(self):
        source = compose('key: "one\\ntwo\\n"\n', uri='file:///x.raml')
        text = write_raml(source)
        assert 'key: |\n  one\n  two\n' in text
        assert node_value_equal(compose(text, uri='file:///y.raml'), source)

    def test_a_path_on_another_drive_cannot_be_written_relative(self):
        # docs/20 § 7.2: `file:///D:/...` from an output on `C:`.
        node = compose('key: !include x.raml\n', uri='file:///C:/a.raml').content[1]
        with pytest.raises(RamlError) as caught:
            Rebaser('file:///C:/out/joined.raml').relative('file:///D:/x.raml', node, 'file:///C:/a.raml')
        assert caught.value.head.message == 'join path not relative'
        assert caught.value.head.info == {'path': '/D:/x.raml', 'output': '/C:/out/'}

    def test_a_url_is_kept(self):
        node = compose('key: !include x.raml\n', uri='file:///a.raml').content[1]
        url = 'https://example.com/x.raml'
        assert Rebaser('file:///out/joined.raml').relative(url, node, 'file:///a.raml') == url

    def test_an_include_argument_is_plain(self):
        text = write_raml(compose('key: !include a b.raml\n', uri='file:///x.raml'))
        assert 'key: !include a b.raml\n' in text


class TestCommandLine:
    """The `join` verb: exit codes, overrides and configuration (docs/20 § 8)."""

    @pytest.fixture
    def inputs(self, tmp_path):
        write_files(
            tmp_path,
            {
                'a.raml': HEAD + 'baseUri: https://x/a\n/r:\n  get:\n',
                'b.raml': HEAD + '/r:\n  get:\n',
            },
        )
        return tmp_path

    def test_a_join_needs_two_inputs(self, inputs, capsys):
        assert main(['join', str(inputs / 'a.raml')]) == EXIT_INVALID
        assert 'at least two' in capsys.readouterr().err

    def test_a_failed_join_exits_one_and_writes_nothing(self, inputs, capsys):
        out = inputs / 'joined.raml'
        assert main(['join', '-o', str(out), str(inputs / 'a.raml'), str(inputs / 'b.raml')]) == EXIT_INVALID
        assert 'join missing base uri' in capsys.readouterr().err
        assert not out.exists()

    def test_base_uri_supplies_an_inputs_base_uri(self, inputs, capsys):
        arguments = ['join', '--base-uri', f'{inputs / "b.raml"}=https://x/b', str(inputs / 'a.raml')]
        assert main([*arguments, str(inputs / 'b.raml')]) == EXIT_OK
        joined = yaml.safe_load(capsys.readouterr().out)
        assert joined['baseUri'] == 'https://x'
        assert set(joined) == {'title', 'baseUri', '/a', '/b'}

    def test_base_uri_must_name_an_input_and_a_uri(self, inputs, capsys):
        arguments = ['join', '--base-uri', 'https://x/b', str(inputs / 'a.raml'), str(inputs / 'b.raml')]
        assert main(arguments) == EXIT_INVALID
        assert '--base-uri takes INPUT=URI' in capsys.readouterr().err

    def test_the_configuration_gives_title_and_base_uri_parameters(self, inputs, capsys):
        (inputs / 'fastraml.yaml').write_text(
            'join:\n'
            '  title: Joined\n'
            '  inputs:\n'
            '    b.raml:\n'
            '      baseUri: https://x/{tenant}/b\n'
            '      baseUriParameters:\n'
            '        tenant: {pattern: "^[a-z]+$"}\n',
            encoding='utf-8',
        )
        arguments = ['join', '--config', str(inputs / 'fastraml.yaml'), str(inputs / 'a.raml'), str(inputs / 'b.raml')]
        assert main(arguments) == EXIT_OK
        joined = yaml.safe_load(capsys.readouterr().out)
        assert joined['title'] == 'Joined'
        assert joined['/{tenant}/b']['uriParameters'] == {'tenant': {'pattern': '^[a-z]+$'}}
