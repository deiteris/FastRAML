"""Security schemes: docs/09-security-and-annotations.md Part A.

Almost every test here is a rejection, because almost everything the spec says
about security schemes is a constraint on the declaration. The exceptions are
the four that matter most: `securedBy: [null]` on a method *removing* inherited
security, scope narrowing, and `describedBy:` shapes reaching the later passes.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path
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


def parse(workspace, body: str, **options):
    root = workspace({'api.raml': API + body})
    return parse_from_path(root / 'api.raml', ParseOptions(**options) if options else None)


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
    """The two sites this phase creates (docs/09 § B5).

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
            '    settings:\n      accessTokenUri: https://e.com/t\n      (ann): 1\n',
        )
        assert self.target(raml, 'ann') is DomainLocation.SECURITY_SCHEME_SETTINGS


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
                    '  scopes: [ADMIN]\n'
                ),
            }
        )
        raml = parse_from_path(root / 'api.raml')
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
                    '  scopes: [ADMIN]\n'
                ),
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'scope is not declared by the security scheme' in keys(caught.value)
