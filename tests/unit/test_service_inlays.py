"""Inline hints show hidden type facts, not text already authored at the site."""

import pytest

from fastraml.positions import Position
from fastraml.service import inlays
from tests.unit.test_service_data_hover import DOCUMENT
from tests.unit.test_service_queries import _buffered, _where


def test_inferred_declarations_and_typed_data_share_hint_types_and_navigation(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    hints = inlays.inlay_hints(snapshot, uri, Position(1, 1, 1000, 1))
    by_position = {(hint.position.line, hint.position.column): hint for hint in hints}
    line, column = _where(DOCUMENT, 'Person:')
    assert by_position[line, column + len('Person')].label == '[object]'
    line, column = _where(DOCUMENT, 'name: Grace')
    hint = by_position[line, column + len('name')]
    assert hint.label == '[string]'
    assert "The person's **display name**." in hint.tooltip
    part = hint.parts[1]
    assert part.definition_uri == uri
    assert part.definition_span is not None
    assert part.definition_span.line == _where(DOCUMENT, 'name:\n')[0]
    # The schema property has an authored `type: string`, so it needs no inferred label.
    assert (_where(DOCUMENT, 'name:\n')[0], _where(DOCUMENT, 'name:\n')[1] + len('name')) not in by_position


def test_inherited_constraints_are_visible_without_repeating_authored_constraints(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  Word:\n    type: string\n    minLength: 2\n'
        '  Name:\n    type: Word\n    maxLength: 40\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    hints = inlays.inlay_hints(workspace.snapshot(uri), uri, Position(1, 1, 100, 1))
    line, _ = _where(document, 'type: Word')
    (hint,) = [hint for hint in hints if hint.position.line == line]
    assert hint.label == '[length: ≥2]'
    assert 'minLength: 2' in hint.tooltip
    assert 'maxLength' not in hint.tooltip
    assert not [hint for hint in hints if hint.position.line == _where(document, 'Word:')[0]]


def test_hint_range_is_respected_and_unknown_data_fields_have_no_type_hint(memory_workspace):
    document = DOCUMENT.replace('name: Grace', 'name: Grace\n    unknown: value')
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    line, column = _where(document, 'name: Grace')
    hints = inlays.inlay_hints(snapshot, uri, Position(line, column, line, column + len('name')))
    assert [hint.label for hint in hints] == ['[string]']
    line, column = _where(document, 'unknown: value')
    assert not inlays.inlay_hints(snapshot, uri, Position(line, column, line, column + len('unknown')))


def test_ambiguous_union_hints_preserve_all_known_expected_types(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  A:\n    properties:\n      value: string\n'
        '  B:\n    properties:\n      value: integer\nannotationTypes:\n  either: A | B\n(either): {value: 1}\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    line, column = _where(document, 'value: 1')
    hints = inlays.inlay_hints(workspace.snapshot(uri), uri, Position(line, column, line, column + 5))
    (hint,) = hints
    assert hint.label == '[string | integer]'
    assert [part.definition_span.line for part in hint.parts if part.definition_span is not None] == [
        _where(document, 'value: string')[0],
        _where(document, 'value: integer')[0],
    ]


def test_complex_inherited_constraints_stay_in_the_tooltip_not_the_source_line(memory_workspace):
    pattern = '^' + 'a' * 200 + '```$'
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  Isbn:\n    type: string\n    minLength: 13\n'
        f'    maxLength: 13\n    pattern: {pattern}\n'
        '  Book:\n    properties:\n      isbn: Isbn\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    line, column = _where(document, 'isbn: Isbn')
    (hint,) = inlays.inlay_hints(
        snapshot, uri, Position(line, column + len('isbn: '), line, column + len('isbn: Isbn'))
    )
    assert hint.position.column == column + len('isbn: Isbn')
    assert hint.label == '[length: 13]'
    assert 'minLength: 13' in hint.tooltip
    assert 'maxLength: 13' in hint.tooltip
    assert pattern in hint.tooltip
    assert '````yaml\n' in hint.tooltip  # the author's backticks cannot close the block


def test_range_starting_inside_a_key_includes_the_hint_at_its_end(memory_workspace):
    workspace, folder = _buffered(memory_workspace, {'api.raml': DOCUMENT})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    for needle, label in (('Person:', '[object]'), ('name: Grace', '[string]')):
        line, column = _where(DOCUMENT, needle)
        end = column + len(needle.split(':')[0])
        (hint,) = inlays.inlay_hints(snapshot, uri, Position(line, column + 1, line, end))
        assert hint.label == label
        assert hint.position.column == end
        assert not inlays.inlay_hints(snapshot, uri, Position(line, column, line, end - 1))


def test_same_type_union_candidates_preserve_docs_without_an_arbitrary_navigation_target(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  A:\n    properties:\n      value:\n'
        '        type: string\n        description: Meaning in A.\n'
        '  B:\n    properties:\n      value:\n'
        '        type: string\n        description: Meaning in B.\n'
        'annotationTypes:\n  either: A | B\n(either): {value: text}\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    line, column = _where(document, 'value: text')
    (hint,) = inlays.inlay_hints(workspace.snapshot(uri), uri, Position(line, column, line, column + 5))
    assert hint.label == '[string]'
    assert 'Meaning in A.' in hint.tooltip
    assert 'Meaning in B.' in hint.tooltip
    assert 'no single alternative is assumed' in hint.tooltip
    assert all(part.definition_uri is None and part.definition_span is None for part in hint.parts)


def test_supplied_custom_facets_get_expected_types_at_their_keys(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  Entity:\n    type: object\n    facets:\n'
        '      stewardedBy: string\n      reviewedEvery: integer\n      sources: string[]\n'
        '  Book:\n    type: Entity\n    stewardedBy: catalogue-team\n    reviewedEvery: 90\n'
        '    sources: [publisher-feed, isbndb]\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert snapshot.error is None
    for needle, expected in (
        ('stewardedBy: catalogue', '[string]'),
        ('reviewedEvery: 90', '[integer]'),
        ('sources: [', '[string[]]'),
    ):
        line, column = _where(document, needle)
        (hint,) = inlays.inlay_hints(snapshot, uri, Position(line, column, line, column + len(needle.split(':')[0])))
        assert hint.label == expected
        assert hint.parts[1].definition_span is not None
        assert hint.parts[1].definition_span.line == _where(document, needle.split(':')[0] + ':')[0]


def test_only_compact_concrete_constraints_are_shown_and_explicit_types_are_not_repeated(memory_workspace):
    document = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  Code:\n    type: string\n'
        '    pattern: ^' + 'a' * 200 + '$\n  Message:\n    type: Code\n'
        '  A: object\n  B: object\n  Both:\n    type: [A, B]\n'
    )
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    assert not inlays.inlay_hints(workspace.snapshot(uri), uri, Position(1, 1, 100, 1))


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        ('type: string\n    minLength: 2\n    maxLength: 40', '[length: 2..40]'),
        ('type: array\n    items: string\n    minItems: 1\n    maxItems: 10', '[items: 1..10]'),
        ('type: object\n    minProperties: 1\n    maxProperties: 3', '[properties: 1..3]'),
        ('type: number\n    minimum: 0.1\n    maximum: 0.9', '[range: 0.1..0.9]'),
        ('type: number\n    maximum: 10', '[range: ≤10]'),
        ('type: number\n    multipleOf: 1.1', '[multipleOf: 1.1]'),
    ],
)
def test_hidden_bounds_show_concrete_exact_facts_at_the_reference(memory_workspace, body, expected):
    document = f'#%RAML 1.0\ntitle: T\ntypes:\n  Base:\n    {body}\n  Use: Base\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    snapshot = workspace.snapshot(uri)
    assert snapshot.error is None
    line, column = _where(document, 'Use: Base')
    end = column + len('Use: Base')
    (hint,) = inlays.inlay_hints(snapshot, uri, Position(line, end - 1, line, end))
    assert hint.label == expected
    assert hint.position.column == end
    assert not hint.is_type


def test_long_union_labels_remain_short_without_losing_alternative_documentation(memory_workspace):
    names = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta']
    declarations = ''.join(
        f'  {name}:\n    properties:\n      value:\n        type: string\n        description: Meaning in {name}.\n'
        f'  Wrapper{name}:\n    properties:\n      payload: {name}\n'
        for name in names
    )
    expression = ' | '.join(f'Wrapper{name}' for name in names)
    document = f'#%RAML 1.0\ntitle: T\ntypes:\n{declarations}annotationTypes:\n  either: {expression}\n(either): {{payload: {{value: text}}}}\n'
    workspace, folder = _buffered(memory_workspace, {'api.raml': document})
    uri = f'{folder}/api.raml'
    line, column = _where(document, 'payload: {value: text}')
    (hint,) = inlays.inlay_hints(workspace.snapshot(uri), uri, Position(line, column, line, column + len('payload')))
    assert len(hint.label) <= 38
    assert '…' in hint.label
    assert 'Alpha' in hint.label
    assert all(f'Uses `{name}`' in hint.tooltip for name in names)
