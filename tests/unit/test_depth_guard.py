"""One ceiling, four recursions, and never a `RecursionError`.

docs/12-performance.md section 14. `ParseOptions.max_depth` governs every
descent whose depth is bounded only by the input — document conversion in P0,
unwrap and recursion-marking in P9, and the JSON Schema walks — because they all
defend the same C stack. These live in one file rather than beside each pass
because the point being pinned is that they are *one* rule.

Each guard has its own message key, so a document that trips one says which.
"""

from __future__ import annotations

import json

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path
from pyraml.yamlnode import DEFAULT_MAX_DEPTH

LIB = '#%RAML 1.0 Library\n'


def messages(error: RamlError) -> list[str]:
    return [trace.message for chain in error.chains() for trace in chain]


def deep_graph(depth: int) -> str:
    """A **flat** document whose type graph is `depth` levels deep.

    Flat on purpose. Nesting the declarations inline would trip the document
    guard first — an inline type level costs at least two YAML levels — so the
    only way to reach the type guard is to spell the chain out as sibling
    declarations that name each other.
    """
    body = '  T0: string\n'
    for level in range(1, depth):
        body += f'  T{level}:\n    type: object\n    properties:\n      p: T{level - 1}\n'
    return LIB + 'types:\n' + body


def deep_document(depth: int) -> str:
    body = '  T:\n'
    for level in range(depth):
        pad = '  ' * level
        body += f'{pad}    properties:\n{pad}      a:\n'
    return LIB + 'types:\n' + body + '  ' * depth + '        type: string\n'


def deep_schema(depth: int) -> str:
    node: dict = {'type': 'string'}
    for _ in range(depth):
        node = {'type': 'object', 'properties': {'p': node}}
    return json.dumps(node)


class TestEachGuardNamesItself:
    def test_a_deep_type_graph_is_a_positioned_diagnostic(self, workspace):
        root = workspace({'lib.raml': deep_graph(40)})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=10))
        assert 'type nesting too deep' in messages(caught.value)

    def test_a_deep_document_is_a_positioned_diagnostic(self, workspace):
        root = workspace({'lib.raml': deep_document(40)})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(max_depth=10))
        assert 'document nesting too deep' in messages(caught.value)

    def test_a_deep_json_schema_is_a_positioned_diagnostic(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.json\n', 's.json': deep_schema(40)})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(max_depth=10))
        assert 'JSON schema nesting too deep' in messages(caught.value)

    def test_the_limit_travels_in_the_diagnostic(self, workspace):
        """A caller who raises the ceiling has to be able to see what it was."""
        root = workspace({'lib.raml': deep_graph(40)})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=17))
        limits = [trace.info.get('limit') for chain in caught.value.chains() for trace in chain]
        assert 17 in limits


class TestOneOptionGovernsThemAll:
    """The reconciliation itself: raising the option raises every ceiling."""

    @pytest.mark.parametrize(
        ('name', 'files'),
        [
            ('graph', {'lib.raml': deep_graph(40)}),
            ('document', {'lib.raml': deep_document(40)}),
            ('schema', {'lib.raml': LIB + 'types:\n  S: !include s.json\n', 's.json': deep_schema(40)}),
        ],
    )
    def test_raising_the_option_admits_what_the_default_would_refuse(self, workspace, name, files):
        root = workspace(files)
        with pytest.raises(RamlError):
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=10))
        # Same input, same passes, one number changed.
        parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=500))


class TestNoRecursionErrorEscapes:
    """The invariant behind the ceiling, stated as CLAUDE.md and docs/12 state it.

    A 200-level JSON Schema used to exhaust CPython's stack inside the schema
    library's own meta-schema validation, which is not a recursion pyRAML can
    guard from the inside. It is bounded by measuring the decoded document
    before anything walks it.
    """

    @pytest.mark.parametrize('depth', [DEFAULT_MAX_DEPTH, DEFAULT_MAX_DEPTH * 3])
    def test_a_schema_past_the_default_ceiling_raises_ramlerror(self, workspace, depth):
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.json\n', 's.json': deep_schema(depth)})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
        assert 'JSON schema nesting too deep' in messages(caught.value)

    def test_a_ref_to_a_deep_schema_is_bounded_too(self, workspace):
        """A shallow schema must not be able to reach the stack through a `$ref`."""
        root = workspace(
            {
                'lib.raml': LIB + 'types:\n  S: !include s.json\n',
                's.json': json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'deep.json'}}}),
                'deep.json': deep_schema(DEFAULT_MAX_DEPTH * 2),
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
        assert 'JSON schema nesting too deep' in messages(caught.value)

    def test_many_references_are_not_deep_references(self, workspace):
        """`seen` counts documents; depth counts levels. Conflating them was a bug.

        A schema naming three hundred distinct `$ref` targets is ordinary, and
        nests two levels. An early draft of the guard compared the size of the
        visited set against the ceiling and rejected exactly this.
        """
        count = DEFAULT_MAX_DEPTH + 100
        files = {
            'lib.raml': LIB + 'types:\n  S: !include s.json\n',
            's.json': json.dumps(
                {
                    'type': 'object',
                    'properties': {f'p{index}': {'$ref': f'd{index}.json'} for index in range(count)},
                }
            ),
        }
        for index in range(count):
            files[f'd{index}.json'] = json.dumps({'type': 'string'})
        root = workspace(files)
        parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
