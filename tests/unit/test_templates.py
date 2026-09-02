"""Template variables: docs/08-templates-and-endpoints.md section 7.

These tests pin: the `<<name | !action>>` grammar, the ten transform
functions (including the compound-word and irregular-plural cases the spec's
own examples give), and — the detail the section calls out as risky — that
`collect_variables_index` and any future walk built on `iter_indexed` compute
the identical positional index for the identical node.
"""

from __future__ import annotations

import pytest

from pyraml.errors import RamlError
from pyraml.parser.templates import (
    TEMPLATE_ACTIONS,
    VariableInfo,
    apply_template_action,
    collect_required_variables,
    collect_variables_index,
    iter_indexed,
    parse_template_variables,
)
from pyraml.yamlnode import TAG_STR, Node, NodeKind, compose, pairs

LOCATION = 'file:///t/api.raml'


def parse(text: str) -> Node:
    return compose(text, uri=LOCATION)


class TestParseTemplateVariables:
    def test_single_variable(self):
        variables = parse_template_variables('Hello <<name>>!', LOCATION)
        assert len(variables) == 1
        assert variables[0].name == 'name'
        assert variables[0].substring == '<<name>>'
        assert variables[0].actions == ()

    def test_whitespace_around_name_and_action_is_tolerated(self):
        variables = parse_template_variables('<< name | !singularize >>', LOCATION)
        assert variables[0].name == 'name'
        assert variables[0].actions == ('!singularize',)
        assert variables[0].substring == '<< name | !singularize >>'

    def test_multiple_actions_in_order(self):
        variables = parse_template_variables('<<name|!uppercase|!pluralize>>', LOCATION)
        assert variables[0].actions == ('!uppercase', '!pluralize')

    def test_several_variables_in_one_scalar(self):
        variables = parse_template_variables('<<a>> and <<b>> and <<c>>', LOCATION)
        assert [v.name for v in variables] == ['a', 'b', 'c']

    def test_same_variable_appearing_twice(self):
        variables = parse_template_variables('<<name>> then <<name>> again', LOCATION)
        assert [v.name for v in variables] == ['name', 'name']
        assert variables[0].substring == variables[1].substring == '<<name>>'

    def test_no_variables_returns_empty_list(self):
        assert parse_template_variables('just plain text', LOCATION) == []

    def test_unclosed_variable_raises(self):
        with pytest.raises(RamlError) as excinfo:
            parse_template_variables('prefix <<name', LOCATION)
        assert excinfo.value.head.message == 'unclosed template variable'

    def test_action_without_variable_name_raises(self):
        with pytest.raises(RamlError) as excinfo:
            parse_template_variables('<<!uppercase>>', LOCATION)
        assert excinfo.value.head.message == 'action without variable name'

    def test_action_missing_bang_raises(self):
        with pytest.raises(RamlError) as excinfo:
            parse_template_variables('<<name|uppercase>>', LOCATION)
        assert excinfo.value.head.message == "invalid action, must start with '!'"
        assert excinfo.value.head.info == {'action': 'uppercase'}

    def test_unknown_action_raises(self):
        with pytest.raises(RamlError) as excinfo:
            parse_template_variables('<<name|!bogus>>', LOCATION)
        assert excinfo.value.head.message == 'unknown action'
        assert excinfo.value.head.info == {'action': '!bogus'}

    def test_blank_content_is_missing_variable_name(self):
        with pytest.raises(RamlError) as excinfo:
            parse_template_variables('<<   >>', LOCATION)
        assert excinfo.value.head.message == 'missing variable name'


