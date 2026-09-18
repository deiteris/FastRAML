"""Model-native backward compatibility -- docs/16-graph.md section 10."""

from __future__ import annotations

from pathlib import Path

from fastraml import ParseOptions, parse_from_path, parse_from_string
from fastraml.views.backward import (
    ApiChanged,
    ItemsSegment,
    OperationAdded,
    OperationChanged,
    OperationContract,
    OperationId,
    OperationRemoved,
    ParameterLocation,
    PropertySegment,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SchemaChanged,
    SecurityLocation,
    TransportLocation,
    UnionMemberSegment,
    backward,
    backward_markdown,
    record,
    render_markdown,
)
from fastraml.views.diff import RULES


def graded(tmp_path, old: str, new: str):
    options = ParseOptions(unwrap=True)
    before = parse_from_string(old, file_name='old.raml', base_dir=tmp_path, options=options)
    after = parse_from_string(new, file_name='new.raml', base_dir=tmp_path, options=options)
    return backward(before, after)


def rules(tmp_path, old: str, new: str) -> set[str]:
    return {change.rule for change in graded(tmp_path, old, new)}


SECURITY = """#%RAML 1.0
title: T
securitySchemes:
  oauth:
    type: OAuth 2.0
    settings:
      accessTokenUri: https://example.test/token
      authorizationGrants: [client_credentials]
      scopes: [read, write]
  key:
    type: Pass Through
/things:
  get:
    securedBy: [oauth]
    responses:
      200:
"""


def test_adding_a_security_alternative_keeps_existing_callers(tmp_path):
    changed = SECURITY.replace('securedBy: [oauth]', 'securedBy: [oauth, key]')
    found = graded(tmp_path, SECURITY, changed)
    alternative = next(change for change in found if change.rule == 'security-alternative-added')
    assert alternative.impact == 'compatible'


def test_removing_a_security_alternative_breaks_its_callers(tmp_path):
    old = SECURITY.replace('securedBy: [oauth]', 'securedBy: [oauth, key]')
    found = graded(tmp_path, old, SECURITY)
    alternative = next(change for change in found if change.rule == 'security-alternative-removed')
    assert alternative.impact == 'breaking'


def test_security_settings_are_not_lost_in_the_graph_projection(tmp_path):
    changed = SECURITY.replace('https://example.test/token', 'https://example.test/v2/token')
    assert 'reference-retargeted' in rules(tmp_path, SECURITY, changed)


def test_described_by_is_compared_as_request_structure(tmp_path):
    old = SECURITY.replace(
        '    type: Pass Through\n',
        '    type: Pass Through\n    describedBy:\n      headers:\n        X-Key: string\n',
    ).replace('securedBy: [oauth]', 'securedBy: [key]')
    new = old.replace('X-Key: string', 'X-Key: integer')
    found = graded(tmp_path, old, new)
    assert any(
        isinstance(change, SchemaChanged)
        and change.rule == 'type-changed'
        and isinstance(change.location, ParameterLocation)
        and change.location.name == 'X-Key'
        for change in found
    )


def test_parameter_contract_and_shape_changes_are_separate(tmp_path):
    old = '#%RAML 1.0\ntitle: T\n/items:\n  get:\n    queryParameters:\n      limit?: string\n'
    new = old.replace('limit?: string', 'limit: integer')

    found = graded(tmp_path, old, new)

    required = next(change for change in found if change.attribute == 'required')
    shape = next(change for change in found if change.attribute == 'type')
    assert isinstance(required, OperationChanged)
    assert isinstance(required.location, ParameterLocation)
    assert 'path' not in record(required)
    assert isinstance(shape, SchemaChanged)
    assert isinstance(shape.location, ParameterLocation)
    assert shape.path == ()
    assert record(shape)['path'] == []


RECURSIVE = """#%RAML 1.0
title: T
types:
  Node:
    type: object
    properties:
      value: string
      next?: Node
/nodes:
  get:
    responses:
      200:
        body:
          application/json: Node
"""


