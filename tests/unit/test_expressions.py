"""RAML type expressions (RDT): tokenizer, parser, AST, and the expression cache.

See docs/06-type-expressions.md sections 1-2. Only the grammar and the AST are
in scope here -- section 3 (AST -> shapes) depends on a type model that does
not exist yet.
"""

from __future__ import annotations

import dataclasses

import pytest

import pyraml.types.expressions.parser as parser_module
from pyraml.errors import RamlError
from pyraml.registry import Raml
from pyraml.types.expressions import (
    Array,
    ExprCache,
    Optional_,
    Primitive,
    Reference,
    Union,
)
from pyraml.types.expressions import parse_expression as parse_with_cache
from pyraml.types.expressions.lexer import Token, TokenKind, tokenize


def parse_expression(text: str, cache: ExprCache | None = None):
    """Parse with a cache of this call's own unless one is supplied.

    The real cache belongs to a `Raml` (docs/06 section 2.3), so there is no
    global to clear between tests. A test that is about the memoisation passes
    its own dict and is the only thing that shares one.
    """
    return parse_with_cache(text, {} if cache is None else cache)


# -- The reference corpus -----------------------------------------------------
# Adopted verbatim from go-raml's rdt/examples.txt (docs/06 section 1). Kept
# inline rather than read from the sibling checkout so this test runs without
# it.

EXAMPLES: list[tuple[str, object]] = [
    ('string', Primitive('string', 0)),
    ('integer?', Optional_(Primitive('integer', 0))),
    ('date-time[]', Array(Reference('date-time', 0))),
    ('Ref?', Optional_(Reference('Ref', 0))),
    ('Ref[]', Array(Reference('Ref', 0))),
    ('external.Ref', Reference('external.Ref', 0)),
    ('external.Ref?', Optional_(Reference('external.Ref', 0))),
    ('external.Ref[]', Array(Reference('external.Ref', 0))),
    ('string | nil', Union((Primitive('string', 0), Primitive('nil', 9)))),
    ('Ref | string', Union((Reference('Ref', 0), Primitive('string', 6)))),
    (
        '(string | integer)?',
        Optional_(Union((Primitive('string', 1), Primitive('integer', 10)))),
    ),
    (
        '(string | integer)[]',
        Array(Union((Primitive('string', 1), Primitive('integer', 10)))),
    ),
    (
        '(string | integer) | Ref[] | external.Ref?',
        Union(
            (
                Union((Primitive('string', 1), Primitive('integer', 10))),
                Array(Reference('Ref', 21)),
                Optional_(Reference('external.Ref', 29)),
            )
        ),
    ),
]


class TestExamplesCorpus:
    @pytest.mark.parametrize(('text', 'expected'), EXAMPLES, ids=[e[0] for e in EXAMPLES])
    def test_parses_to_the_expected_tree(self, text, expected):
        assert parse_expression(text) == expected


class TestPrecedence:
    def test_array_binds_tighter_than_union(self):
        # `Person | Animal[]` is `Person | (Animal[])`: grouping is required
        # to say otherwise. docs/06 section 1.
        assert parse_expression('Person | Animal[]') == Union((Reference('Person', 0), Array(Reference('Animal', 9))))

    def test_grouping_reverses_the_default_precedence(self):
        assert parse_expression('(Person | Animal)[]') == Array(
            Union((Reference('Person', 1), Reference('Animal', 10)))
        )


class TestArraySugar:
    def test_nested_arrays(self):
        assert parse_expression('string[][]') == Array(Array(Primitive('string', 0)))

    def test_optional_array_is_not_an_array_of_optionals(self):
        # `?` is postfix on `type`, applied *after* every `[]`.
        assert parse_expression('string[]?') == Optional_(Array(Primitive('string', 0)))
        assert parse_expression('string[]?') != Array(Optional_(Primitive('string', 0)))


class TestSingleMemberUnionCollapses:
    def test_a_lone_member_is_not_wrapped_in_union(self):
        assert parse_expression('string') == Primitive('string', 0)
        assert not isinstance(parse_expression('string'), Union)

    def test_a_grouped_lone_member_still_collapses(self):
        assert parse_expression('(string)') == Primitive('string', 1)


class TestDottedReferences:
    def test_a_dotted_name_is_kept_whole(self):
        # IDENTIFIER already includes '.'; the resolver (out of scope here)
        # splits on the *last* dot, not this parser. docs/06 section 1.
        assert parse_expression('lib.Thing') == Reference('lib.Thing', 0)

    def test_a_double_dot_is_syntactically_valid(self):
        # Judgment call: `Foo..Bar` matches IDENTIFIER's `[0-9a-zA-Z_.-]+`
        # verbatim -- there is no rule in the grammar that rejects a repeated
        # or trailing '.'. Whether "Foo." or "Foo..Bar" denotes a resolvable
        # type is a resolver-level question (docs/04 section 3), not a
        # grammar-level one, so this parser accepts it and keeps the name
        # whole, exactly as it would keep any other IDENTIFIER.
        assert parse_expression('Foo..Bar') == Reference('Foo..Bar', 0)


