"""The extension merge and the overlay check (docs/19 § 3 and § 4).

Each test names the rule it protects. The merge is exercised on node trees
alone: `mark` records what it was handed, so authorship can be asserted without
a registry, and violations are asserted by message key and `info`.
"""

from __future__ import annotations

from fastraml.parser.extension_merge import merge_extension
from fastraml.yamlnode import NodeKind, compose, pairs
from tests.trees import snapshot

MASTER = 'file:///api/master.raml'
OVERLAY = 'file:///api/overlay.raml'


def tree(text: str, uri: str = MASTER):
    return compose(text, uri=uri)


def apply(master: str, extension: str, *, overlay: bool = False):
    """Merge; returns (result, the master tree, the set of marked nodes)."""
    marked: set[int] = set()

    def mark(node) -> None:
        stack = [node]
        while stack:
            current = stack.pop()
            marked.add(id(current))
            stack += current.content

    target = tree(master)
    result = merge_extension(target, tree(extension, OVERLAY), location=OVERLAY, overlay=overlay, mark=mark)
    return result, target, marked


def plain(node):
    """A node as plain Python, for comparing merged structure."""
    if node.kind is NodeKind.MAPPING:
        return {key.value: plain(value) for key, value in pairs(node)}
    if node.kind is NodeKind.SEQUENCE:
        return [plain(item) for item in node.content]
    return node.value


def merged(master: str, extension: str):
    result, _, _ = apply(master, extension)
    assert result.error is None
    return plain(result.tree)


def violations(master: str, overlay: str) -> list[dict[str, object]]:
    result, _, _ = apply(master, overlay, overlay=True)
    if result.error is None:
        return []
    heads = [chain[0] for chain in result.error.chains()]
    assert all(head.message == 'not allowed in an overlay' for head in heads)
    assert all(head.location == OVERLAY for head in heads)
    return [dict(head.info) for head in heads]


class TestPropertyKinds:
    def test_a_single_value_is_replaced(self):
        assert merged('title: A\n', 'title: B\n') == {'title': 'B'}

    def test_a_missing_key_is_added_after_the_targets_keys(self):
        # Invariant I8: target order, then new keys in extension order.
        assert list(merged('b: 1\na: 2\n', 'd: 3\nc: 4\n')) == ['b', 'a', 'd', 'c']

    def test_an_object_is_merged_recursively(self):
        master = '/books:\n  get:\n    description: a\n'
        assert merged(master, '/books:\n  get:\n    displayName: B\n') == {
            '/books': {'get': {'description': 'a', 'displayName': 'B'}}
        }

    def test_a_multi_value_property_adds_only_new_values(self):
        # Spec, Merging Rules: added "if no such value already exists".
        assert merged('protocols: [HTTP]\n', 'protocols: [HTTP, HTTPS]\n') == {'protocols': ['HTTP', 'HTTPS']}

    def test_enum_is_multi_value(self):
        # The spec's own example of a multi-value simple property.
        master = 'types:\n  Color:\n    enum: [red, green]\n'
        extension = 'types:\n  Color:\n    enum: [green, blue]\n'
        assert merged(master, extension)['types']['Color']['enum'] == ['red', 'green', 'blue']

    def test_an_array_of_objects_adds_only_new_objects(self):
        # docs/19 § 7: a restated object is not added twice.
        assert merged('x:\n  - a: 1\n', 'x:\n  - a: 1\n  - b: 2\n') == {'x': [{'a': '1'}, {'b': '2'}]}

    def test_different_kinds_are_replaced(self):
        assert merged('x: 1\n', 'x: [1, 2]\n') == {'x': ['1', '2']}

    def test_documentation_is_replaced_not_appended(self):
        # docs/19 § 7: the Overlays table and the localization example.
        master = 'documentation:\n  - title: Intro\n    content: a\n'
        extension = 'documentation:\n  - title: Intro-es\n    content: b\n'
        assert merged(master, extension) == {'documentation': [{'title': 'Intro-es', 'content': 'b'}]}


