"""Security recovery leaves the rest of the API usable (docs/09 § A7)."""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, Stage
from tests.diagnostics import keys

API = '#%RAML 1.0\ntitle: T\n'
BOTH = ParseOptions(unwrap=True, validate=True)
MODEL = (
    'types:\n  Name: {type: string, minLength: 2}\n  User:\n    properties:\n      name: Name\n'
    '/users:\n  get:\n    responses:\n      200:\n        body:\n          application/json: User\n'
)


@pytest.mark.parametrize(
    ('definition', 'message'),
    [
        ('type: Nope', 'unknown security scheme type'),
        ('description: Missing type', 'security scheme must declare a type'),
        ('type: OAuth 2.0', 'security scheme setting is required'),
        ('type: Basic Authentication\n    settings: {scopes: [read]}', 'security scheme type has no settings'),
        ('type: Basic Authentication\n    describedBy: nope', 'describedBy must be a mapping'),
        ('type: Basic Authentication\n    unknown: true', 'unknown field'),
    ],
)
def test_a_broken_definition_keeps_endpoints_and_effective_types(workspace, definition, message):
    root = workspace(
        {
            'api.raml': API + f'securitySchemes:\n  bad:\n    {definition}\n'
            '  good: {type: Basic Authentication}\nsecuredBy: [bad, good]\n' + MODEL
        }
    )
    with pytest.raises(RamlError) as caught:
        workspace.parse(root / 'api.raml', BOTH)
    raml, error = workspace.lenient(root / 'api.raml', BOTH)
    assert error is not None
    assert error.to_dict() == caught.value.to_dict()
    assert message in keys(error)
    assert raml.stopped_at is None
    assert raml.completed == list(Stage)
    assert raml.unwrapped
    user = raml.entry_point.types['User']
    assert user.shape.properties['name'].base.shape.min_length.value == 2
    operation = raml.endpoints['/users'].operations['get']
    body = operation.responses['200'].bodies['application/json'].shape
    assert body.validate({'name': 'Alice'}) is None
    assert body.validate({'name': 'A'}) is not None
    bad, good = operation.secured_by
    assert bad.definition is raml.entry_point.security_schemes['bad']
    assert set(raml.broken) >= {bad.id, bad.definition.id}
    assert good.definition is raml.entry_point.security_schemes['good']
    assert good.id not in raml.broken
    assert operation.id not in raml.broken
    assert raml.recovered_errors is None


@pytest.mark.parametrize('header', ['', '#%RAML 1.0 SecurityScheme\n'])
def test_a_broken_include_keeps_its_partial_definition(workspace, header):
    root = workspace(
        {
            'api.raml': API + 'securitySchemes:\n  bad: !include scheme.raml\n'
            '  again: !include scheme.raml\nsecuredBy: [bad, again]\n' + MODEL,
            'scheme.raml': header + 'type: OAuth 2.0\nsettings:\n  accessTokenUri: https://e.test/token\n',
        }
    )
    raml, error = workspace.lenient(root / 'api.raml', BOTH)
    assert error is not None
    assert keys(error).count('security scheme setting is required') == 1
    assert raml.completed == list(Stage)
    for reference in raml.global_secured_by:
        definition = reference.definition
        assert definition.link is not None
        assert definition.resolved().type == 'OAuth 2.0'
        assert definition.resolved().settings.values['accessTokenUri'].value == 'https://e.test/token'
        assert reference.id in raml.broken


def test_a_broken_library_scheme_keeps_library_types_and_templates(workspace):
    root = workspace(
        {
            'api.raml': API + 'uses:\n  lib: lib.raml\n/users:\n  get:\n    is: [lib.secured]\n',
            'lib.raml': '#%RAML 1.0 Library\nsecuritySchemes:\n  bad: {type: Nope}\n'
            'types:\n  Name: {type: string, minLength: 2}\ntraits:\n  secured:\n'
            '    securedBy: [bad]\n    queryParameters: {name: Name}\n',
        }
    )
    raml, error = workspace.lenient(root / 'api.raml', BOTH)
    assert keys(error) == ['unknown security scheme type']
    assert raml.completed == list(Stage)
    operation = raml.endpoints['/users'].operations['get']
    assert operation.request.query_parameters['name'].base.shape.min_length.value == 2
    assert operation.secured_by[0].id in raml.broken


@pytest.mark.parametrize(
    ('declaration', 'message'),
    [
        ('securitySchemes: [bad]\n', 'SecurityScheme declarations must be a mapping'),
        ('securitySchemes:\n  bad: !include missing.raml\n', 'file not found'),
        ('securitySchemes:\n  bad: 42\n', 'security scheme definition must be a mapping'),
    ],
)
def test_an_unusable_definition_or_container_still_keeps_the_api(workspace, declaration, message):
    raml, error = workspace.lenient_document(API + declaration + 'securedBy: [bad]\n' + MODEL, BOTH)
    assert message in keys(error)
    assert raml.completed == list(Stage)
    assert '/users' in raml.endpoints
    assert raml.entry_point.types['User'].validate({'name': 'Alice'}) is None
    assert raml.global_secured_by[0].id in raml.broken


def test_unknown_schemes_and_bad_scopes_do_not_block_type_resolution(workspace):
    raml, error = workspace.lenient_document(
        API + 'securitySchemes:\n  good: {type: Basic Authentication}\n'
        'securedBy: [missing, {good: {scopes: [read]}}]\n' + MODEL,
        BOTH,
    )
    assert 'get security scheme definition' in keys(error)
    assert 'scopes override is only valid for OAuth 2.0 schemes' in keys(error)
    assert raml.completed == list(Stage)
    assert raml.entry_point.types['User'].validate({'name': 'Alice'}) is None
    assert all(reference.id in raml.broken for reference in raml.global_secured_by)


def test_a_later_failure_keeps_the_original_security_diagnostic(workspace):
    raml, error = workspace.lenient_document(
        API + 'securitySchemes:\n  bad: {type: Nope}\n' + MODEL + '(missing): true\n', BOTH
    )
    assert keys(error) == ['unknown security scheme type', 'reference not found']
    assert raml.stopped_at is None


def test_a_broken_definition_does_not_report_cascading_scope_errors(workspace):
    raml, error = workspace.lenient_document(
        API + 'securitySchemes:\n  bad:\n    type: OAuth 2.0\n'
        '    settings:\n      accessTokenUri: https://e.test/token\n'
        'securedBy: [{bad: {scopes: [read]}}]\n' + MODEL,
        BOTH,
    )
    assert keys(error) == ['security scheme setting is required']
    assert raml.completed == list(Stage)
    reference = raml.global_secured_by[0]
    assert reference.compiled_params is None
    assert reference.id in raml.broken


def test_a_later_prerequisite_failure_keeps_recovered_security_diagnostics(workspace):
    raml, error = workspace.lenient_document(
        API + 'securitySchemes:\n  bad: {type: Nope}\ntypes:\n  MissingType: Missing\n/r:\n  get:\n',
        BOTH,
    )
    assert keys(error) == ['unknown security scheme type', 'resolve shape', 'reference not found']
    assert raml.stopped_at is Stage.RESOLVED
    assert raml.completed == list(Stage)[:3]
    assert '/r' in raml.endpoints
