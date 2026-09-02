"""The YAML node model.

These tests pin the decisions of docs/03-yaml-and-io.md section 2 — flat mapping
content, identity hashing, 1-based positions, raw timestamp text, alias
expansion — because every layer above depends on them and a plausible-looking
refactor can break any one silently.
"""

from __future__ import annotations

import pytest

from pyraml.errors import RamlError
from pyraml.yamlnode import (
    TAG_INCLUDE,
    TAG_INT,
    TAG_MERGE,
    TAG_NULL,
    TAG_STR,
    TAG_TIMESTAMP,
    Node,
    NodeKind,
    backend_name,
    compose,
    duplicate_keys,
    end_column,
    end_line,
    is_null,
    last_leaf,
    pairs,
    read_head,
)

URI = 'file:///t/api.raml'


def parse(text: str, **kwargs) -> Node:
    return compose(text, uri=URI, **kwargs)


class TestStructure:
    def test_mapping_content_is_flat(self):
        # [k0, v0, k1, v1], not a list of pairs: decoders step by two and
        # allocate no tuples. See docs/12-performance.md section 5.
        root = parse('a: 1\nb: 2\n')
        assert root.kind is NodeKind.MAPPING
        assert [n.value for n in root.content] == ['a', '1', 'b', '2']

    def test_pairs_helper_walks_the_flat_content(self):
        root = parse('a: 1\nb: 2\n')
        assert [(k.value, v.value) for k, v in pairs(root)] == [('a', '1'), ('b', '2')]

    def test_sequences_hold_their_items_directly(self):
        root = parse('xs: [a, b, c]\n')
        _, value = next(pairs(root))
        assert value.kind is NodeKind.SEQUENCE
        assert [n.value for n in value.content] == ['a', 'b', 'c']

    def test_nesting(self):
        root = parse('types:\n  User:\n    type: string\n')
        _, types = next(pairs(root))
        _, user = next(pairs(types))
        assert [n.value for n in user.content] == ['type', 'string']


class TestIdentity:
    def test_nodes_hash_by_identity(self):
        # Required: the provenance overlay is dict[Node, ParseCtx] keyed by
        # object identity. See docs/08-templates-and-endpoints.md section 6.5.
        a = Node(NodeKind.SCALAR, TAG_STR, 'same')
        b = Node(NodeKind.SCALAR, TAG_STR, 'same')
        assert a != b
        assert hash(a) != hash(b) or a is not b
        assert len({a: 1, b: 2}) == 2

    def test_a_node_survives_as_a_dict_key(self):
        root = parse('a: 1\n')
        overlay = {root.content[1]: 'scope'}
        assert overlay[root.content[1]] == 'scope'


class TestPositions:
    def test_positions_are_one_based(self):
        root = parse('title: My API\n')
        key, value = next(pairs(root))
        assert (key.line, key.column) == (1, 1)
        assert (value.line, value.column) == (1, 8)

    def test_scalar_span_covers_the_value(self):
        root = parse('title: My API\n')
        _, value = next(pairs(root))
        assert (value.end_line, value.end_column) == (1, 14)

    def test_include_span_covers_the_tag(self):
        # `!include a.raml` must underline all of it, not just the filename.
        root = parse('inc: !include a.raml\n')
        _, value = next(pairs(root))
        assert value.tag == TAG_INCLUDE
        assert value.value == 'a.raml'
        assert (value.column, value.end_column) == (6, 21)

    def test_full_position_of_a_block_reaches_the_last_leaf(self):
        root = parse('a:\n  b: 1\n  c: 2\n')
        _, block = next(pairs(root))
        span = block.full_position
        assert span.line == 2
        assert span.end_line == 3

    def test_last_leaf_helpers(self):
        root = parse('a:\n  b:\n    c: value\n')
        assert last_leaf(root).value == 'value'
        assert end_line(root) == 3
        assert end_column(root) == 13


class TestTags:
    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            ('x: text', TAG_STR),
            ('x: 5', TAG_INT),
            ('x: ~', TAG_NULL),
            ('x:', TAG_NULL),
            ('x: 2015-05-23', TAG_TIMESTAMP),
            ('x: !include a.raml', TAG_INCLUDE),
        ],
    )
    def test_tags_use_the_short_form(self, source, expected):
        _, value = next(pairs(parse(source + '\n')))
        assert value.tag == expected

    def test_timestamps_keep_their_raw_text(self):
        # RAML validates a `date-only` example against its own grammar, so the
        # literal text must survive rather than becoming a datetime.
        _, value = next(pairs(parse('birthday: 2015-05-23\n')))
        assert value.value == '2015-05-23'

    def test_integers_keep_their_raw_text(self):
        # Numeric facet bounds are parsed exactly, never through float.
        _, value = next(pairs(parse('minimum: 10000000000000000001\n')))
        assert value.value == '10000000000000000001'

    def test_is_null_covers_the_empty_value(self):
        root = parse('get:\nx: ~\n')
        assert all(is_null(v) for _, v in pairs(root))

    def test_merge_keys_are_preserved_not_expanded(self):
        # RAML gives `<<` no meaning. Preserving it lets a decoder report
        # `unknown field: <<` rather than silently absorbing the merged keys.
        root = parse('base: &b {a: 1}\nd: {<<: *b, c: 2}\n')
        _, mapping = list(pairs(root))[1]
        first_key, _ = next(pairs(mapping))
        assert first_key.value == '<<'
        assert first_key.tag == TAG_MERGE