class TestApplyTemplateAction:
    def test_uppercase(self):
        assert apply_template_action('abc', '!uppercase') == 'ABC'

    def test_lowercase(self):
        assert apply_template_action('AbC', '!lowercase') == 'abc'

    # Compound-word cases from the RAML 1.0 spec's own transform-function table.
    def test_uppercamelcase_single_camel_word(self):
        assert apply_template_action('userId', '!uppercamelcase') == 'UserId'

    def test_lowercamelcase_single_camel_word(self):
        assert apply_template_action('UserId', '!lowercamelcase') == 'userId'

    def test_upperunderscorecase_compound(self):
        assert apply_template_action('userId', '!upperunderscorecase') == 'USER_ID'

    def test_lowerunderscorecase_compound(self):
        assert apply_template_action('userId', '!lowerunderscorecase') == 'user_id'

    def test_upperhyphencase_compound(self):
        assert apply_template_action('userId', '!upperhyphencase') == 'USER-ID'

    def test_lowerhyphencase_compound(self):
        assert apply_template_action('userId', '!lowerhyphencase') == 'user-id'

    def test_singularize(self):
        assert apply_template_action('users', '!singularize') == 'user'

    def test_pluralize(self):
        assert apply_template_action('user', '!pluralize') == 'users'

    def test_camelcase_joins_separator_split_words(self):
        assert apply_template_action('hello_world', '!uppercamelcase') == 'HelloWorld'
        assert apply_template_action('hello_world', '!lowercamelcase') == 'helloWorld'
        assert apply_template_action('hello-world', '!uppercamelcase') == 'HelloWorld'

    def test_underscorecase_does_not_double_separate_existing_words(self):
        assert apply_template_action('hello_world', '!upperunderscorecase') == 'HELLO_WORLD'
        assert apply_template_action('hello_world', '!lowerunderscorecase') == 'hello_world'

    def test_unknown_action_returns_value_unchanged(self):
        assert apply_template_action('abc', '!nope') == 'abc'

    def test_empty_string_returns_empty_string_for_every_action(self):
        for action in TEMPLATE_ACTIONS:
            assert apply_template_action('', action) == ''

    @pytest.mark.parametrize(
        ('singular', 'plural'),
        [
            ('medium', 'media'),
            ('memorandum', 'memoranda'),
            ('vortex', 'vortices'),
        ],
    )
    def test_irregular_plurals_both_directions(self, singular, plural):
        assert apply_template_action(singular, '!pluralize') == plural
        assert apply_template_action(plural, '!singularize') == singular


class TestVariablesIndex:
    def test_collects_declared_variables_and_positional_index(self):
        root = parse('description: Create a new <<resourcePathName | !singularize>>\n')
        declared, index = collect_variables_index(root, LOCATION)
        assert declared == {'resourcePathName'}
        assert list(index.values()) == [
            [VariableInfo('resourcePathName', '<<resourcePathName | !singularize>>', ('!singularize',))]
        ]

    def test_only_str_scalars_are_scanned(self):
        # A non-!!str scalar that happens to contain "<<...>>" text is left
        # alone; only !!str carries template variables.
        non_str_scalar = Node(NodeKind.SCALAR, '!!int', '<<shouldBeIgnored>>')
        str_scalar = Node(NodeKind.SCALAR, TAG_STR, '<<realVar>>')
        root = Node(NodeKind.SEQUENCE, '!!seq', content=[non_str_scalar, str_scalar])

        declared, _index = collect_variables_index(root, LOCATION)

        assert declared == {'realVar'}

    def test_indexer_agrees_with_an_independent_walk_over_nested_structure(self):
        # Nested sequences and mappings, several levels deep, mirroring the
        # shape a resource type / trait body actually has.
        root = parse("""
queryParameters:
  <<queryParamName>>:
    description: Filter by <<queryParamName | !lowercamelcase>>
is: [<<traitName>>, secured]
responses:
  200:
    body:
      application/json:
        example: |
          plain text, no variables here
""")
        declared, index = collect_variables_index(root, LOCATION)

        # A stand-in for a future substitution pass: it knows nothing about
        # `collect_variables_index`'s internals, but it uses the same shared
        # walk helper and the same "!!str scalar" filter. If the two walks
        # ever computed different indices for the same node, this would fail.
        mirrored: dict[int, list[VariableInfo]] = {}
        for idx, node in iter_indexed(root):
            if node.kind is not NodeKind.SCALAR or node.tag != TAG_STR:
                continue
            variables = parse_template_variables(node.value, LOCATION)
            if variables:
                mirrored[idx] = variables

        assert mirrored == index
        assert declared == {'queryParamName', 'traitName'}

    def test_unclosed_variable_reports_the_scalars_location(self):
        root = parse('description: prefix <<broken\n')
        with pytest.raises(RamlError) as excinfo:
            collect_variables_index(root, LOCATION)
        assert excinfo.value.head.message == 'parse template variables'
        assert excinfo.value.frames()[-1].message == 'unclosed template variable'


