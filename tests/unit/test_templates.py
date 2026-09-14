"""Template variables: docs/08-templates-and-endpoints.md section 7.

These tests pin: the `<<name | !action>>` grammar, the ten transform
functions (including the compound-word and irregular-plural cases the spec's
own examples give), and — the detail the section calls out as risky —
that the variable index survives the optional-method filtering that happens
between the scan and its use, and that substitution records which values came
from the caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from fastraml.errors import RamlError
from fastraml.parser.templates import (
    TEMPLATE_ACTIONS,
    VariableInfo,
    apply_template_action,
    collect_required_variables,
    collect_variables_index,
    compile_source_provenance,
    iter_nodes,
    parse_template_variables,
)
from fastraml.registry import ParseCtx
from fastraml.yamlnode import TAG_STR, Node, NodeKind, compose, pairs

LOCATION = 'file:///t/api.raml'
CALLER = ParseCtx()


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
    def test_the_overrides_inflect_in_both_directions(self, singular, plural):
        assert apply_template_action(singular, '!pluralize') == plural
        assert apply_template_action(plural, '!singularize') == singular


class TestPluralizationParity:
    """`!singularize` and `!pluralize` agree with go-raml, word for word.

    The two actions need a dictionary, not a rule, so the only way to agree with
    the reference implementation is to share its dictionary: `go-pluralize` and
    `pluralizer` are both ports of one JavaScript library. An earlier design
    paired a *different* pluraliser with a three-word override table, which
    diverged on 298 of these 618 answers while passing every test that named
    only those three words. Hence a table rather than examples.

    `data/pluralize_parity.tsv` was generated by go-raml's own
    `applyTemplateAction` over go-pluralize's whole irregular and uncountable
    tables. See docs/08 section 7.3.
    """

    TABLE: ClassVar[list[list[str]]] = [
        line.split('\t')
        for line in (Path(__file__).parent / 'data' / 'pluralize_parity.tsv').read_text(encoding='utf-8').splitlines()
        if not line.startswith('#')
    ]

    def test_the_table_is_the_size_it_should_be(self):
        assert len(self.TABLE) > 300, 'the parity table did not load; every check below would be vacuous'

    @pytest.mark.parametrize('action', ['!pluralize', '!singularize'])
    def test_every_word_agrees_with_the_reference_implementation(self, action):
        column = 1 if action == '!pluralize' else 2
        wrong = [
            f'{action} {word!r}: {apply_template_action(word, action)!r} != {row[column]!r}'
            for row in self.TABLE
            for word in [row[0]]
            if apply_template_action(word, action) != row[column]
        ]
        assert not wrong, '\n'.join(wrong[:20])


class TestVariablesIndex:
    def test_collects_declared_variables_and_the_occurrences(self):
        root = parse('description: Create a new <<resourcePathName | !singularize>>\n')
        declared, index = collect_variables_index(root, LOCATION)
        assert declared == {'resourcePathName'}
        assert list(index.values()) == [
            [VariableInfo('resourcePathName', '<<resourcePathName | !singularize>>', ('!singularize',))]
        ]

    def test_the_key_is_the_scalar_node_itself(self):
        # `Node` is identity-hashable by design (docs/03 section 2), so the node
        # is a safe dict key: no `id()`, and nothing to keep alive separately.
        root = parse('description: <<foo>>\n')
        _declared, index = collect_variables_index(root, LOCATION)
        ((_key, value),) = pairs(root)
        assert list(index) == [value]

    def test_only_str_scalars_are_scanned(self):
        # A non-!!str scalar that happens to contain "<<...>>" text is left
        # alone; only !!str carries template variables.
        non_str_scalar = Node(NodeKind.SCALAR, '!!int', '<<shouldBeIgnored>>')
        str_scalar = Node(NodeKind.SCALAR, TAG_STR, '<<realVar>>')
        root = Node(NodeKind.SEQUENCE, '!!seq', content=[non_str_scalar, str_scalar])

        declared, _index = collect_variables_index(root, LOCATION)

        assert declared == {'realVar'}

    def test_every_variable_bearing_scalar_of_a_nested_body_is_found(self):
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

        mirrored = {
            node: parse_template_variables(node.value, LOCATION)
            for node in iter_nodes(root)
            if node.kind is NodeKind.SCALAR and node.tag == TAG_STR and parse_template_variables(node.value, LOCATION)
        }
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
        root = parse('a: <<foo>>\nb: plain\n')
        _declared, index = collect_variables_index(root, LOCATION)
        (_a_key, a_value), (_b_key, b_value) = pairs(root)

        assert collect_required_variables(a_value, index) == {'foo'}
        assert collect_required_variables(b_value, index) == set()

    def test_a_sibling_branch_does_not_report_another_branch_variable(self):
        # The failure a positional index caused: `get` has no variables, but a
        # non-injective numbering had its `200` key sharing an index with
        # `<<TextAboutPost>>` (docs/08 section 7.1).
        root = parse(
            'post:\n'
            '  body:\n'
            '    application/json:\n'
            '      example: <<TextAboutPost>>\n'
            'get:\n'
            '  responses:\n'
            '    200:\n'
            '      description: ok\n'
        )
        _declared, index = collect_variables_index(root, LOCATION)
        get_value = next(value for key, value in pairs(root) if key.value == 'get')
        post_value = next(value for key, value in pairs(root) if key.value == 'post')

        assert collect_required_variables(get_value, index) == set()
        assert collect_required_variables(post_value, index) == {'TextAboutPost'}

    def test_the_index_survives_a_subtree_being_filtered_out(self):
        # docs/08 section 5.1: optional methods are removed from the tree
        # *before* the required variables are recollected. A positional index
        # does not survive that removal — every later node shifts — which is why
        # go-raml demands `<<TextAboutPost>>` from a resource that has no `post`
        # (KNOWN-ISSUES.md). Identity keys are unaffected by it.
        root = parse('post:\n  description: <<TextAboutPost>>\nget:\n  description: <<TextAboutGet>>\n')
        _declared, index = collect_variables_index(root, LOCATION)
        filtered = Node(NodeKind.MAPPING, root.tag, root.value, list(root.content[2:]))

        assert collect_required_variables(filtered, index) == {'TextAboutGet'}

    def test_no_variables_returns_empty_set(self):
        root = parse('description: nothing to see here\n')
        _declared, index = collect_variables_index(root, LOCATION)
        assert collect_required_variables(root, index) == set()


class TestCompileSourceProvenance:
    """Substitution, and the marks that say which namespace a value resolves in."""

    def compile(self, text: str, params: dict[str, str] | None = None, complex_params: dict | None = None):
        root = parse(text)
        _declared, index = collect_variables_index(root, LOCATION)
        nodes = {name: Node(NodeKind.SCALAR, TAG_STR, value) for name, value in (params or {}).items()}
        nodes.update(complex_params or {})
        overlay: dict = {}
        return root, compile_source_provenance(root, nodes, index, CALLER, overlay), overlay

    def test_a_variable_is_replaced_in_place(self):
        _root, compiled, _overlay = self.compile('description: about <<what>>\n', {'what': 'queues'})
        assert compiled.content[1].value == 'about queues'

    def test_actions_apply_in_order(self):
        _root, compiled, _overlay = self.compile(
            'description: <<name | !singularize | !uppercase>>\n', {'name': 'queues'}
        )
        assert compiled.content[1].value == 'QUEUE'

    def test_several_variables_in_one_scalar(self):
        _root, compiled, _overlay = self.compile('a: <<x>>/<<y>>\n', {'x': 'one', 'y': 'two'})
        assert compiled.content[1].value == 'one/two'

    def test_an_unsupplied_variable_is_left_as_written(self):
        # The parameter checks of section 5 catch this; substitution does not
        # get to invent a value, and must not mark the node as caller-scoped.
        root, compiled, overlay = self.compile('a: <<missing>>\n')
        assert compiled is root
        assert overlay == {}

    def test_a_static_body_is_returned_by_identity(self):
        root, compiled, overlay = self.compile('a: 1\nb: {c: d}\n')
        assert compiled is root
        assert overlay == {}

    def test_neither_the_input_nor_its_untouched_branches_are_rebuilt(self):
        root, compiled, _overlay = self.compile('a: <<x>>\nb: {c: static}\n', {'x': 'v'})
        assert compiled is not root
        assert root.content[1].value == '<<x>>', 'the template body stays reusable'
        assert compiled.content[3] is root.content[3], 'an untouched branch keeps its identity'

    def test_a_substituted_scalar_is_marked_caller_scoped(self):
        _root, compiled, overlay = self.compile('a: <<x>>\n', {'x': 'v'})
        assert overlay[compiled.content[1]] is CALLER

    def test_a_complex_parameter_replaces_the_node_and_is_marked(self):
        value = parse('type: Foo\n')
        _root, compiled, overlay = self.compile('a: <<x>>\n', complex_params={'x': value})
        assert compiled.content[1] is value
        assert overlay[value] is CALLER
