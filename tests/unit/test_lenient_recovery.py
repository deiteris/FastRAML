"""Safe recovery boundaries, and the prerequisite failures that still stop (docs/11 § 2)."""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, Stage
from tests.diagnostics import infos, keys

API = '#%RAML 1.0\ntitle: T\n'
BOTH = ParseOptions(unwrap=True, validate=True)
MODEL = 'types:\n  Word: {type: string, minLength: 2}\n/r:\n  get:\n    queryParameters: {q: Word}\n'


@pytest.mark.parametrize('header', [API, '#%RAML 1.0 Library\n'])
def test_unknown_root_fields_are_diagnostics_not_model_prerequisites(workspace, header):
    source = header + 'unknown: true\n' + (MODEL if header == API else 'types:\n  Word: string\n')
    with pytest.raises(RamlError) as caught:
        workspace.document(source, BOTH)
    raml, error = workspace.lenient_document(source, BOTH)
    assert error.to_dict() == caught.value.to_dict()
    assert infos(error) == [{'field': 'unknown'}]
    assert raml.completed == list(Stage)
    assert raml.stopped_at is None
    assert raml.entry_point.types['Word'].validate('Alice') is None
    if header == API:
        assert '/r' in raml.endpoints


@pytest.mark.parametrize('body', ['/r:\n  unknown: true\n  get:\n', '/r:\n  get:\n    unknown: true\n'])
def test_endpoint_field_errors_do_not_block_effective_types(workspace, body):
    raml, error = workspace.lenient_document(API + 'types:\n  Word: {type: string, minLength: 2}\n' + body, BOTH)
    assert keys(error) == ['unknown field']
    assert raml.completed == list(Stage)
    assert raml.entry_point.types['Word'].validate('A') is not None
    assert '/r' in raml.endpoints
    assert raml.endpoints['/r'].id in raml.broken


@pytest.mark.parametrize('facet', ['headers', 'queryParameters'])
def test_one_malformed_parameter_keeps_good_parameters_on_both_sides(workspace, facet):
    raml, error = workspace.lenient_document(
        API + f'/r:\n  get:\n    {facet}:\n'
        '      before: {type: string, minLength: 2}\n'
        '      bad: {type: string, minLength: two}\n'
        '      after: integer\n',
        BOTH,
    )
    assert keys(error) == ['expected an integer value']
    operation = raml.endpoints['/r'].operations['get']
    parameters = operation.request.headers if facet == 'headers' else operation.request.query_parameters
    assert list(parameters) == ['before', 'after']
    assert parameters['before'].base.shape.min_length.value == 2
    assert parameters['after'].base.type == 'integer'
    assert operation.id in raml.broken
    assert raml.stopped_at is Stage.ENDPOINTS
    assert raml.completed == [Stage.DECODED]


def test_unknown_annotations_do_not_block_unwrap_or_report_again_in_validation(workspace):
    raml, error = workspace.lenient_document(API + '(missing): 1\n' + MODEL, BOTH)
    assert len(list(error.chains())) == 1
    assert infos(error) == [{'annotation': 'missing'}]
    assert raml.completed == list(Stage)
    assert raml.domain_extensions[0].id in raml.broken
    assert raml.entry_point.types['Word'].validate('A') is not None


@pytest.mark.parametrize(
    ('declaration', 'message'),
    [
        ('type: string\n    misspelled: 1', 'unknown facet'),
        ('type: integer\n    example: nope', 'invalid example'),
        ('type: integer\n    default: nope', 'invalid default'),
        ('type: string\n    minLength: 4\n    maxLength: 2', 'minLength exceeds maxLength'),
    ],
)
def test_validation_errors_keep_the_decoded_model(workspace, declaration, message):
    raml, error = workspace.lenient_document(API + f'types:\n  Bad:\n    {declaration}\n/r:\n  get:\n', BOTH)
    assert message in keys(error)
    assert raml.stopped_at is Stage.VALIDATED
    assert raml.completed == list(Stage)[:-1]
    assert '/r' in raml.endpoints
    assert raml.entry_point.types['Bad'].shape is not None
    assert raml.broken == {}


def test_unknown_type_facets_are_not_rejected_during_decoding(workspace):
    raml, error = workspace.lenient_document(
        API + 'types:\n  Word: {type: string, misspelled: 1}\n/r:\n  get:\n', ParseOptions(unwrap=True)
    )
    assert error is None
    assert raml.entry_point.types['Word'].custom_facets['misspelled'].raw == 1
    assert '/r' in raml.endpoints


@pytest.mark.parametrize(
    'declaration',
    ['type: string\n    minLength: two', 'type: string\n    description: {wrong: kind}', 'type: {wrong: kind}'],
)
def test_unsettled_or_incomplete_type_decoding_remains_a_stopping_boundary(workspace, declaration):
    raml, error = workspace.lenient_document(API + f'types:\n  Bad:\n    {declaration}\n/r:\n  get:\n', BOTH)
    assert error is not None
    assert raml.stopped_at is Stage.DECODED
    assert raml.completed == []
    assert raml.entry_point.types['Bad'].id in raml.broken


def test_resolution_failure_still_blocks_unwrap_without_cascading_errors(workspace):
    raml, error = workspace.lenient_document(API + 'unknown: true\ntypes:\n  Bad: Missing\n/r:\n  get:\n', BOTH)
    assert keys(error) == ['unknown field', 'resolve shape', 'reference not found']
    assert raml.stopped_at is Stage.RESOLVED
    assert raml.completed == list(Stage)[:3]