class TestCollectRequiredVariables:
    def test_scopes_to_the_given_subtree(self):
        # A shallow mapping with two direct children: one whose value carries
        # a variable, one whose value does not. Kept shallow deliberately —
        # the `idx + i` scheme is positional, not a unique node id (docs/08
        # section 7.1), so two *different* nodes several levels apart can
        # legitimately compute the same index. A deep fixture here would
        # exercise that collision instead of the subtree-scoping behaviour
        # this test targets.
        root = parse('a: <<foo>>\nb: plain\n')
        _declared, index = collect_variables_index(root, LOCATION)

        (_a_key, a_value), (_b_key, b_value) = pairs(root)
        # `Node` is identity-hashable by design (docs/03 section 2), so the
        # node itself is a safe dict key: no `id()`, nothing to keep alive
        # separately.
        idx_of_node = {node: idx for idx, node in iter_indexed(root)}

        a_required = collect_required_variables(a_value, idx_of_node[a_value], index)
        b_required = collect_required_variables(b_value, idx_of_node[b_value], index)

        assert a_required == {'foo'}
        assert b_required == set()

    def test_scopes_to_a_standalone_compiled_tree(self):
        # Mirrors the spec's own corpResource/queues example (docs/08 section
        # 5.1 step 4): required variables are recollected "from the filtered
        # tree" — a template's own compiled subtree, indexed from its own
        # root (idx 0) — not from a random subtree of a larger shared
        # document sharing one global numbering.
        post_tree = parse('body:\n  application/json:\n    example: <<TextAboutPost>>\n')
        queues_tree = parse('body:\n  application/json:\n    example: static text\n')

        _declared_post, post_index = collect_variables_index(post_tree, LOCATION)
        _declared_queues, queues_index = collect_variables_index(queues_tree, LOCATION)

        assert collect_required_variables(post_tree, 0, post_index) == {'TextAboutPost'}
        assert collect_required_variables(queues_tree, 0, queues_index) == set()

    def test_no_variables_returns_empty_set(self):
        root = parse('description: nothing to see here\n')
        _declared, index = collect_variables_index(root, LOCATION)
        assert collect_required_variables(root, 0, index) == set()


class TestIndexUniqueness:
    """The positional index must be injective.

    go-raml's `idx + i` rule is not: a node and its first child share an index.
    Substitution tolerates that (replacing an absent substring is a no-op), but
    a required-variable scan does not — it reports variables from unrelated
    branches. See `iter_indexed`.
    """

    def test_every_node_gets_a_distinct_index(self):
        from pyraml.parser.templates import iter_indexed
        from pyraml.yamlnode import compose

        node = compose(
            'post:\n'
            '  body:\n'
            '    application/json:\n'
            '      example: <<TextAboutPost>>\n'
            'get:\n'
            '  responses:\n'
            '    200:\n'
            '      description: ok\n',
            uri='file:///t.raml',
        )
        pairs_seen = list(iter_indexed(node))
        indices = [i for i, _ in pairs_seen]
        assert len(indices) == len(set(indices)), 'positional indices collided'
        assert indices == list(range(len(indices))), 'indices are not a dense preorder sequence'

    def test_a_sibling_branch_does_not_report_another_branch_variable(self):
        # The concrete failure the collision caused: `get` has no variables, but
        # under `idx + i` its `200` key shared an index with `<<TextAboutPost>>`.
        from pyraml.parser.templates import collect_required_variables, collect_variables_index, iter_indexed
        from pyraml.yamlnode import compose, pairs

        node = compose(
            'post:\n'
            '  body:\n'
            '    application/json:\n'
            '      example: <<TextAboutPost>>\n'
            'get:\n'
            '  responses:\n'
            '    200:\n'
            '      description: ok\n',
            uri='file:///t.raml',
        )
        _declared, index = collect_variables_index(node, 'file:///t.raml')
        by_index = {id(n): i for i, n in iter_indexed(node)}
        get_value = next(v for k, v in pairs(node) if k.value == 'get')
        assert collect_required_variables(get_value, by_index[id(get_value)], index) == set()

    def test_the_owning_branch_still_reports_its_variable(self):
        from pyraml.parser.templates import collect_required_variables, collect_variables_index, iter_indexed
        from pyraml.yamlnode import compose, pairs

        node = compose(
            'post:\n  body:\n    example: <<TextAboutPost>>\nget:\n  description: ok\n',
            uri='file:///t.raml',
        )
        _declared, index = collect_variables_index(node, 'file:///t.raml')
        by_index = {id(n): i for i, n in iter_indexed(node)}
        post_value = next(v for k, v in pairs(node) if k.value == 'post')
        assert collect_required_variables(post_value, by_index[id(post_value)], index) == {'TextAboutPost'}
