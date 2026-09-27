"""Where a value that breaks a constraint is reported (docs/11 § 3).

The chain narrows: the example, default or annotation as written, then the
value inside it that is at fault. The constraint it broke is the frame's
`origin`, in whatever file declared it. Spans are `(line, column, end_line,
end_column)`, 1-based with exclusive ends.
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