class TestMalformedInput:
    @pytest.mark.parametrize(
        ('text', 'column'),
        [
            ('', 0),  # nothing at all
            ('|', 0),  # union with no first member
            ('string |', 8),  # union with no second member
            ('(string', 7),  # unclosed group
            ('string)', 6),  # unopened / extra close paren
            ('string string', 7),  # trailing garbage after a complete expression
            ('[', 0),  # a lone '[' is never valid; only '[]' is a token
            (']', 0),
            ('@Foo', 0),  # a character outside IDENTIFIER's set
        ],
        ids=[
            'empty',
            'bare-pipe',
            'union-missing-second-member',
            'unclosed-group',
            'unopened-group',
            'trailing-garbage',
            'lone-open-bracket',
            'lone-close-bracket',
            'illegal-character',
        ],
    )
    def test_raises_raml_error_with_the_offending_column(self, text, column):
        with pytest.raises(RamlError) as excinfo:
            parse_expression(text)
        err = excinfo.value
        assert err.head.message == 'invalid type expression'
        assert err.head.info['column'] == column


class TestCache:
    def test_returns_the_identical_object_for_the_same_text(self):
        cache: ExprCache = {}
        first = parse_expression('MyType[]', cache)
        second = parse_expression('MyType[]', cache)
        assert first is second

    def test_distinct_text_is_not_shared(self):
        cache: ExprCache = {}
        assert parse_expression('MyType', cache) is not parse_expression('OtherType', cache)

    def test_two_caches_do_not_share(self):
        # The cache belongs to one parse: nothing survives into the next.
        assert parse_expression('MyType[]') is not parse_expression('MyType[]')

    def test_parses_once_for_many_repeated_calls(self, monkeypatch):
        calls = 0
        real_tokenize = parser_module.tokenize

        def counting_tokenize(text):
            nonlocal calls
            calls += 1
            return real_tokenize(text)

        monkeypatch.setattr(parser_module, 'tokenize', counting_tokenize)
        cache: ExprCache = {}
        for _ in range(500):
            parse_expression('Counted.Type[]?', cache)
        assert calls == 1

    def test_a_parse_failure_is_cached_as_the_same_exception_object(self):
        cache: ExprCache = {}
        errors = []
        for _ in range(500):
            try:
                parse_expression('(broken', cache)
            except RamlError as err:
                errors.append(err)
        assert len(errors) == 500
        assert all(err is errors[0] for err in errors)

    def test_a_parse_failure_is_only_parsed_once(self, monkeypatch):
        calls = 0
        real_tokenize = parser_module.tokenize

        def counting_tokenize(text):
            nonlocal calls
            calls += 1
            return real_tokenize(text)

        monkeypatch.setattr(parser_module, 'tokenize', counting_tokenize)
        cache: ExprCache = {}
        for _ in range(500):
            with pytest.raises(RamlError):
                parse_expression('also | (broken', cache)
        assert calls == 1

    def test_a_fresh_registry_starts_with_an_empty_cache(self):
        assert Raml().expr_cache == {}


class TestTokenizer:
    def test_skips_whitespace_and_tags_columns(self):
        tokens = tokenize('Foo | Bar')
        assert tokens == [
            Token(TokenKind.IDENT, 'Foo', 0),
            Token(TokenKind.PIPE, '|', 4),
            Token(TokenKind.IDENT, 'Bar', 6),
            Token(TokenKind.EOF, '', 9),
        ]

    def test_array_notation_is_a_single_token(self):
        tokens = tokenize('T[]')
        assert [t.kind for t in tokens] == [TokenKind.IDENT, TokenKind.ARRAY, TokenKind.EOF]


class TestAstShape:
    # Pins the exact field set from docs/06-type-expressions.md section 2.2:
    # only `Primitive` and `Reference` carry a `col`. This is a documented
    # decision worth protecting from a "make it consistent" refactor -- see
    # the discrepancy noted in this feature's commit message / report.
    def test_only_primitive_and_reference_carry_a_column(self):
        assert [f.name for f in dataclasses.fields(Primitive)] == ['name', 'col']
        assert [f.name for f in dataclasses.fields(Reference)] == ['name', 'col']
        assert [f.name for f in dataclasses.fields(Array)] == ['item']
        assert [f.name for f in dataclasses.fields(Optional_)] == ['inner']
        assert [f.name for f in dataclasses.fields(Union)] == ['members']

    def test_ast_nodes_are_frozen(self):
        node = Primitive('string', 0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            node.name = 'integer'