def test_an_unchanged_recursive_contract_needs_no_cycle_algorithm(tmp_path):
    assert (
        backward(
            parse_from_string(RECURSIVE, file_name='old.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True)),
            parse_from_string(RECURSIVE, file_name='new.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True)),
        )
        == []
    )


def test_a_change_before_a_recursion_marker_is_still_found(tmp_path):
    changed = RECURSIVE.replace('value: string', 'value: integer')
    assert 'type-changed' in rules(tmp_path, RECURSIVE, changed)


def test_reordering_union_members_is_not_a_contract_change(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Result:
    type: object
    properties:
      value: string | integer
/result:
  get:
    responses:
      200:
        body:
          application/json: Result
"""
    new = old.replace('string | integer', 'integer | string')

    assert graded(tmp_path, old, new) == []


def test_large_numeric_bounds_are_compared_exactly(tmp_path):
    old = RECURSIVE.replace('value: string', 'value:\n        type: integer\n        maximum: 9007199254740992')
    new = old.replace('maximum: 9007199254740992', 'maximum: 9007199254740993')
    found = graded(tmp_path, old, new)
    bound = next(change for change in found if isinstance(change, SchemaChanged) and change.attribute == 'maximum')
    assert bound.rule == 'response-constraint-loosened'
    assert bound.impact == 'breaking'


def test_markdown_names_the_property_a_facet_constrains(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    properties:
      code:
        type: string
        maxLength: 10
/items:
  post:
    body:
      application/json: Payload
"""
    new = old.replace('maxLength: 10', 'maxLength: 5')
    options = ParseOptions(unwrap=True)
    before = parse_from_string(old, file_name='old.raml', base_dir=tmp_path, options=options)
    after = parse_from_string(new, file_name='new.raml', base_dir=tmp_path, options=options)

    report = backward_markdown(before, after)
    assert '| Request body `application/json` | `$.code` | maxLength | 10 | 5 | Breaking |' in report


def test_base_uri_change_is_breaking(tmp_path):
    old = '#%RAML 1.0\ntitle: T\nbaseUri: https://old.example.test\n'
    new = old.replace('old.example.test', 'new.example.test')
    found = graded(tmp_path, old, new)
    assert found == [
        ApiChanged(
            kind='changed',
            subject='base-uri',
            attribute='baseUri',
            before='https://old.example.test',
            after='https://new.example.test',
            impact='breaking',
            rule='base-uri-changed',
        )
    ]


def test_a_resource_without_operations_is_not_caller_surface(tmp_path):
    old = '#%RAML 1.0\ntitle: T\n/container:\n  /child:\n'
    new = '#%RAML 1.0\ntitle: T\n'
    assert graded(tmp_path, old, new) == []


def test_an_added_resource_is_reported_through_its_operations(tmp_path):
    old = '#%RAML 1.0\ntitle: T\n'
    new = """#%RAML 1.0
title: T
/things:
  get:
    displayName: List things
    description: Lists every thing available to the caller.
    responses:
      200:
"""
    found = graded(tmp_path, old, new)
    assert len(found) == 1
    assert isinstance(found[0], OperationAdded)
    assert found[0].operation.path == '/things'
    assert found[0].display_name == 'List things'
    assert found[0].description == 'Lists every thing available to the caller.'

    options = ParseOptions(unwrap=True)
    before = parse_from_string(old, file_name='old.raml', base_dir=tmp_path, options=options)
    after = parse_from_string(new, file_name='new.raml', base_dir=tmp_path, options=options)
    report = backward_markdown(before, after)
    assert (
        '### Added operations\n\n'
        '- `GET /things` - **List things** - Lists every thing available to the caller.' in report
    )
    assert '## `GET /things`' not in report


def test_a_removed_operation_retains_its_description(tmp_path):
    old = """#%RAML 1.0
title: T
/legacy:
  delete:
    displayName: Delete legacy record
    description: Permanently removes the legacy record.
"""
    new = '#%RAML 1.0\ntitle: T\n'

    found = graded(tmp_path, old, new)

    assert found == [
        OperationRemoved(
            OperationId('/legacy', 'delete'),
            display_name='Delete legacy record',
            description='Permanently removes the legacy record.',
        )
    ]


def test_markdown_summarizes_and_escapes_operation_metadata():
    description = 'First | line with *markup* and <b>HTML</b>\nSecond line must not appear'
    change = OperationAdded(
        OperationId('/things/`raw`', 'get'),
        display_name='Find | *things*',
        description=description,
    )

    report = render_markdown([change])

    assert '`` GET /things/`raw` ``' in report
    assert '**Find | \\*things\\***' in report
    assert 'First | line with \\*markup\\* and \\<b\\>HTML\\</b\\>...' in report
    assert 'Second line must not appear' not in report
    assert record(change)['description'] == description


def test_markdown_quotes_hostile_schema_paths_and_table_values():
    change = SchemaChanged(
        operation=OperationId('/things', 'get'),
        location=ResponseBody('200', 'application/vnd.test|json'),
        path=(PropertySegment('user.name|`raw`'),),
        kind='changed',
        subject='constraint',
        attribute='pattern',
        before='old|pattern',
        after='new\r\npattern',
        impact='review',
        rule='other',
    )

    report = render_markdown([change])

    assert 'application/vnd.test\\|json' in report
    assert '$.user.name' not in report
    assert '``$["user.name\\|`raw`"]``' in report
    assert '| old\\|pattern | new pattern | Review |' in report


def test_markdown_summarizes_description_changes_but_json_does_not():
    before = '\n\nOld summary\nOld detail'
    after = 'New summary\nNew detail'
    change = OperationChanged(
        operation=OperationId('/things', 'get'),
        location=OperationContract(),
        kind='changed',
        subject='description',
        attribute='description',
        before=before,
        after=after,
        impact='cosmetic',
        rule='documentation-changed',
    )

    report = render_markdown([change])

    assert '| description | Old summary... | New summary... | Cosmetic |' in report
    assert 'Old detail' not in report
    assert record(change)['before'] == before
    assert record(change)['after'] == after


def test_markdown_bounds_a_single_long_description_line():
    description = 'x' * 200
    change = OperationAdded(OperationId('/things', 'get'), description=description)

    report = render_markdown([change])

    assert f'{"x" * 157}...' in report
    assert description not in report
    assert record(change)['description'] == description


def test_the_worked_example_is_a_readable_end_to_end_report():
    root = Path(__file__).parents[2] / 'examples' / 'compatibility'
    options = ParseOptions(unwrap=True)
    old = parse_from_path(root / 'v1.raml', options)
    new = parse_from_path(root / 'v2.raml', options)

    changes = backward(old, new)
    report = backward_markdown(old, new)

    # `reference-dropped` describes a raw graph edge and has no effective-model
    # counterpart. Every compatibility rule is exercised by this one report.
    assert {change.rule for change in changes} == set(RULES) - {'reference-dropped'}
    assert '# API compatibility' in report
    assert '## `POST /request-required`' in report
    assert '## `GET /response-enum-add`' in report
    assert '| Request body `application/json` | `$.profile.nickname` | maxLength |' in report
    assert '| Response `200` body `application/json` | `$.records[].state` | enum |' in report
    assert '| Security | Setting `accessTokenUri` |' in report
    assert (
        '| Response `200` body `application/json` | `$.productCode` | pattern | '
        '^\\[A-Z\\]+$ | ^\\[a-z\\]+$ | Review |' in report
    )
    assert 'accessTokenUri' in report
    assert '### Method contract' in report
    assert '### Schemas' in report
    assert '| Response `410` | Response removed | present | absent | Breaking |' in report
    assert '| Response `202` | Response added | absent | present | Compatible |' in report
    assert '| query parameter `limit` | Requiredness | Optional | Required | Breaking |' in report
    assert '| query parameter `limit` | `$` | type | string | integer | Breaking |' in report
    assert '| Request body `application/vnd.legacy+json` | mediaType | application/vnd.legacy+json | Absent |' in report
    assert '| Response `200` body `application/vnd.example+json` | mediaType | Absent |' in report
    assert '| Response `200` body `application/json` | `$.result<integer>` | Union member removed |' in report
    assert '| Response `200` body `application/json` | `$.result<boolean>` | Union member added |' in report

    operation_locations = {type(change.location) for change in changes if isinstance(change, OperationChanged)}
    assert operation_locations == {
        OperationContract,
        ParameterLocation,
        RequestBody,
        ResponseBody,
        ResponseStatus,
        SecurityLocation,
        TransportLocation,
    }
    schema_locations = {type(change.location) for change in changes if isinstance(change, SchemaChanged)}
    assert schema_locations == {ParameterLocation, RequestBody, ResponseBody}
    schema_segments = {
        type(segment) for change in changes if isinstance(change, SchemaChanged) for segment in change.path
    }
    assert schema_segments == {PropertySegment, ItemsSegment, UnionMemberSegment}
    assert any(isinstance(change, SchemaChanged) and change.path == () for change in changes)
    removed_member = next(
        change
        for change in changes
        if isinstance(change, SchemaChanged) and change.operation.path == '/union-members' and change.kind == 'removed'
    )
    assert removed_member.path[-1] == UnionMemberSegment(name=None, type='integer')
    assert record(removed_member)['path'][-1] == {
        'kind': 'UnionMemberSegment',
        'name': None,
        'type': 'integer',
        'occurrence': 1,
    }

    protocol = next(change for change in changes if change.rule == 'protocol-removed')
    nested_shape = next(
        change
        for change in changes
        if isinstance(change, SchemaChanged)
        and change.operation.path == '/response-enum-add'
        and change.attribute == 'enum'
    )
    assert isinstance(protocol, OperationChanged)
    assert record(protocol)['scope'] == 'operation'
    assert 'path' not in record(protocol)
    assert record(nested_shape)['scope'] == 'schema'
    assert record(nested_shape)['path'] == [
        {'kind': 'PropertySegment', 'name': 'records'},
        {'kind': 'ItemsSegment'},
        {'kind': 'PropertySegment', 'name': 'state'},
    ]
    assert 'Callers read a field that has gone' not in report