class TestAlwaysSimple:
    def test_an_example_is_data_and_replaced_whole(self):
        master = 'types:\n  T:\n    example: {a: 1, b: 2}\n'
        extension = 'types:\n  T:\n    example: {c: 3}\n'
        assert merged(master, extension)['types']['T']['example'] == {'c': '3'}

    def test_named_examples_merge_by_name(self):
        master = 'types:\n  T:\n    examples:\n      a: {x: 1}\n      b: {x: 2}\n'
        extension = 'types:\n  T:\n    examples:\n      b: {y: 3}\n      c: {x: 4}\n'
        assert merged(master, extension)['types']['T']['examples'] == {
            'a': {'x': '1'},
            'b': {'y': '3'},
            'c': {'x': '4'},
        }

    def test_an_annotation_value_is_replaced_whole(self):
        assert merged('(meta): {a: 1}\n', '(meta): {b: 2}\n') == {'(meta)': {'b': '2'}}

    def test_trait_names_are_multi_value(self):
        master = '/a:\n  get:\n    is: [paged]\n'
        assert merged(master, '/a:\n  get:\n    is: [secured]\n')['/a']['get']['is'] == ['paged', 'secured']

    def test_a_parameterised_application_is_replaced(self):
        master = '/a:\n  get:\n    is: [{paged: {size: 1}}]\n'
        extension = '/a:\n  get:\n    is: [{paged: {size: 2}}]\n'
        assert merged(master, extension)['/a']['get']['is'] == [{'paged': {'size': '2'}}]

    def test_a_resource_type_application_is_replaced(self):
        master = '/a:\n  type: {collection: {item: A}}\n'
        extension = '/a:\n  type: {collection: {other: B}}\n'
        assert merged(master, extension)['/a']['type'] == {'collection': {'other': 'B'}}


class TestNormalization:
    def test_an_empty_method_merges_like_an_empty_mapping(self):
        assert merged('/a:\n  get:\n', '/a:\n  get:\n    description: d\n') == {'/a': {'get': {'description': 'd'}}}

    def test_an_empty_extension_value_changes_nothing(self):
        assert merged('/a:\n  get:\n    description: d\n', '/a:\n  get:\n') == {'/a': {'get': {'description': 'd'}}}

    def test_a_shorthand_type_takes_a_facet_without_losing_its_type(self):
        master = 'types:\n  T:\n    properties:\n      name: string\n'
        extension = 'types:\n  T:\n    properties:\n      name:\n        description: d\n'
        assert merged(master, extension)['types']['T']['properties']['name'] == {'type': 'string', 'description': 'd'}

    def test_a_shorthand_extension_sets_the_type_and_keeps_the_rest(self):
        master = 'types:\n  Color:\n    type: string\n    enum: [red]\n'
        assert merged(master, 'types:\n  Color: integer\n')['types']['Color'] == {'type': 'integer', 'enum': ['red']}

    def test_a_lone_protocol_merges_like_a_sequence(self):
        assert merged('protocols: [HTTP]\n', 'protocols: HTTPS\n') == {'protocols': ['HTTP', 'HTTPS']}

    def test_type_is_not_a_sequence_position(self):
        # A scalar `type` is an expression; a sequence is multiple inheritance.
        master = 'types:\n  T:\n    type: [A, B]\n'
        assert merged(master, 'types:\n  T:\n    type: C\n')['types']['T']['type'] == 'C'


class TestConflictingProperties:
    def test_query_string_removes_query_parameters(self):
        master = '/a:\n  get:\n    queryParameters:\n      q: string\n'
        extension = '/a:\n  get:\n    queryString:\n      type: object\n'
        assert merged(master, extension)['/a']['get'] == {'queryString': {'type': 'object'}}

    def test_type_and_schema_are_one_property(self):
        # Synonyms, not a conflict: one pair, replaced key and all, so the
        # extension's spelling is the one that survives.
        master = 'types:\n  T:\n    schema: string\n'
        assert merged(master, 'types:\n  T:\n    type: integer\n')['types']['T'] == {'type': 'integer'}

    def test_schemas_adds_to_types_instead_of_displacing_them(self):
        master = 'types:\n  A: string\n'
        assert merged(master, 'schemas:\n  B: string\n') == {'types': {'A': 'string', 'B': 'string'}}

    def test_examples_removes_example(self):
        master = 'types:\n  T:\n    example: 1\n'
        extension = 'types:\n  T:\n    examples:\n      a: 2\n'
        assert merged(master, extension)['types']['T'] == {'examples': {'a': '2'}}

    def test_a_non_media_body_key_uses_the_type_conflicts(self):
        # A body without media-type keys is itself a type declaration.
        master = '/a:\n  post:\n    body:\n      example: 1\n'
        extension = '/a:\n  post:\n    body:\n      examples:\n        a: 2\n'
        assert merged(master, extension)['/a']['post']['body'] == {'examples': {'a': '2'}}

    def test_only_declared_pairs_conflict(self):
        master = '/a:\n  get:\n    headers:\n      h: string\n'
        extension = '/a:\n  get:\n    queryString:\n      type: object\n'
        assert set(merged(master, extension)['/a']['get']) == {'headers', 'queryString'}


