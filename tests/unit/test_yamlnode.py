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
    decode_source,
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

    @pytest.mark.parametrize(
        'source',
        ['x: !includeexample.json', 'x: !foo bar', 'x: !foo [1, 2]', 'x: !foo {a: 1}'],
        ids=['run-together-include', 'scalar', 'sequence', 'mapping'],
    )
    def test_an_unrecognised_local_tag_is_rejected(self, source):
        # `!include` is the only tag RAML defines. `!includeexample.json` is
        # otherwise a perfectly good local tag on an empty scalar, so the
        # document would parse with an empty value where a file was meant.
        with pytest.raises(RamlError) as caught:
            parse(source + '\n')
        assert 'unknown tag' in str(caught.value)

    def test_yamls_own_tags_are_not_local_tags(self):
        _, value = next(pairs(parse('x: !!str 5\n')))
        assert value.tag == TAG_STR

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
    """`decode_source` is the single place where bytes become text.

    `compose` takes `str`. Every entry point that reads a loader passes through
    here first, so the encoding policy lives in one function instead of being
    repeated at each of them.
    """

    def test_bytes_are_decoded_as_utf8(self):
        assert decode_source('title: café\n'.encode()) == 'title: café\n'

    def test_a_byte_order_mark_is_stripped(self):
        # A BOM in front of `#%RAML` would defeat the fragment-header check.
        source = '\ufeff#%RAML 1.0\ntitle: X\n'.encode()
        assert read_head(decode_source(source)) == '#%RAML 1.0'

    def test_non_utf8_input_is_rejected_rather_than_guessed(self):
        # RAML is UTF-8 (spec section Markup Language). Sniffing an encoding
        # would make the same bytes mean different things on different machines.
        with pytest.raises(UnicodeDecodeError):
            decode_source('title: café\n'.encode('utf-16'))


def test_backend_is_reported():
    assert backend_name() in {'libyaml', 'python'}


class TestLineSeparators:
    """U+2028 and U+2029 (docs/01 section 4, D10).

    YAML 1.1 reads them as line breaks and YAML 1.2 does not, so PyYAML silently
    splits an unquoted scalar that contains one and then fails somewhere else.
    The character cannot be supported, but the diagnostic can point at it.
    """

    SEPARATOR = chr(0x2028)
    PARAGRAPH = chr(0x2029)

    @pytest.mark.parametrize(
        ('label', 'template'),
        [('plain', 'a: x{sep}y'), ('block literal', 'a: |\n  x{sep}y'), ('key', 'x{sep}y: 1')],
    )
    def test_an_unquoted_separator_is_named_and_located(self, label: str, template: str):
        with pytest.raises(RamlError) as caught:
            compose(template.format(sep=self.SEPARATOR), uri='file:///a.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'unquoted line separator character'
        assert trace.info['character'] == 'U+2028'
        assert trace.position.line == (2 if label == 'block literal' else 1)

    def test_the_paragraph_separator_is_named_too(self):
        with pytest.raises(RamlError) as caught:
            compose(f'a: x{self.PARAGRAPH}y', uri='file:///a.raml')
        assert next(iter(caught.value.chains()))[-1].info['character'] == 'U+2029'

    @pytest.mark.parametrize('quote', ["'", '"'])
    def test_a_quoted_separator_parses_and_round_trips(self, quote: str):
        # PyYAML handles these correctly inside quotes, so the diagnostic must
        # not fire: it is consulted only after composition has already failed.
        node = compose(f'a: {quote}x{self.SEPARATOR}y{quote}', uri='file:///a.raml')
        assert node.content[1].value == f'x{self.SEPARATOR}y'

    def test_a_quoted_separator_does_not_hijack_an_unrelated_error(self):
        # The check is only allowed to fire when the separators are the cause.
        # Here one sits harmlessly inside quotes and the real fault is a flow
        # sequence three lines later; that is what must be reported.
        source = f"a: 'x{self.SEPARATOR}y'\nb: [1,"
        with pytest.raises(RamlError) as caught:
            compose(source, uri='file:///a.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message != 'unquoted line separator character'

    def test_an_ordinary_syntax_error_is_unaffected(self):
        with pytest.raises(RamlError) as caught:
            compose('a: [1,', uri='file:///a.raml')
        assert next(iter(caught.value.chains()))[-1].message != 'unquoted line separator character'


class TestSpecialisedResolver:
    """`_RamlLoader.resolve` replaces PyYAML's, so it has to agree with it.

    The specialisation is a performance change (docs/12 § 19a) on the hottest
    callback in the parser, and its failure mode is the quietest one there is: a
    scalar silently resolving to the wrong tag. `tests/conformance` would catch
    that across the corpus; this catches it at the function, which is where it
    would be introduced.
    """

    SCALARS = (
        '',
        'k0',
        'v0-12',
        'true',
        'True',
        'TRUE',
        'false',
        'yes',
        'no',
        'on',
        'off',
        'null',
        '~',
        '42',
        '-42',
        '+42',
        '0x1f',
        '0o17',
        '0b1011',
        '1_000',
        '1.5',
        '-1.5e10',
        '.inf',
        '.nan',
        '2024-03-17',
        '12:30:00',
        '1e3',
        'a longer plain scalar with spaces',
        'http://example.com',
        '!include foo.raml',
    )

    def test_it_agrees_with_pyyamls_own_implementation(self):
        from yaml.nodes import MappingNode, ScalarNode, SequenceNode
        from yaml.resolver import BaseResolver

        from pyraml.yamlnode import _RamlLoader

        loader = _RamlLoader.__new__(_RamlLoader)
        for value in self.SCALARS:
            for implicit in ((True, False), (False, True), (False, False)):
                ours = _RamlLoader.resolve(loader, ScalarNode, value, implicit)
                stock = BaseResolver.resolve(loader, ScalarNode, value, implicit)
                assert ours == stock, (value, implicit)
        for kind in (SequenceNode, MappingNode):
            assert _RamlLoader.resolve(loader, kind, '', (True, False)) == BaseResolver.resolve(
                loader, kind, '', (True, False)
            )

    def test_the_preconditions_it_relies_on_are_checked_at_import(self):
        """A wildcard or path resolver would be skipped silently otherwise."""
        from pyraml.yamlnode import _assert_resolver_shape, _RamlLoader

        _assert_resolver_shape()  # the real table
        assert None not in _RamlLoader.yaml_implicit_resolvers
        assert not _RamlLoader.yaml_path_resolvers