class TestAliases:
    def test_aliases_are_expanded_into_independent_nodes(self):
        # PyYAML returns the same object for an alias, which would give one node
        # two logical positions and break the identity-keyed overlay.
        root = parse('a: &x {k: 1}\nb: *x\n')
        (_, first), (_, second) = pairs(root)
        assert first is not second
        assert first.content[1].value == second.content[1].value == '1'
        assert first.content[1] is not second.content[1]

    def test_a_recursive_anchor_is_rejected(self):
        with pytest.raises(RamlError, match='recursive YAML anchor'):
            parse('a: &x\n  - *x\n')

    def test_expansion_is_bounded(self):
        # A "billion laughs" document must produce a diagnostic, not an OOM.
        source = 'a0: &a0 [x, x, x, x]\n'
        for level in range(1, 8):
            prev = f'*a{level - 1}'
            source += f'a{level}: &a{level} [{", ".join([prev] * 4)}]\n'
        with pytest.raises(RamlError, match='too many nodes'):
            parse(source, max_nodes=5000)


class TestLimitsAndErrors:
    def test_deep_nesting_reports_a_diagnostic_not_a_recursion_error(self):
        source = 'a:\n' + ''.join('  ' * i + f'k{i}:\n' for i in range(1, 60))
        with pytest.raises(RamlError, match='nesting too deep'):
            parse(source, max_depth=10)

    def test_syntax_errors_carry_a_position(self):
        with pytest.raises(RamlError) as excinfo:
            parse('a: [1, 2\nb: 3\n')
        frame = excinfo.value.frames()[0]
        assert frame.position is not None
        assert frame.location == URI

    def test_syntax_error_message_drops_the_pyyaml_position_prose(self):
        with pytest.raises(RamlError) as excinfo:
            parse('a: [1, 2\nb: 3\n')
        assert 'line' not in str(excinfo.value.head.message)


class TestEmptyDocuments:
    @pytest.mark.parametrize('source', ['', '\n', '# only a comment\n', '#%RAML 1.0 Trait\n'])
    def test_empty_documents_compose_to_an_empty_mapping(self, source):
        # Each fragment decoder decides whether an empty body is valid for its
        # kind; composition does not pre-empt that. See docs/03 section 2.2.
        root = parse(source)
        assert root.kind is NodeKind.MAPPING
        assert root.content == []


class TestDuplicateKeys:
    def test_duplicates_are_recorded_not_rejected(self):
        root = parse('a: 1\nb: 2\na: 3\n')
        found = duplicate_keys(root)
        assert [name for name, _ in found] == ['a']
        assert found[0][1].line == 3

    def test_no_duplicates_reports_nothing(self):
        assert duplicate_keys(parse('a: 1\nb: 2\n')) == []

    def test_a_scalar_has_no_duplicates(self):
        assert duplicate_keys(Node(NodeKind.SCALAR, TAG_STR, 'x')) == []


class TestReadHead:
    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            ('#%RAML 1.0\ntitle: X\n', '#%RAML 1.0'),
            ('#%RAML 1.0 Trait\r\nx: 1\n', '#%RAML 1.0 Trait'),
            ('#%RAML 1.0 Library   \n', '#%RAML 1.0 Library'),
            ('#%RAML 1.0', '#%RAML 1.0'),
            ('', ''),
        ],
    )
    def test_reads_the_first_line(self, source, expected):
        assert read_head(source) == expected

    def test_the_head_is_not_removed_so_line_numbers_stay_true(self):
        source = '#%RAML 1.0\ntitle: My API\n'
        assert read_head(source) == '#%RAML 1.0'
        key, _ = next(pairs(parse(source)))
        assert key.line == 2


class TestLineEndings:
    def test_crlf_input_yields_the_same_values_and_lines(self):
        # RAML files authored on Windows commonly use CRLF. The carriage return
        # must not end up in a scalar value or shift a line number.
        crlf = parse('#%RAML 1.0\r\ntitle: My API\r\ndescription: text\r\n')
        assert [(k.value, v.value) for k, v in pairs(crlf)] == [
            ('title', 'My API'),
            ('description', 'text'),
        ]
        assert crlf.content[0].line == 2
        assert crlf.content[2].line == 3

    def test_crlf_in_a_block_scalar_is_normalised(self):
        _, value = next(pairs(parse('description: |\r\n  one\r\n  two\r\n')))
        assert value.value == 'one\ntwo\n'


class TestEncoding:
    def test_bytes_are_decoded_as_utf8(self):
        root = compose('title: café\n'.encode(), uri=URI)
        _, value = next(pairs(root))
        assert value.value == 'café'

    def test_a_byte_order_mark_is_stripped(self):
        # A BOM in front of `#%RAML` would defeat the fragment-header check.
        source = '\ufeff#%RAML 1.0\ntitle: X\n'.encode()
        assert read_head(compose(source, uri=URI).content[0].value) == 'title'


def test_backend_is_reported():
    assert backend_name() in {'libyaml', 'python'}
