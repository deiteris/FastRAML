"""Model-native backward compatibility -- docs/16-graph.md section 10."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastraml import ParseOptions, parse_from_path, parse_from_string
from fastraml.config import CompatibilityConfig, CompatibilityMatch, CompatibilityRuleSetting
from fastraml.views.backward import (
    RULE_IDS,
    SUBJECTS,
    ApiChanged,
    ApiSchemaChanged,
    Change,
    ItemsSegment,
    OperationAdded,
    OperationChanged,
    OperationContract,
    OperationId,
    OperationRemoved,
    ParameterLocation,
    PatternPropertySegment,
    PropertySegment,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SchemaChanged,
    SecurityLocation,
    TransportLocation,
    UnionMemberSegment,
    _location_label,
    _path_label,
    backward,
    backward_markdown,
    configure,
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


def test_added_security_alternatives_preserve_declaration_order(tmp_path):
    old = SECURITY.replace(
        '  key:\n    type: Pass Through\n', '  key:\n    type: Pass Through\n  basic:\n    type: Basic Authentication\n'
    )
    new = old.replace('securedBy: [oauth]', 'securedBy: [oauth, basic, key]')

    found = graded(tmp_path, old, new)

    added = [change for change in found if change.subject == 'security-alternative' and change.kind == 'added']
    assert [change.after['name'] for change in added] == ['basic', 'key']


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


def test_base_uri_parameter_changes_are_api_owned_schema_changes(tmp_path):
    old = """#%RAML 1.0
title: T
baseUri: https://{tenant}.example.test
baseUriParameters:
  tenant:
    type: string
    maxLength: 20
