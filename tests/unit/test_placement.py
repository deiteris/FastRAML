"""Where a fault is reported: at the node at fault, never a construct that
holds it (docs/11 § 1, § 3).

For a value, the chain narrows from the example, default or annotation as
written to the value inside it; the constraint it broke is the frame's
`origin`, in whatever file declared it (§ 3.1). Spans are `(line, column,
end_line, end_column)`, 1-based with exclusive ends.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions

API = '#%RAML 1.0\ntitle: T\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def span(position) -> tuple[int, int, int, int]:
    return (position.line, position.column, position.end_line, position.end_column)


def innermost(workspace, files: dict[str, str]) -> dict[str, object]:
    """The innermost frame of each chain, by its message."""
    root = workspace(files)
    _raml, error = workspace.lenient(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
    assert error is not None
    return {chain[-1].message: chain[-1] for chain in error.chains()}


def test_a_value_is_reported_at_the_value_and_its_constraint_at_the_facet(workspace):
    found = innermost(workspace, {'api.raml': API + 'types:\n  Name:\n    minLength: 5\n    example: d\n'})
    frame = found['value is too short']
    assert frame.info['path'] == '$'
    assert span(frame.position) == (6, 14, 6, 15)
    assert frame.origin.message == 'declared here'
    assert span(frame.origin.position) == (5, 5, 5, 17)


def test_a_nested_value_is_found_by_its_path_and_an_extra_property_by_its_key(workspace):
    document = API + (
        'types:\n'
        '  Person:\n'
        '    additionalProperties: false\n'
        '    properties:\n'
        '      tags:\n'
        '        type: string[]\n'
        '        maxItems: 1\n'
        '    example:\n'
        '      tags: [a, b]\n'
        '      extra: 1\n'
    )
    found = innermost(workspace, {'api.raml': document})
    items, extra = found['too many items'], found['additional properties are not allowed']
    assert (span(items.position), span(items.origin.position)) == ((11, 13, 11, 19), (9, 9, 9, 20))
    assert (span(extra.position), span(extra.origin.position)) == ((12, 7, 12, 12), (5, 5, 5, 32))


def test_an_inherited_constraint_is_placed_in_the_file_that_declared_it(workspace):
    files = {
        'lib.raml': '#%RAML 1.0 Library\ntypes:\n  Short:\n    maxLength: 2\n',
        'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  Code:\n    type: lib.Short\n    example: abc\n',
    }
    frame = innermost(workspace, files)['value is too long']
    assert (frame.location.rsplit('/', 1)[1], span(frame.position)) == ('api.raml', (8, 14, 8, 17))
    assert (frame.origin.location.rsplit('/', 1)[1], span(frame.origin.position)) == ('lib.raml', (4, 5, 4, 17))


def test_an_included_example_is_reported_at_the_include_then_inside_its_file(workspace):
    files = {
        'ex.yaml': 'name: 1\n',
        'api.raml': API + 'types:\n  P:\n    properties:\n      name: string\n    example: !include ex.yaml\n',
    }
    root = workspace(files)
    _raml, error = workspace.lenient(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
    (chain,) = error.chains()
    outer, inner = chain
    assert (outer.message, outer.location.rsplit('/', 1)[1], outer.position.line) == ('invalid example', 'api.raml', 7)
    assert (inner.message, inner.location.rsplit('/', 1)[1], span(inner.position)) == (
        'invalid type',
        'ex.yaml',
        (1, 7, 1, 8),
    )


@pytest.mark.parametrize(
    ('body', 'value'),
    [
        pytest.param(
            'annotationTypes:\n  short:\n    maxLength: 2\n/r:\n  (short): abc\n', (7, 12, 7, 15), id='annotation'
        ),
        pytest.param('types:\n  C:\n    maxLength: 2\n    default: abc\n', (6, 14, 6, 17), id='default'),
        pytest.param('types:\n  C:\n    maxLength: 2\n    enum: [ab, abc]\n', (6, 16, 6, 19), id='enum member'),
    ],
)
def test_every_value_check_reports_at_the_value(workspace, body, value):
    frame = innermost(workspace, {'api.raml': API + body})['value is too long']
    assert span(frame.position) == value
    assert span(frame.origin.position) == (5, 5, 5, 17)


def test_a_json_schema_failure_is_placed_at_the_instance_it_names(workspace):
    document = API + (
        'types:\n'
        '  J:\n'
        '    type: |\n'
        '      {"type": "object", "properties": {"n": {"type": "string"}}}\n'
        '    example:\n'
        '      n: 1\n'
    )
    frame = innermost(workspace, {'api.raml': document})['value does not match the JSON schema']
    assert frame.info['path'] == '$.n'
    assert span(frame.position) == (8, 10, 8, 11)


def test_an_unknown_discriminator_value_is_placed_at_the_value(workspace):
    document = API + (
        'types:\n'
        '  Pet:\n'
        '    discriminator: kind\n'
        '    properties:\n'
        '      kind: string\n'
        '  Cat:\n'
        '    type: Pet\n'
        '    example:\n'
        '      kind: Horse\n'
    )
    frame = innermost(workspace, {'api.raml': document})['discriminator value names no known type']
    assert span(frame.position) == (11, 13, 11, 18)


class TestTemplateParameters:
    """An application is where a parameter fault is fixed; the template, where it is used."""

    def test_a_missing_parameter_is_placed_at_the_application_beside_its_first_use(self, workspace):
        document = API + (
            'resourceTypes:\n  searchable:\n    get:\n      description: by <<field>>\n/books:\n  type: searchable\n'
        )
        frame = innermost(workspace, {'api.raml': document})['missing required parameter']
        assert frame.info == {'parameter': 'field'}
        assert span(frame.position) == (8, 9, 8, 19)
        assert (frame.origin.message, span(frame.origin.position)) == ('used here', (6, 23, 6, 32))

    def test_an_unexpected_parameter_is_placed_at_its_value_beside_the_template(self, workspace):
        document = API + ('traits:\n  paged:\n    description: paged\n/books:\n  get:\n    is: [{paged: {size: 10}}]\n')
        frame = innermost(workspace, {'api.raml': document})['unexpected parameter']
        assert span(frame.position) == (8, 25, 8, 27)
        assert (frame.origin.message, span(frame.origin.position)) == ('declared here', (4, 3, 4, 8))


def where(document: str, needle: str) -> tuple[int, int, int, int]:
    """The span of the one `needle` in `document`."""
    assert document.count(needle) == 1, needle
    before = document[: document.index(needle)]
    line, column = before.count('\n') + 1, len(before.rpartition('\n')[2]) + 1
    return (line, column, line, column + len(needle))


OAUTH1 = (
    'securitySchemes:\n  o:\n    type: OAuth 1.0\n    settings:\n      requestTokenUri: https://a/b\n'
    '      authorizationUri: https://a/c\n      tokenCredentialsUri: https://a/d\n'
    '      signatures: [HMAC-SHA1, HI]\n'
)
OAUTH2 = (
    'securitySchemes:\n  o:\n    type: OAuth 2.0\n    settings:\n      accessTokenUri: https://a/t\n'
    '      authorizationGrants: [client_credentials, nope]\n'
)


@pytest.mark.parametrize(
    ('body', 'message', 'needle'),
    [
        pytest.param(
            'types:\n  A: string\nschemas:\n  B: string\n',
            'types and schemas are mutually exclusive',
            'schemas',
            id='schemas',
        ),
        pytest.param(
            '/r:\n  get:\n    queryString:\n      type: object\n    queryParameters:\n      a: string\n',
            'queryString and queryParameters are mutually exclusive',
            'queryParameters',
            id='query',
        ),
        pytest.param(OAUTH1, 'unknown signature', 'HI', id='signature'),
        pytest.param(OAUTH2, 'unknown authorization grant', 'nope', id='grant'),
        pytest.param(
            '/{id}:\n  uriParameters:\n    id:\n      example: a/b\n',
            'uri parameter value must not contain a slash',
            'a/b',
            id='slash',
        ),
    ],
)
def test_a_fault_is_placed_at_the_node_that_holds_it(workspace, body, message, needle):
    document = API + body
    frame = innermost(workspace, {'api.raml': document})[message]
    assert span(frame.position) == where(document, needle)


def test_a_missing_title_is_placed_at_the_header(workspace):
    frame = innermost(workspace, {'api.raml': '#%RAML 1.0\nversion: v1\n/r:\n  get:\n'})['title is required']
    assert span(frame.position) == (1, 1, 1, 11)


def test_a_missing_custom_facet_is_placed_at_the_type_beside_its_declaration(workspace):
    document = API + 'types:\n  Base:\n    facets:\n      unit: string\n  Sub:\n    type: Base\n'
    frame = innermost(workspace, {'api.raml': document})['required custom facet is missing']
    assert span(frame.position) == where(document, 'Sub')
    assert span(frame.origin.position) == where(document, 'unit')