class TestIgnoredAndDeclared:
    def test_uses_usage_and_extends_are_ignored_at_the_root(self):
        extension = 'uses:\n  lib: lib.raml\nusage: u\nextends: master.raml\ntitle: B\n'
        assert merged('title: A\n', extension) == {'title': 'B'}

    def test_usage_below_the_root_is_an_ordinary_facet(self):
        master = 'traits:\n  paged:\n    usage: a\n'
        assert merged(master, 'traits:\n  paged:\n    usage: b\n')['traits']['paged']['usage'] == 'b'

    def test_added_declarations_are_recorded_by_kind(self):
        master = 'types:\n  A: string\ntraits:\n  t: {}\n'
        extension = 'types:\n  A: string\n  B: string\nschemas:\n  C: string\ntraits:\n  u: {}\n'
        result, _, _ = apply(master, extension)
        assert result.declared == {'types': ['B', 'C'], 'traits': ['u']}

    def test_a_schemas_entry_counts_as_a_type(self):
        result, _, _ = apply('title: A\n', 'schemas:\n  C: string\n')
        assert result.declared == {'types': ['C']}


class TestIdentityAndMarks:
    def test_neither_input_is_mutated(self):
        # Invariant I9.
        master = tree('/a:\n  get:\n    queryParameters:\n      q: string\n')
        extension = tree('/a:\n  get:\n    queryString: {type: object}\n  post:\n', OVERLAY)
        before = (snapshot(master), snapshot(extension))
        merge_extension(master, extension, location=OVERLAY, overlay=False, mark=lambda node: None)
        assert (snapshot(master), snapshot(extension)) == before

    def test_untouched_children_keep_their_identity(self):
        # Invariant I10: the provenance maps are keyed by node identity.
        result, target, _ = apply('/a:\n  get: {}\n/b:\n  get: {}\n', '/a:\n  post:\n')
        assert result.tree.content[3] is target.content[3]

    def test_a_merge_that_changes_nothing_returns_the_target(self):
        result, target, marked = apply('/a:\n  get:\n    description: d\n', '/a:\n  get:\n    description: d\n')
        assert result.tree is target
        assert not marked

    def test_an_added_pair_is_marked_key_and_value(self):
        result, _, marked = apply('title: A\n', 'version: v1\n')
        key, value = result.tree.content[2], result.tree.content[3]
        assert id(key) in marked
        assert id(value) in marked

    def test_a_replaced_pair_takes_the_extensions_key(self):
        # A diagnostic on the key then names the same file as one on the value.
        result, target, marked = apply('title: A\n', 'title: B\n')
        key = result.tree.content[0]
        assert key is not target.content[0]
        assert id(key) in marked

    def test_a_recursed_container_is_not_marked(self):
        result, _, marked = apply('/a:\n  get: {}\n', '/a:\n  post:\n')
        assert id(result.tree.content[1]) not in marked