"""
    new = old.replace('maxLength: 20', 'maxLength: 10')

    found = graded(tmp_path, old, new)

    assert len(found) == 1
    change = found[0]
    assert isinstance(change, ApiSchemaChanged)
    assert change.location == ParameterLocation('baseUri', 'tenant')
    assert change.path == ()
    assert change.rule == 'request-constraint-tightened'
    assert change.impact == 'breaking'
    assert record(change)['scope'] == 'api-schema'
    assert 'operation' not in record(change)


def test_pattern_properties_are_compared_at_their_own_schema_path(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Labels:
    type: object
    properties:
      /^x-/: string
/labels:
  get:
    responses:
      200:
        body:
          application/json: Labels
"""
    new = old.replace('/^x-/: string', '/^x-/: integer')

    found = graded(tmp_path, old, new)

    change = next(change for change in found if isinstance(change, SchemaChanged))
    assert change.path == (PatternPropertySegment('^x-'),)
    assert change.rule == 'type-changed'
    assert change.impact == 'breaking'
    assert '$[/^x-/]' in backward_markdown(
        parse_from_string(old, file_name='old.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True)),
        parse_from_string(new, file_name='new.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True)),
    )


def test_pattern_property_order_changes_are_reported_for_first_match_semantics(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Labels:
    type: object
    properties:
      /^x-/: string
      /-id$/: integer
/labels:
  get:
    responses:
      200:
        body:
          application/json: Labels
"""
    new = old.replace('      /^x-/: string\n      /-id$/: integer', '      /-id$/: integer\n      /^x-/: string')

    found = graded(tmp_path, old, new)

    change = next(change for change in found if isinstance(change, SchemaChanged))
    assert change.subject == 'pattern-property'
    assert change.attribute == 'order'
    assert change.impact == 'review'


def test_pattern_property_addition_and_removal_follow_constraint_direction(tmp_path):
    without = """#%RAML 1.0
title: T
types:
  Labels:
    type: object
    properties: {}
/labels:
  post:
    body:
      application/json: Labels
"""
    with_pattern = without.replace('properties: {}', 'properties:\n      /^x-/: string')

    added = next(change for change in graded(tmp_path, without, with_pattern) if isinstance(change, SchemaChanged))
    removed = next(change for change in graded(tmp_path, with_pattern, without) if isinstance(change, SchemaChanged))

    assert (added.rule, added.impact) == ('request-constraint-tightened', 'breaking')
    assert (removed.rule, removed.impact) == ('request-constraint-loosened', 'compatible')


def test_mixed_response_enum_delta_reports_added_and_removed_values(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  State:
    type: string
    enum: [a, b]
/state:
  get:
    responses:
      200:
        body:
          application/json: State
"""
    new = old.replace('enum: [a, b]', 'enum: [b, c]')

    found = [change for change in graded(tmp_path, old, new) if isinstance(change, SchemaChanged)]

    assert [(change.rule, change.before, change.after, change.impact) for change in found] == [
        ('response-enum-value-removed', ('a',), None, 'compatible'),
        ('response-enum-value-added', None, ('c',), 'review'),
    ]


def test_enum_comparison_preserves_scalar_types(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Value:
    type: any
    enum: [1]
/value:
  get:
    responses:
      200:
        body:
          application/json: Value
"""
    new = old.replace('enum: [1]', "enum: ['1']")

    found = [change for change in graded(tmp_path, old, new) if isinstance(change, SchemaChanged)]

    assert found[0].before == (1,)
    assert found[1].after == ('1',)


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


def test_reordering_same_kind_union_members_pairs_by_structure(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Result:
    type: union
    anyOf:
      - type: string
        maxLength: 5
      - type: string
        maxLength: 10
/result:
  get:
    responses:
      200:
        body:
          application/json: Result
"""
    new = old.replace(
        '      - type: string\n        maxLength: 5\n      - type: string\n        maxLength: 10',
        '      - type: string\n        maxLength: 10\n      - type: string\n        maxLength: 5',
    )

    assert graded(tmp_path, old, new) == []


def test_added_operations_and_statuses_preserve_declaration_order(tmp_path):
    old = '#%RAML 1.0\ntitle: T\n/existing:\n  get:\n    responses:\n      200:\n'
    new = old + '/z-last:\n  get:\n/a-first:\n  post:\n/statuses:\n  get:\n    responses:\n      202:\n      201:\n'

    found = graded(tmp_path, old, new)

    additions = [change for change in found if isinstance(change, OperationAdded)]
    assert [change.operation.path for change in additions] == ['/z-last', '/a-first', '/statuses']

    old_with_statuses = old + '/statuses:\n  get:\n    responses:\n      200:\n'
    new_with_statuses = old + '/statuses:\n  get:\n    responses:\n      200:\n      202:\n      201:\n'
    statuses = [
        change
        for change in graded(tmp_path, old_with_statuses, new_with_statuses)
        if isinstance(change, OperationChanged) and isinstance(change.location, ResponseStatus)
    ]
    assert [change.location.status for change in statuses] == ['202', '201']


def test_display_names_are_reported_at_each_model_layer(tmp_path):
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    displayName: Old shape
/things:
  get:
    displayName: Old operation
    responses:
      200:
        displayName: Old response
        body:
          application/json: Payload
"""
    new = (
        old.replace('Old shape', 'New shape')
        .replace('Old operation', 'New operation')
        .replace('Old response', 'New response')
    )

    found = [change for change in graded(tmp_path, old, new) if change.attribute == 'displayName']

    assert len(found) == 3
    assert all(change.rule == 'documentation-changed' and change.impact == 'cosmetic' for change in found)
    assert {type(change.location) for change in found} == {OperationContract, ResponseStatus, ResponseBody}


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
    assert '| Body `application/json` | `$.code` | `maxLength` | 10 -> 5 | Breaking |' in report


def test_base_uri_change_is_breaking(tmp_path):
    old = '#%RAML 1.0\ntitle: T\nbaseUri: https://old.example.test\n'
    new = old.replace('old.example.test', 'new.example.test')
    found = graded(tmp_path, old, new)
    assert found == [
        ApiChanged(
            location=TransportLocation(),
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
    assert '### Added\n\n- `GET /things` - **List things** - Lists every thing available to the caller.' in report
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


def test_a_boolean_facet_is_not_rendered_as_a_requiredness(tmp_path):
    """The subject decides what a value means, not its Python type.

    `additionalProperties: true -> false` and `uniqueItems: true -> false` both
    read "Required -> Optional" while the renderer sniffed `bool`, because the
    only boolean it had ever been handed was a requiredness flag.
    """
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    additionalProperties: true
    properties:
      tags:
        type: array
        uniqueItems: true
        items: string
/things:
  post:
    body:
      application/json: Payload
"""
    new = old.replace('additionalProperties: true', 'additionalProperties: false').replace(
        'uniqueItems: true', 'uniqueItems: false'
    )

    changes = graded(tmp_path, old, new)
    report = render_markdown(changes)

    assert {change.subject for change in changes} == {'constraint'}
    assert '| `additionalProperties` | true -> false |' in report
    assert '| `uniqueItems` | true -> false |' in report
    assert 'Required' not in report
    assert 'Optional' not in report


def test_requiredness_still_reads_as_requiredness(tmp_path):
    """The mirror of the rule above: `required` is the subject that does spell a
    boolean as Required and Optional, and it must keep doing so.
    """
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    properties:
      name?: string
/things:
  post:
    body:
      application/json: Payload
"""
    new = old.replace('name?: string', 'name: string')

    change = next(change for change in graded(tmp_path, old, new) if isinstance(change, SchemaChanged))

    assert change.subject == 'required'
    assert '| Requiredness | Optional -> Required |' in render_markdown([change])


def test_an_added_or_removed_subject_carries_one_value_in_one_column(tmp_path):
    """`Before` and `After` fitted a `changed` row and misfitted the other two.

    One side of an addition or a removal is empty by construction and the heading
    already says which, so a two-column pair spent itself carrying one value --
    on half the worked catalogue's rows. `Detail` holds it instead, and the
    kind-split heading supplies the verb.
    """
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    properties:
      state:
        type: string
        enum: [a, b]
      note?: string
/things:
  post:
    body:
      application/json: Payload
"""
    new = old.replace('enum: [a, b]', 'enum: [b, c]').replace('      note?: string\n', '')

    report = render_markdown(graded(tmp_path, old, new))

    assert '**Removed**' in report
    assert '**Added**' in report
    assert '| Body `application/json` | `$.state` | a | Breaking |' in report
    assert '| Body `application/json` | `$.state` | c | Compatible |' in report
    assert '| Body `application/json` | `$.note` | optional string | Review |' in report
    assert 'Enum value removed' not in report, 'the heading is the verb'
    assert 'Absent' not in report


def test_an_unset_facet_is_still_absent_on_a_changed_row(tmp_path):
    """Blanking is keyed to `kind`, not to `None`. A facet that went from declared
    to undeclared is a `changed` row, and "Absent" is the true reading there.
    """
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    properties:
      code:
        type: string
        maxLength: 10
/things:
  post:
    body:
      application/json: Payload
"""
    new = old.replace('\n        maxLength: 10', '')

    change = next(change for change in graded(tmp_path, old, new) if isinstance(change, SchemaChanged))

    assert (change.kind, change.subject) == ('changed', 'constraint')
    assert '| `maxLength` | 10 -> Absent |' in render_markdown([change])


def test_every_emitted_subject_is_one_a_project_may_match_on():
    """`SUBJECTS` is what `configure` validates an override against, so a subject
    the walk emits but the vocabulary omits would be unmatchable policy.
    """
    root = Path(__file__).parents[2] / 'examples' / 'compatibility'
    options = ParseOptions(unwrap=True)
    changes = backward(parse_from_path(root / 'v1.raml', options), parse_from_path(root / 'v2.raml', options))

    emitted = {change.subject for change in changes if not isinstance(change, (OperationAdded, OperationRemoved))}
    assert emitted <= SUBJECTS
    assert 'operation' in SUBJECTS, 'the subject _match_fields reports for an added or removed operation'


def test_a_misspelled_subject_in_an_override_is_refused():
    """A `subject:` nobody emits matches nothing, which reads as a policy that ran
    and decided against you. Refused for the same reason an unknown rule id is.
    """
    change = ApiChanged(TransportLocation(), 'changed', 'base-uri', 'baseUri', 'a', 'b', 'breaking', 'base-uri-changed')
    config = CompatibilityConfig(
        rules=(
            CompatibilityRuleSetting(
                id='base-uri-changed', impact='compatible', match=CompatibilityMatch(subject='uri')
            ),
        )
    )

    with pytest.raises(ValueError, match='unknown compatibility subject: uri'):
        configure([change], config)


API_SECURED = """#%RAML 1.0
title: T
securitySchemes:
  oauth:
    type: OAuth 2.0
    settings:
      accessTokenUri: https://example.test/token
      authorizationGrants: [client_credentials]
  key:
    type: Pass Through
    describedBy:
      headers:
        X-Key: string
securedBy: [oauth]
/a:
  get:
/b:
  get:
/own:
  get:
    securedBy: [key]
"""


def test_an_inherited_security_change_is_reported_once_at_the_api(tmp_path):
    """`securedBy:` is the same API-level default as `protocols:`.

    Nothing exercised it before, so the duplication was latent: an edit at the
    root would have filed an identical security row under every method that
    inherits it, exactly as `protocols:` did.
    """
    new = API_SECURED.replace('securedBy: [oauth]\n/a:', 'securedBy: [oauth, key]\n/a:')

    found = graded(tmp_path, API_SECURED, new)

    api = [change for change in found if isinstance(change, ApiChanged)]
    assert [(change.subject, change.kind, change.after) for change in api] == [
        ('security-alternative', 'added', {'name': 'key'})
    ]
    assert isinstance(api[0].location, SecurityLocation)
    assert not [change for change in found if isinstance(change, OperationChanged)], '/a and /b inherit'


def test_an_api_level_scheme_carries_its_described_by_to_the_api_too(tmp_path):
    """The whole comparison moves up, not only the alternative list.

    A scheme the root named is the root's, so the headers its `describedBy`
    requires are compared once as well -- and those are parameter shapes, which
    is what `ApiSchemaChanged` already exists to carry.
    """
    old = API_SECURED.replace('securedBy: [oauth]\n/a:', 'securedBy: [key]\n/a:')
    new = old.replace('X-Key: string', 'X-Key: integer')

    found = graded(tmp_path, old, new)

    change = next(change for change in found if isinstance(change, ApiSchemaChanged))
    assert change.location == ParameterLocation('header', 'X-Key')
    assert (change.rule, change.impact) == ('type-changed', 'breaking')
    assert record(change)['scope'] == 'api-schema'
    # `/own` names the same scheme itself, so it reports it on its own account.
    # `/a` and `/b` inherit, and read it from the API table once.
    owners = {change.operation.path for change in found if isinstance(change, (OperationChanged, SchemaChanged))}
    assert owners == {'/own'}


def test_an_operation_that_declares_its_own_security_is_still_compared(tmp_path):
    """`explicit_secured_by` is what separates the two, and `/own` has it."""
    new = API_SECURED.replace('    securedBy: [key]', '    securedBy: [oauth]')

    found = graded(tmp_path, API_SECURED, new)

    assert not [change for change in found if isinstance(change, ApiChanged)]
    operations = {change.operation.path for change in found if isinstance(change, OperationChanged)}
    assert operations == {'/own'}


def test_an_inherited_protocol_change_is_reported_once_at_the_api(tmp_path):
    """An API-level default that every method inherits is one change, at the root.

    Reported per method, this catalogue's single `protocols:` edit filled 33 of 34
    operation tables with an identical row and 33 of 54 breaking changes, so a
    reader counting the damage saw two and a half times what happened.
    """
    old = """#%RAML 1.0
title: T
protocols: [HTTP, HTTPS]
/a:
  get:
/b:
  get:
/own:
  get:
    protocols: [HTTP]
"""
    new = old.replace('protocols: [HTTP, HTTPS]\n/a:', 'protocols: [HTTPS]\n/a:')

    found = graded(tmp_path, old, new)

    api = [change for change in found if isinstance(change, ApiChanged)]
    assert [(change.subject, change.before, change.after) for change in api] == [
        ('protocol', ('HTTP', 'HTTPS'), ('HTTPS',))
    ]
    assert not [change for change in found if isinstance(change, OperationChanged)], (
        '/a and /b inherit, so they restate nothing'
    )


def test_an_operation_that_declares_its_own_protocols_is_still_compared(tmp_path):
    """The mirror. `protocols:` has a method-level override, unlike `baseUri`, so
    silence at the operation would lose a real per-method change.
    """
    old = """#%RAML 1.0
title: T
protocols: [HTTP, HTTPS]
/own:
  get:
    protocols: [HTTP, HTTPS]
"""
    new = old.replace('    protocols: [HTTP, HTTPS]', '    protocols: [HTTPS]')

    found = graded(tmp_path, old, new)

    assert not [change for change in found if isinstance(change, ApiChanged)]
    operation = next(change for change in found if isinstance(change, OperationChanged))
    assert (operation.rule, operation.impact) == ('protocol-removed', 'breaking')
    assert isinstance(operation.location, TransportLocation)


def test_a_method_that_starts_overriding_an_inherited_default_is_compared(tmp_path):
    """Declared on one side only: the method's effective value moved even though it
    names the root on the other, so it is its own change and not the API's.
    """
    old = '#%RAML 1.0\ntitle: T\nprotocols: [HTTP, HTTPS]\n/own:\n  get:\n'
    new = old.replace('/own:\n  get:\n', '/own:\n  get:\n    protocols: [HTTPS]\n')

    operation = next(change for change in graded(tmp_path, old, new) if isinstance(change, OperationChanged))

    assert (operation.before, operation.after) == (('HTTP', 'HTTPS'), ('HTTPS',))
    assert operation.impact == 'breaking'


def test_an_addition_says_what_the_new_thing_is_for(tmp_path):
    """The author's `description` is what a reader wants about a field they have
    never seen, and no coordinate can supply it.

    It reaches every entity RAML lets an author describe, not only properties: a
    query parameter, a response status and a security scheme each carry theirs.
    """
    old = """#%RAML 1.0
title: T
securitySchemes:
  key:
    type: Pass Through
/things:
  get:
    securedBy: [key]
    responses:
      200:
"""
    new = """#%RAML 1.0
title: T
securitySchemes:
  key:
    type: Pass Through
  token:
    type: Pass Through
    description: Bearer token from the device pairing flow.
/things:
  get:
    securedBy: [key, token]
    queryParameters:
      cursor?:
        type: string
        description: Opaque position from the previous page.
    responses:
      200:
      202:
        description: Queued; poll the Location header.
"""

    report = render_markdown(graded(tmp_path, old, new))

    assert '| query parameter `cursor` | optional string | Opaque position from the previous page. |' in report
    assert '| Status `202` | Queued; poll the Location header. |' in report
    assert '| Security | token | Bearer token from the device pairing flow. |' in report
    assert '| Where | Detail | Description | Compatibility |' in report


def test_a_table_of_undescribed_additions_has_no_description_column(tmp_path):
    """The same adaptive rule as `Path`: a column nobody fills is not a column."""
    old = '#%RAML 1.0\ntitle: T\n/things:\n  get:\n    responses:\n      200:\n'
    new = old + '      202:\n'

    report = render_markdown(graded(tmp_path, old, new))

    assert '| Where | Compatibility |' in report
    assert 'Description' not in report.split('## How to read this')[-1].split('##', 1)[-1]


def test_an_added_or_removed_entity_never_restates_its_own_coordinate():
    """A descriptor carries what `location` and `path` do not already say.

    A status, a media type and a union member's type are all in the coordinate
    that addresses the change, so a `type` key on those would put one fact in a
    row twice. A property's and a parameter's are not, and stay. Prose is nobody's
    coordinate, so `description` is free to appear on any of them.
    """
    root = Path(__file__).parents[2] / 'examples' / 'compatibility'
    options = ParseOptions(unwrap=True)
    changes = backward(parse_from_path(root / 'v1.raml', options), parse_from_path(root / 'v2.raml', options))

    entities = [
        change
        for change in changes
        if isinstance(change, (OperationChanged, SchemaChanged)) and change.kind in ('added', 'removed')
    ]
    assert {'response', 'body', 'union-member'} <= {change.subject for change in entities}, 'all three are exercised'

    for change in entities:
        descriptor = change.before if change.before is not None else change.after
        if not isinstance(descriptor, dict):
            continue
        identifying = descriptor.keys() - {'description'}
        if change.subject not in ('property', 'parameter', 'security-alternative'):
            assert not identifying, f'{change.subject} carries {identifying}, which its coordinate already states'
        addressed = _location_label(change.location)
        addressed += _path_label(change.path) if isinstance(change, SchemaChanged) else ''
        for key in identifying:
            assert str(descriptor[key]) not in addressed, f'{change.subject}.{key} restates its coordinate'


SHARED_TYPE = """#%RAML 1.0
title: T
types:
  Money:
    type: object
    properties:
      currency:
        type: string
        maxLength: 3
/orders:
  get:
    responses:
      200:
        body:
          application/json: Money
/invoices:
  get:
    responses:
      200:
        body:
          application/json: Money
/refunds:
  get:
    queryParameters:
      cursor?: string
    responses:
      200:
        body:
          application/json: Money
"""


def test_one_edit_reaching_several_operations_is_one_row(tmp_path):
    """The headline counts decisions, not blast radius.

    A type three operations carry, edited once, filled three tables with the same
    row and reported "3 breaking changes" for one `maxLength`. The change list
    still holds them apart -- each operation is a separate contract, and a project
    override matches each on its own operation.
    """
    new = SHARED_TYPE.replace('maxLength: 3', 'maxLength: 8')

    changes = graded(tmp_path, SHARED_TYPE, new)
    report = render_markdown(changes)

    assert len(changes) == 3, 'the record keeps one change per contract'
    assert [change.operation.path for change in changes] == ['/orders', '/invoices', '/refunds']
    assert report.count('`maxLength` | 3 -> 8') == 1, 'the report states the edit once'
    assert '> **Breaking.** 1 breaking change require' in report
    assert '| `GET /orders`, `GET /invoices`, `GET /refunds` |' in report
    assert '## `GET /orders`' not in report, 'nothing is left over to head a section with'


def test_a_change_reaching_one_operation_stays_under_it(tmp_path):
    """Only rows stating the same fact collapse; the rest keep their owner."""
    new = SHARED_TYPE.replace('maxLength: 3', 'maxLength: 8').replace('cursor?: string', 'cursor: string')

    report = render_markdown(graded(tmp_path, SHARED_TYPE, new))

    assert '## Several operations' in report
    assert '## `GET /refunds`' in report
    assert '| query parameter `cursor` | Requiredness | Optional -> Required | Breaking |' in report
    assert '> **Breaking.** 2 breaking changes require' in report


def test_the_same_facet_on_two_unrelated_shapes_is_not_rolled_up(tmp_path):
    """Equal values are not one edit. The key carries the coordinate too, so two
    different properties moving to the same bound stay two rows.
    """
    old = """#%RAML 1.0
title: T
/a:
  post:
    body:
      application/json:
        type: object
        properties:
          alpha:
            type: string
            maxLength: 3
/b:
  post:
    body:
      application/json:
        type: object
        properties:
          beta:
            type: string
            maxLength: 3
"""
    new = old.replace('maxLength: 3', 'maxLength: 8')

    report = render_markdown(graded(tmp_path, old, new))

    assert '## Several operations' not in report
    assert '## `POST /a`' in report
    assert '## `POST /b`' in report


def test_a_table_states_nothing_its_heading_or_its_where_column_already_said(tmp_path):
    """Three ways the table repeated itself, all from the section heading.

    Under **Response**, every cell opened with the word "Response". A `$` in
    `Path` meant "the coordinate in Where", which is what an empty cell on the
    row above already meant. And a `Path` column on a table of contract changes
    announced a column of nothing -- sixteen of the worked catalogue's
    thirty-seven tables.
    """
    old = """#%RAML 1.0
title: T
/things:
  post:
    body:
      application/json:
        type: object
        properties:
          title: string
    responses:
      200:
        headers:
          X-Trace?: string
        body:
          application/json: string
"""
    new = old.replace('X-Trace?: string', 'X-Trace: string').replace('title: string', 'title: integer')

    report = render_markdown(graded(tmp_path, old, new))

    assert '### Response\n\n**Changed**\n\n| Where | Change |' in report, 'no Path column where no row has one'
    assert '| `200` header `X-Trace` |' in report, 'the heading already said Response'
    assert 'Response `200` header' not in report
    assert '| Body `application/json` | `$.title` |' in report, 'a path that reaches inside is kept'
    assert '| `$` |' not in report, 'the shape root is the coordinate itself, and renders blank'


def test_markdown_orders_rows_by_impact_without_losing_declaration_order(tmp_path):
    """A break must not sit under a documentation edit, and same-impact rows must
    still read in the order the document declared them.

    The list itself stays in walk order: `impact` is the one field `configure`
    rewrites, so a list ordered by it would be stale the moment a project regraded
    anything.
    """
    old = """#%RAML 1.0
title: T
types:
  Payload:
    type: object
    properties:
      alpha:
        type: string
        description: Old alpha
      beta:
        type: string
        maxLength: 10
      gamma:
        type: string
        maxLength: 10
/things:
  post:
    body:
      application/json: Payload
"""
    new = (
        old.replace('Old alpha', 'New alpha')
        .replace('      beta:\n        type: string\n        maxLength: 10', '      beta:\n        type: integer')
        .replace('      gamma:\n        type: string\n        maxLength: 10', '      gamma:\n        type: integer')
    )

    changes = graded(tmp_path, old, new)
    rows = [line for line in render_markdown(changes).splitlines() if line.startswith('| Body `')]

    assert changes[0].impact == 'cosmetic', 'the walk still reports in declaration order'
    assert [row.rsplit('|', 2)[1].strip() for row in rows] == ['Breaking', 'Breaking', 'Cosmetic']
    assert [row for row in rows if 'Breaking' in row] == [row for row in rows if '$.beta' in row or '$.gamma' in row], (
        'ties keep declaration order'
    )


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
    assert '| old\\|pattern -> new pattern | Review |' in report


def test_markdown_summarizes_description_changes_but_json_does_not():
    before = '\n\nOld summary\nOld detail'
    after = 'New summary\nNew detail'
    change = OperationChanged(
        operation=OperationId('/things', 'get'),
        location=OperationContract(),
        kind='changed',
        subject='documentation',
        attribute='description',
        before=before,
        after=after,
        impact='cosmetic',
        rule='documentation-changed',
    )

    report = render_markdown([change])

    assert '| `description` | Old summary... -> New summary... | Cosmetic |' in report
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


def _worked_example() -> tuple[list[Change], str]:
    root = Path(__file__).parents[2] / 'examples' / 'compatibility'
    options = ParseOptions(unwrap=True)
    old = parse_from_path(root / 'v1.raml', options)
    new = parse_from_path(root / 'v2.raml', options)
    return backward(old, new), backward_markdown(old, new)


def test_the_worked_example_is_a_readable_end_to_end_report():
    changes, report = _worked_example()

    # `reference-dropped` describes a raw graph edge and has no effective-model
    # counterpart. Every compatibility rule is exercised by this one report.
    emitted = {change.rule for change in changes}
    assert emitted == set(RULES) - {'reference-dropped'}
    # And `RULE_IDS` -- what `configure` validates an override against -- is exactly
    # that set. An id listed there but never emitted would be an override the CLI
    # accepts and no change ever matches, which is the failure an override must not
    # have. The set is hand-kept because the walk builds ids by interpolation.
    assert emitted == RULE_IDS
    assert '# API compatibility' in report
    assert '## Every operation' in report
    assert '| baseUri parameter `tenant` | `maxLength` | 20 -> 10 | Breaking |' in report
    assert '## `POST /request-required`' in report
    assert '## `GET /response-enum-add`' in report
    assert '| Body `application/json` | `$.profile.nickname` | `maxLength` |' in report
    assert '| `200` body `application/json` | `$.records[].state` | archived | Review |' in report
    assert '| Security | `accessTokenUri` |' in report
    assert (
        '| `200` body `application/json` | `$.productCode` | `pattern` | '
        '^\\[A-Z\\]+$ -> ^\\[a-z\\]+$ | Review |' in report
    )
    assert 'accessTokenUri' in report
    # Sides of the wire, not result classes: a caller reads what it sends apart
    # from what it receives, and a header under a response is the response's.
    assert '### Request' in report
    assert '### Response' in report
    assert '### Documentation' in report
    assert '### Method contract' not in report
    # Then by kind, because Before and After fitted only one of the three.
    assert '**Removed**' in report
    assert '**Changed**' in report
    assert '**Added**' in report
    # A status, a media type and a member type are stated once, by the coordinate
    # that addresses them. What is left is the author's prose, which is nobody's
    # coordinate and the one thing an addition can say that its position cannot.
    assert '| Status `410` | The record is permanently gone. | Breaking |' in report
    assert '| Status `202` | The request was accepted for processing. | Compatible |' in report
    assert '| query parameter `cursor` | optional string | Opaque position' in report
    # One parameter, two facts, adjacent: the contract row then the shape row.
    assert (
        '| query parameter `limit` | Requiredness | Optional -> Required | Breaking |\n'
        '| query parameter `limit` | `type` | string -> integer | Breaking |' in report
    )
    # A body names no type: `Where` already did, and it has no prose here either.
    assert '| Body `application/vnd.legacy+json` | Breaking |' in report
    assert '| Body `application/vnd.example+json` | Compatible |' in report
    assert '| `200` body `application/problem+json` | Breaking |' in report
    assert '| `200` body `application/vnd.example+json` | Compatible |' in report
    assert '| `200` body `application/json` | `$.result<integer>` | Compatible |' in report
    assert '| `200` body `application/json` | `$.result<boolean>` | Review |' in report
    # Rule prose belongs to the policy documentation, not to a change row.
    assert 'Callers read a field that has gone' not in report


def test_the_worked_example_reaches_every_model_coordinate():
    """Rule coverage alone would conceal an unexercised location or segment kind,
    because several model branches share one rule.
    """
    changes, _ = _worked_example()

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

    protocol = next(change for change in changes if change.rule == 'protocol-added')
    nested_shape = next(
        change
        for change in changes
        if isinstance(change, SchemaChanged)
        and change.operation.path == '/response-enum-add'
        and change.subject == 'enum-value'
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
