"""Security schemes: docs/09-security-and-annotations.md § A.

Almost every test here is a rejection, because almost everything the spec says
about security schemes is a constraint on the declaration. The exceptions are
the ones that matter most: `securedBy: [null]` on a method *replacing*
inherited security, scope narrowing, and `describedBy:` shapes reaching the
later passes.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError
from fastraml.domains import DomainLocation

API = '#%RAML 1.0\ntitle: T\nmediaType: application/json\n'

BASIC = 'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
OAUTH2 = (
    'securitySchemes:\n'
    '  oauth:\n'
    '    type: OAuth 2.0\n'
    '    settings:\n'
    '      accessTokenUri: https://example.com/token\n'
    '      authorizationUri: https://example.com/authorize\n'
    '      authorizationGrants: [authorization_code]\n'
    '      scopes: [ADMIN, GUEST]\n'
)


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def parse(workspace, body: str, **options):
    root = workspace({'api.raml': API + body})
    return workspace.parse(root / 'api.raml', ParseOptions(**options) if options else None)


def rejected(workspace, body: str) -> RamlError:
    with pytest.raises(RamlError) as caught:
        parse(workspace, body)
    return caught.value


def keys(error: RamlError) -> list[str]:
    return [trace.message for chain in error.chains() for trace in chain]


def infos(error: RamlError) -> list[dict]:
    return [trace.info for chain in error.chains() for trace in chain if trace.info]


class TestSchemeType:
    def test_the_five_named_types_are_accepted(self, workspace):
        for scheme_type in ('Basic Authentication', 'Digest Authentication', 'Pass Through'):
            raml = parse(workspace, f'securitySchemes:\n  s:\n    type: {scheme_type}\n')
            assert raml.entry_point.security_schemes['s'].type == scheme_type

    def test_an_x_prefixed_type_is_an_extension(self, workspace):
        raml = parse(workspace, 'securitySchemes:\n  s:\n    type: x-custom\n')
        assert raml.entry_point.security_schemes['s'].type == 'x-custom'

    def test_any_other_type_is_rejected(self, workspace):
        # `Cool Authentication` and `n-sls` are both typos, not extensions:
        # only an `x-` prefix declares one (docs/09 § A2).
        error = rejected(workspace, 'securitySchemes:\n  s:\n    type: Cool Authentication\n')
        assert 'unknown security scheme type' in keys(error)
        assert {'type': 'Cool Authentication'} in infos(error)

    def test_a_missing_type_is_rejected(self, workspace):
        assert 'security scheme must declare a type' in keys(rejected(workspace, 'securitySchemes:\n  s:\n    x: 1\n'))

    def test_an_unknown_field_is_rejected(self, workspace):
        error = rejected(workspace, 'securitySchemes:\n  s:\n    type: x-c\n    nonsense: 1\n')
        assert {'field': 'nonsense'} in infos(error)


class TestSettings:
    def test_a_type_with_no_settings_rejects_a_settings_block(self, workspace):
        # `type: Basic Authentication` with an `accessTokenUri` is a
        # misunderstanding, not a no-op (docs/09 § A2).
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: Basic Authentication\n'
            '    settings:\n      accessTokenUri: https://e.com/t\n',
        )
        assert 'security scheme type has no settings' in keys(error)

    def test_a_setting_the_type_does_not_define_is_rejected(self, workspace):
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n'
            '    settings:\n      accessTokenUri: https://e.com/t\n      requestTokenUri: https://e.com/r\n',
        )
        assert {'setting': 'requestTokenUri', 'type': 'OAuth 2.0'} in infos(error)


class TestOAuth1:
    SETTINGS = (
        'securitySchemes:\n  s:\n    type: OAuth 1.0\n    settings:\n'
        '      requestTokenUri: https://e.com/request\n'
        '      authorizationUri: https://e.com/authorize\n'
        '      tokenCredentialsUri: https://e.com/credentials\n'
    )

    def test_the_three_uris_are_enough(self, workspace):
        assert parse(workspace, self.SETTINGS).entry_point.security_schemes['s'].type == 'OAuth 1.0'

    @pytest.mark.parametrize('missing', ['requestTokenUri', 'authorizationUri', 'tokenCredentialsUri'])
    def test_each_uri_is_required(self, workspace, missing):
        body = '\n'.join(line for line in self.SETTINGS.splitlines() if missing not in line) + '\n'
        error = rejected(workspace, body)
        assert {'setting': missing} in infos(error)

    def test_a_known_signature_is_accepted(self, workspace):
        parse(workspace, self.SETTINGS + '      signatures: [HMAC-SHA1, RSA-SHA1, PLAINTEXT]\n')

    def test_an_unknown_signature_is_rejected(self, workspace):
        error = rejected(workspace, self.SETTINGS + '      signatures: [MD5]\n')
        assert {'signature': 'MD5'} in infos(error)


class TestOAuth2:
    def test_a_complete_declaration_is_accepted(self, workspace):
        assert parse(workspace, OAUTH2).entry_point.security_schemes['oauth'].settings.scopes == ['ADMIN', 'GUEST']

    def test_the_access_token_uri_is_required(self, workspace):
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n'
            '    settings:\n      authorizationGrants: [client_credentials]\n',
        )
        assert {'setting': 'accessTokenUri'} in infos(error)

    def test_the_authorization_grants_are_required(self, workspace):
        """Spec § OAuth 2.0 (raml-10.md L2711): the settings table writes
        `authorizationGrants` without `?` (docs/09 § A2).
        """
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n      accessTokenUri: https://e.com/t\n',
        )
        assert infos(error) == [{'setting': 'authorizationGrants'}]

    def test_the_authorization_uri_is_required_only_for_two_grants(self, workspace):
        head = 'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n      accessTokenUri: https://e.com/t\n'
        parse(workspace, head + '      authorizationGrants: [client_credentials]\n')
        error = rejected(workspace, head + '      authorizationGrants: [implicit]\n')
        assert {'setting': 'authorizationUri'} in infos(error)

    @pytest.mark.parametrize('grant', ['authorization_code', 'implicit', 'password', 'client_credentials'])
    def test_the_four_rfc_names_are_accepted(self, workspace, grant):
        parse(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://e.com/t\n'
            '      authorizationUri: https://e.com/a\n'
            f'      authorizationGrants: [{grant}]\n',
        )

    def test_an_absolute_uri_is_an_extension_grant(self, workspace):
        # Spec section Security Scheme Types: an extension grant such as
        # `urn:ietf:params:oauth:grant-type:saml2-bearer` is legal.
        parse(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://e.com/t\n'
            "      authorizationGrants: ['urn:ietf:params:oauth:grant-type:saml2-bearer']\n",
        )

    def test_a_bare_name_that_is_not_one_of_the_four_is_rejected(self, workspace):
        # `refresh_token` is an RFC 6749 *grant type* for the token endpoint,
        # not an authorization grant, and it is not a URI either.
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://e.com/t\n'
            '      authorizationGrants: [refresh_token]\n',
        )
        assert {'grant': 'refresh_token'} in infos(error)

    def test_a_relative_uri_is_not_an_extension_grant(self, workspace):
        error = rejected(
            workspace,
            'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://e.com/t\n'
            "      authorizationGrants: ['example.com']\n",
        )
        assert {'grant': 'example.com'} in infos(error)


def test_a_scheme_built_with_no_source_is_placed_as_every_entity_is():
    # docs/09 § A1: `None` where every other entity says `UNKNOWN` made each
    # consumer guard both.
    from fastraml.parser.security import (
        SecuritySchemeDefinition,
        SecuritySchemeDescription,
        SecuritySchemeSettings,
    )
    from fastraml.positions import UNKNOWN

    definition = SecuritySchemeDefinition(id=1, name='s', location='file:///a.raml')
    settings = SecuritySchemeSettings(scheme_type='OAuth 2.0', location='file:///a.raml')
    description = SecuritySchemeDescription(id=2, location='file:///a.raml')
    assert (definition.key_pos, definition.value_pos, settings.value_pos, description.value_pos) == (UNKNOWN,) * 4


class TestDescribedBy:
    BODY = (
        'securitySchemes:\n  s:\n    type: x-custom\n    describedBy:\n'
        '      headers:\n        X-Token: string\n'
        '      queryParameters:\n        access_token: string\n'
        '      responses:\n        401:\n          description: no\n'
    )

    def test_it_decodes_the_operation_vocabulary(self, workspace):
        described = parse(workspace, self.BODY).entry_point.security_schemes['s'].described_by
        assert list(described.headers) == ['X-Token']
        assert list(described.query_parameters) == ['access_token']
        assert list(described.responses) == ['401']

    def test_its_shapes_reach_the_later_passes(self, workspace):
        # `fragment_typedefs` is the only index P9 and P10 iterate, so a shape
        # that skips it is silently never validated. A bad example is the
        # cheapest proof that P10 saw this one.
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                'securitySchemes:\n  s:\n    type: x-custom\n    describedBy:\n'
                '      headers:\n        X-Token:\n          type: integer\n          example: nope\n',
                unwrap=True,
                validate=True,
            )
        assert 'example' in str(caught.value)

    def test_a_non_mapping_is_rejected(self, workspace):
        assert 'describedBy must be a mapping' in keys(
            rejected(workspace, 'securitySchemes:\n  s:\n    type: x-custom\n    describedBy: nonsense\n')
        )

    def test_an_unknown_key_is_rejected(self, workspace):
        error = rejected(workspace, 'securitySchemes:\n  s:\n    type: x-custom\n    describedBy:\n      HELLO: 123\n')
        assert {'field': 'HELLO'} in infos(error)


class TestSecuredBy:
    def test_a_scheme_name_binds_to_its_definition(self, workspace):
        raml = parse(workspace, BASIC + '/users:\n  get:\n    securedBy: [basic]\n')
        scheme = raml.endpoints['/users'].operations['get'].secured_by[0]
        assert scheme.definition is raml.entry_point.security_schemes['basic']

    def test_an_unknown_name_is_rejected(self, workspace):
        error = rejected(workspace, '/users:\n  get:\n    securedBy: [nowhere]\n')
        assert {'scheme': 'nowhere'} in infos(error)

    def test_a_null_entry_binds_to_a_null_definition(self, workspace):
        # docs/09 § A3: nothing downstream special-cases `None`; it sees a
        # scheme whose type is `null`.
        raml = parse(workspace, '/users:\n  get:\n    securedBy: [~]\n')
        scheme = raml.endpoints['/users'].operations['get'].secured_by[0]
        assert scheme.is_null
        assert scheme.definition.type == 'null'


class TestInheritance:
    """docs/09 § A4: API root, then resource, then method — each replacing."""

    def test_the_api_global_reaches_a_method_that_declares_nothing(self, workspace):
        raml = parse(workspace, BASIC + 'securedBy: [basic]\n/users:\n  get:\n')
        assert [s.name for s in raml.endpoints['/users'].operations['get'].secured_by] == ['basic']

    def test_a_resource_replaces_the_global(self, workspace):
        raml = parse(
            workspace,
            'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
            '  other:\n    type: Digest Authentication\n'
            'securedBy: [basic]\n/users:\n  securedBy: [other]\n  get:\n',
        )
        assert [s.name for s in raml.endpoints['/users'].operations['get'].secured_by] == ['other']

    def test_a_method_with_its_own_is_left_alone(self, workspace):
        raml = parse(
            workspace,
            BASIC + 'securedBy: [basic]\n/users:\n  securedBy: [basic]\n  get:\n    securedBy: []\n',
        )
        assert raml.endpoints['/users'].operations['get'].secured_by == []

    def test_null_on_a_method_removes_inherited_security(self, workspace):
        # The case `explicit_secured_by` exists for. Replacing rather than
        # appending is what makes this work at all.
        raml = parse(workspace, BASIC + 'securedBy: [basic]\n/users:\n  get:\n    securedBy: [~]\n')
        schemes = raml.endpoints['/users'].operations['get'].secured_by
        assert [s.is_null for s in schemes] == [True]

    def test_a_resource_does_not_reach_a_nested_resource(self, workspace):
        # Spec section Applying Security Schemes: "MUST NOT incorporate nested
        # resources".
        raml = parse(workspace, BASIC + '/users:\n  securedBy: [basic]\n  /{id}:\n    get:\n')
        assert raml.endpoints['/users/{id}'].operations['get'].secured_by == []


TWO = 'securitySchemes:\n  basic:\n    type: Basic Authentication\n  digest:\n    type: Digest Authentication\n'


def schemes(raml, uri: str = '/users', method: str = 'get') -> list[str]:
    return [scheme.name for scheme in raml.endpoints[uri].operations[method].secured_by]


class TestTemplates:
    """docs/09 § A4: a trait's `securedBy:` is the method's unless the method
    wrote its own, and a resource type's is the resource's on the same terms.
    """

    def test_a_trait_secures_the_method_it_is_applied_to(self, workspace):
        raml = parse(
            workspace, BASIC + 'traits:\n  secured:\n    securedBy: [basic]\n/users:\n  get:\n    is: [secured]\n'
        )
        get = raml.endpoints['/users'].operations['get']
        assert get.explicit_secured_by
        assert [scheme.definition for scheme in get.secured_by] == [raml.entry_point.security_schemes['basic']]

    def test_the_methods_own_securedby_wins_whole(self, workspace):
        # Replaced, not merged: a method listing `digest` does not also accept `basic`.
        raml = parse(
            workspace,
            TWO + 'traits:\n  secured:\n    securedBy: [basic]\n'
            '/users:\n  get:\n    is: [secured]\n    securedBy: [digest]\n',
        )
        assert schemes(raml) == ['digest']

    def test_the_methods_empty_securedby_still_wins(self, workspace):
        raml = parse(
            workspace,
            BASIC
            + 'traits:\n  secured:\n    securedBy: [basic]\n/users:\n  get:\n    is: [secured]\n    securedBy: []\n',
        )
        assert schemes(raml) == []

    def test_the_closest_trait_wins(self, workspace):
        # The method's `is:` is closer than the resource's (docs/08 § 3.2).
        raml = parse(
            workspace,
            TWO + 'traits:\n  near:\n    securedBy: [basic]\n  far:\n    securedBy: [digest]\n'
            '/users:\n  is: [far]\n  get:\n    is: [near]\n',
        )
        assert schemes(raml) == ['basic']

    def test_a_trait_replaces_the_resources_and_the_apis(self, workspace):
        raml = parse(
            workspace,
            TWO + 'securedBy: [digest]\ntraits:\n  secured:\n    securedBy: [basic]\n'
            '/users:\n  securedBy: [digest]\n  get:\n    is: [secured]\n  post:\n',
        )
        assert (schemes(raml), schemes(raml, method='post')) == (['basic'], ['digest'])

    def test_a_null_entry_from_a_trait_allows_no_scheme(self, workspace):
        raml = parse(
            workspace, BASIC + 'traits:\n  optional:\n    securedBy: [~, basic]\n/users:\n  get:\n    is: [optional]\n'
        )
        assert [scheme.is_null for scheme in raml.endpoints['/users'].operations['get'].secured_by] == [True, False]

    def test_a_parameter_names_the_scheme(self, workspace):
        raml = parse(
            workspace,
            TWO + 'traits:\n  secured:\n    securedBy: [<<scheme>>]\n'
            '/users:\n  get:\n    is: [{secured: {scheme: digest}}]\n',
        )
        assert schemes(raml) == ['digest']

    def test_a_trait_a_resource_type_applies_secures_the_method(self, workspace):
        raml = parse(
            workspace,
            BASIC + 'traits:\n  secured:\n    securedBy: [basic]\n'
            'resourceTypes:\n  collection:\n    get:\n      is: [secured]\n/users:\n  type: collection\n',
        )
        assert schemes(raml) == ['basic']

    def test_a_library_trait_cannot_use_the_apis_import_alias(self, workspace):
        # The importing API's alias is not visible inside the library (docs/04 § 4).
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\n/users:\n  get:\n    is: [lib.secured]\n',
                'lib.raml': '#%RAML 1.0 Library\n' + BASIC + 'traits:\n  secured:\n    securedBy: [lib.basic]\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml')
        assert 'library not found' in keys(caught.value)
        assert {'missing': 'lib'} in infos(caught.value)

    def test_an_unknown_scheme_in_a_trait_is_rejected(self, workspace):
        error = rejected(
            workspace, 'traits:\n  secured:\n    securedBy: [nowhere]\n/users:\n  get:\n    is: [secured]\n'
        )
        assert {'scheme': 'nowhere'} in infos(error)

    def test_a_resource_type_secures_a_resource_with_none_of_its_own(self, workspace):
        raml = parse(
            workspace,
            BASIC + 'resourceTypes:\n  collection:\n    securedBy: [basic]\n    get:\n/users:\n  type: collection\n',
        )
        assert schemes(raml) == ['basic']

    def test_the_resources_own_securedby_replaces_its_resource_types(self, workspace):
        raml = parse(
            workspace,
            TWO + 'resourceTypes:\n  collection:\n    securedBy: [digest]\n    get:\n'
            '/users:\n  type: collection\n  securedBy: [basic]\n',
        )
        assert schemes(raml) == ['basic']


class TestScopeNarrowing:
    """docs/09 § A5 — the only per-application parameter any scheme takes."""

    def test_a_declared_subset_is_accepted(self, workspace):
        raml = parse(workspace, OAUTH2 + '/users:\n  get:\n    securedBy: [{oauth: {scopes: [ADMIN]}}]\n')
        assert raml.endpoints['/users'].operations['get'].secured_by[0].compiled_params == ['ADMIN']

    def test_an_undeclared_scope_is_rejected(self, workspace):
        error = rejected(workspace, OAUTH2 + '/users:\n  get:\n    securedBy: [{oauth: {scopes: [NOBODY]}}]\n')
        assert 'scope is not declared by the security scheme' in keys(error)

    def test_scopes_on_a_non_oauth2_scheme_are_rejected(self, workspace):
        # An error rather than a silent no-op: the author believed it did
        # something.
        error = rejected(workspace, BASIC + '/users:\n  get:\n    securedBy: [{basic: {scopes: [ADMIN]}}]\n')
        assert 'scopes override is only valid for OAuth 2.0 schemes' in keys(error)

    def test_the_definition_is_left_untouched(self, workspace):
        # Two operations narrowing the same scheme differently must not
        # interfere, which is why the result lives on the reference.
        raml = parse(
            workspace,
            OAUTH2
            + '/a:\n  get:\n    securedBy: [{oauth: {scopes: [ADMIN]}}]\n'
            + '/b:\n  get:\n    securedBy: [{oauth: {scopes: [GUEST]}}]\n',
        )
        assert raml.endpoints['/a'].operations['get'].secured_by[0].compiled_params == ['ADMIN']
        assert raml.endpoints['/b'].operations['get'].secured_by[0].compiled_params == ['GUEST']
        assert raml.entry_point.security_schemes['oauth'].settings.scopes == ['ADMIN', 'GUEST']


class TestAnnotationTargets:
    """The two annotation sites security-scheme decoding establishes (docs/09 § B4).

    A missing `target_scope` is silent — the annotation records the enclosing
    site instead — so each new site needs a test that names it.
    """

    DECLARE = 'annotationTypes:\n  ann: any\n'

    def target(self, raml, name: str) -> DomainLocation:
        return next(extension.target for extension in raml.domain_extensions if extension.name == name)

    def test_an_annotation_on_a_scheme_targets_the_scheme(self, workspace):
        raml = parse(workspace, self.DECLARE + 'securitySchemes:\n  s:\n    type: x-c\n    (ann): 1\n')
        assert self.target(raml, 'ann') is DomainLocation.SECURITY_SCHEME

    def test_an_annotation_in_settings_targets_the_settings(self, workspace):
        raml = parse(
            workspace,
            self.DECLARE + 'securitySchemes:\n  s:\n    type: OAuth 2.0\n'
            '    settings:\n      accessTokenUri: https://e.com/t\n      authorizationGrants: [client_credentials]\n'
            '      (ann): 1\n',
        )
        assert self.target(raml, 'ann') is DomainLocation.SECURITY_SCHEME_SETTINGS


class TestLexicalSchemes:
    @pytest.mark.parametrize('kind', ['trait', 'resource-type'])
    @pytest.mark.parametrize('api_scheme', ['', BASIC])
    def test_headerless_template_content_resolves_the_including_library_scheme(self, workspace, kind, api_scheme):
        declaration = 'traits' if kind == 'trait' else 'resourceTypes'
        application = '  get:\n    is: [lib.secured]\n' if kind == 'trait' else '  type: lib.secured\n'
        content = 'securedBy: [basic]\n' if kind == 'trait' else 'get:\n  securedBy: [basic]\n'
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\n' + api_scheme + '/items:\n' + application,
                'lib.raml': '#%RAML 1.0 Library\n' + BASIC + f'{declaration}:\n  secured: !include template.yaml\n',
                'template.yaml': content,
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        reference = raml.endpoints['/items'].operations['get'].secured_by[0]
        assert reference.definition is raml.entry_point.uses['lib'].link.security_schemes['basic']
        assert reference.location == (root / 'template.yaml').as_uri()

    @pytest.mark.parametrize('kind', ['trait', 'resource-type'])
    @pytest.mark.parametrize('qualified', [True, False])
    def test_a_standalone_template_can_see_only_its_own_imports(self, workspace, kind, qualified):
        declaration = 'traits' if kind == 'trait' else 'resourceTypes'
        header = 'Trait' if kind == 'trait' else 'ResourceType'
        application = '  get:\n    is: [secured]\n' if kind == 'trait' else '  type: secured\n'
        name = 'auth.basic' if qualified else 'basic'
        content = f'securedBy: [{name}]\n' if kind == 'trait' else f'get:\n  securedBy: [{name}]\n'
        root = workspace(
            {
                'api.raml': API + BASIC + f'{declaration}:\n  secured: !include template.raml\n/items:\n' + application,
                'template.raml': f'#%RAML 1.0 {header}\nuses:\n  auth: auth.raml\n' + content,
                'auth.raml': '#%RAML 1.0 Library\n' + BASIC,
            }
        )
        if not qualified:
            with pytest.raises(RamlError) as caught:
                workspace.parse(root / 'api.raml')
            assert 'invalid reference' in keys(caught.value)
            assert {'missing': 'basic'} in infos(caught.value)
            return
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        reference = raml.endpoints['/items'].operations['get'].secured_by[0]
        assert reference.definition.location == (root / 'auth.raml').as_uri()

    @pytest.mark.parametrize(
        ('declaration', 'application'),
        [
            ('resourceTypes:\n  secured:\n    get:\n      securedBy: [basic]\n', '  type: lib.secured\n'),
            ('resourceTypes:\n  secured:\n    securedBy: [basic]\n    get:\n', '  type: lib.secured\n'),
            ('traits:\n  secured:\n    securedBy: [basic]\n', '  get:\n    is: [lib.secured]\n'),
        ],
        ids=['resource-type-method', 'resource-type-resource', 'trait'],
    )
    @pytest.mark.parametrize('api_scheme', ['', BASIC])
    def test_a_library_template_uses_its_own_scheme(self, workspace, declaration, application, api_scheme):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\n' + api_scheme + '/items:\n' + application,
                'lib.raml': '#%RAML 1.0 Library\n' + BASIC + declaration,
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        reference = raml.endpoints['/items'].operations['get'].secured_by[0]
        assert reference.definition is raml.entry_point.uses['lib'].link.security_schemes['basic']

    @pytest.mark.parametrize('own_scheme', [True, False])
    def test_a_library_template_resolves_qualified_names_only_through_its_own_imports(self, workspace, own_scheme):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\n  auth: api-auth.raml\n/items:\n  type: lib.secured\n',
                'lib.raml': '#%RAML 1.0 Library\n'
                + ('uses:\n  auth: lib-auth.raml\n' if own_scheme else '')
                + 'resourceTypes:\n  secured:\n    get:\n      securedBy: [auth.basic]\n',
                'api-auth.raml': '#%RAML 1.0 Library\n' + BASIC,
                'lib-auth.raml': '#%RAML 1.0 Library\n' + BASIC,
            }
        )
        if not own_scheme:
            with pytest.raises(RamlError) as caught:
                workspace.parse(root / 'api.raml')
            assert 'library not found' in keys(caught.value)
            assert {'missing': 'auth'} in infos(caught.value)
            return
        raml = workspace.parse(root / 'api.raml')
        reference = raml.endpoints['/items'].operations['get'].secured_by[0]
        owner = raml.entry_point.uses['lib'].link
        assert reference.definition is owner.uses['auth'].link.security_schemes['basic']


class TestFragment:
    def test_a_scheme_arrives_through_an_include(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'securitySchemes:\n  s: !include scheme.raml\n/users:\n  get:\n    securedBy: [s]\n',
                'scheme.raml': (
                    '#%RAML 1.0 SecurityScheme\n'
                    'type: OAuth 2.0\n'
                    'settings:\n'
                    '  accessTokenUri: https://e.com/t\n'
                    '  authorizationGrants: [client_credentials]\n'
                    '  scopes: [ADMIN]\n'
                ),
            }
        )
        raml = workspace.parse(root / 'api.raml')
        scheme = raml.endpoints['/users'].operations['get'].secured_by[0]
        assert scheme.definition.resolved().type == 'OAuth 2.0'

    def test_narrowing_follows_the_link_to_find_the_settings(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'securitySchemes:\n  s: !include scheme.raml\n'
                + '/users:\n  get:\n    securedBy: [{s: {scopes: [NOBODY]}}]\n',
                'scheme.raml': (
                    '#%RAML 1.0 SecurityScheme\n'
                    'type: OAuth 2.0\n'
                    'settings:\n'
                    '  accessTokenUri: https://e.com/t\n'
                    '  authorizationGrants: [client_credentials]\n'
                    '  scopes: [ADMIN]\n'
                ),
            }
        )
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml')
        assert 'scope is not declared by the security scheme' in keys(caught.value)