class TestOverlayRestrictions:
    def test_facets_in_the_table_may_change(self):
        master = 'title: A\n/a:\n  description: a\n  get:\n    displayName: g\n'
        overlay = 'title: B\nusage: u\n/a:\n  description: b\n  get:\n    displayName: h\n'
        assert violations(master, overlay) == []

    def test_a_restated_value_is_not_a_difference(self):
        # docs/19 § 4.1: the spec compares trees, so an unchanged value passes.
        master = '/a:\n  get:\n    body:\n      application/json:\n        properties:\n          n:\n            default: X\n'
        assert violations(master, master) == []

    def test_a_changed_version_is_rejected(self):
        assert violations('version: 1\n', 'version: 2\n') == [{'field': 'version', 'change': 'changed'}]

    def test_a_new_resource_is_rejected(self):
        assert violations('/a:\n', '/b:\n') == [{'field': '/b', 'change': 'added'}]

    def test_a_new_method_is_rejected(self):
        assert violations('/a:\n  get:\n', '/a:\n  post:\n') == [{'field': 'post', 'change': 'added'}]

    def test_a_new_response_and_media_type_are_rejected(self):
        master = '/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n'
        overlay = '/a:\n  get:\n    responses:\n      200:\n        body:\n          text/plain:\n      201:\n'
        assert violations(master, overlay) == [
            {'field': 'text/plain', 'change': 'added'},
            {'field': '201', 'change': 'added'},
        ]

    def test_a_property_named_description_is_not_the_description_facet(self):
        master = 'types:\n  User:\n    properties:\n      name: string\n'
        overlay = 'types:\n  User:\n    properties:\n      description: string\n'
        assert violations(master, overlay) == [{'field': 'description', 'change': 'added'}]

    def test_a_description_on_a_shorthand_property_is_allowed(self):
        master = 'types:\n  User:\n    properties:\n      name: string\n'
        overlay = 'types:\n  User:\n    properties:\n      name:\n        description: localized\n'
        assert violations(master, overlay) == []

    def test_a_new_type_is_allowed(self):
        assert violations('types:\n  A: string\n', 'types:\n  B:\n    properties:\n      x: string\n') == []

    def test_an_existing_type_is_checked_like_any_declaration(self):
        master = 'types:\n  A:\n    type: string\n'
        assert violations(master, 'types:\n  A:\n    minLength: 3\n') == [{'field': 'minLength', 'change': 'added'}]

    def test_new_traits_resource_types_and_schemes_are_rejected(self):
        overlay = 'traits:\n  t: {}\nresourceTypes:\n  r: {}\nsecuritySchemes:\n  s: {type: Basic Authentication}\n'
        assert violations('title: A\n', overlay) == [
            {'field': 'traits', 'change': 'added'},
            {'field': 'resourceTypes', 'change': 'added'},
            {'field': 'securitySchemes', 'change': 'added'},
        ]

    def test_a_trait_body_change_is_rejected(self):
        master = 'traits:\n  paged:\n    queryParameters:\n      page: integer\n'
        overlay = 'traits:\n  paged:\n    queryParameters:\n      size: integer\n'
        assert violations(master, overlay) == [{'field': 'size', 'change': 'added'}]

    def test_a_trait_display_name_may_change(self):
        master = 'traits:\n  paged:\n    displayName: a\n'
        assert violations(master, 'traits:\n  paged:\n    displayName: b\n') == []

    def test_annotation_types_may_change(self):
        # docs/19 § 4.4: checked by annotation validation, not here.
        master = 'annotationTypes:\n  a: string\n'
        assert violations(master, 'annotationTypes:\n  a: integer\n  b: string\n') == []

    def test_annotations_may_be_added_anywhere(self):
        master = '/a:\n  get:\n    responses:\n      200:\n'
        overlay = '(root): 1\n/a:\n  (r): 1\n  get:\n    (m): 1\n    responses:\n      200:\n        (s): 1\n'
        assert violations(master, overlay) == []

    def test_named_examples_may_be_added_and_changed(self):
        master = 'types:\n  T:\n    examples:\n      a: 1\n'
        assert violations(master, 'types:\n  T:\n    examples:\n      a: 2\n      b: 3\n') == []

    def test_an_added_protocol_is_rejected(self):
        assert violations('protocols: [HTTP]\n', 'protocols: [HTTPS]\n') == [{'field': 'protocols', 'change': 'added'}]

    def test_removing_a_conflicting_property_is_a_change(self):
        master = '/a:\n  get:\n    queryParameters:\n      q: string\n'
        overlay = '/a:\n  get:\n    queryString:\n      type: object\n'
        assert violations(master, overlay) == [
            {'field': 'queryParameters', 'change': 'removed'},
            {'field': 'queryString', 'change': 'added'},
        ]

    def test_switching_example_to_examples_is_allowed(self):
        # Both keys are in the table, so removing one is an allowed difference.
        assert violations('types:\n  T:\n    example: 1\n', 'types:\n  T:\n    examples:\n      a: 1\n') == []

    def test_an_extension_is_never_checked(self):
        result, _, _ = apply('/a:\n', '/b:\nversion: 2\n', overlay=False)
        assert result.error is None


class TestOverlayTypeMaps:
    def test_a_whole_types_map_the_target_lacked_is_allowed(self):
        assert violations('title: A\n', 'types:\n  B: string\n') == []
        assert violations('title: A\n', 'schemas:\n  B: string\n') == []


class TestAnnotationTypeChanges:
    def test_changed_annotation_types_are_reported_and_new_ones_are_not(self):
        result, _, _ = apply(
            'annotationTypes:\n  a: string\n  b: string\n', 'annotationTypes:\n  a: integer\n  b: string\n  c: string\n'
        )
        assert list(result.changed_annotation_types) == ['a']
