"""One ceiling, four recursions, and never a `RecursionError`.

docs/12-performance.md § 3. `ParseOptions.max_depth` governs every
descent whose depth is bounded only by the input — document conversion in P1,
unwrap and recursion-marking in P9, and the JSON Schema walks — because they all
defend the same C stack. These live in one file rather than beside each pass
because the point being pinned is that they are *one* rule.

Each guard has its own message key, so a document that trips one says which.
"""

from __future__ import annotations

import json

import pytest

from fastraml import ParseOptions, RamlError
from fastraml.yamlnode import DEFAULT_MAX_DEPTH

LIB = '#%RAML 1.0 Library\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


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


def top_down_graph(depth: int) -> str:
    """`deep_graph`, each type declared before the one it names."""
    body = ''.join(
        f'  T{level}:\n    type: object\n    properties:\n      p: T{level - 1}\n' for level in range(depth - 1, 0, -1)
    )
    return LIB + 'types:\n' + body + '  T0: string\n'


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
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=10))
        assert 'type nesting too deep' in messages(caught.value)

    def test_a_deep_document_is_a_positioned_diagnostic(self, workspace):
        root = workspace({'lib.raml': deep_document(40)})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(max_depth=10))
        assert 'document nesting too deep' in messages(caught.value)

    def test_a_deep_json_schema_is_a_positioned_diagnostic(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.json\n', 's.json': deep_schema(40)})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(max_depth=10))
        assert 'JSON schema nesting too deep' in messages(caught.value)

    @pytest.mark.parametrize('options', [{'unwrap': True}, {'unwrap': True, 'validate': True}])
    def test_the_limit_does_not_depend_on_declaration_order(self, workspace, options):
        """A named reference is one level, whichever of the two is declared first.

        Declared top-down, unwrap reaches each level through the property and
        then through the reference it names; that hop is not a level.
        """

        def too_deep(document):
            root = workspace({'lib.raml': document})
            _raml, error = workspace.lenient(root / 'lib.raml', ParseOptions(max_depth=10, **options))
            return error is not None and 'type nesting too deep' in messages(error)

        # `deep_graph(n)` is `n - 1` levels below its last declaration.
        assert [too_deep(top_down_graph(depth)) for depth in range(8, 15)] == [False] * 4 + [True] * 3
        assert [too_deep(deep_graph(depth)) for depth in range(8, 15)] == [False] * 4 + [True] * 3

    def test_the_private_copy_counts_every_edge(self, workspace):
        """docs/07 § 6: without unwrap, P10 copies each declaration, names included.

        The copy is made before anything is flattened, so a name is a frame
        like any other edge: `deep_graph(n)` is `2 * (n - 1)` edges deep, in
        either declaration order.
        """

        def too_deep(document):
            root = workspace({'lib.raml': document})
            _raml, error = workspace.lenient(root / 'lib.raml', ParseOptions(max_depth=10, validate=True))
            return error is not None and 'type graph too deep to copy' in messages(error)

        assert [too_deep(top_down_graph(depth)) for depth in range(4, 10)] == [False] * 3 + [True] * 3
        assert [too_deep(deep_graph(depth)) for depth in range(4, 10)] == [False] * 3 + [True] * 3

    def test_a_chain_of_names_unwrap_follows_is_bounded_too(self, workspace):
        """A name is not a level, but each one unwrap follows is a frame: they are counted apart.

        Eight levels, each reached through two names: `p: A0`, then `A0: T1`.
        Resolution never follows them in a row; unwrap follows sixteen.
        """
        body = ''.join(
            f'  T{level}:\n    properties:\n      p: A{level}\n  A{level}: T{level + 1}\n' for level in range(8)
        )
        root = workspace({'lib.raml': LIB + 'types:\n' + body + '  T8: string\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=10))
        assert 'type nesting too deep' in messages(caught.value)

    def test_the_limit_travels_in_the_diagnostic(self, workspace):
        """A caller who raises the ceiling has to be able to see what it was."""
        root = workspace({'lib.raml': deep_graph(40)})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=17))
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
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=10))
        # Same input, same passes, one number changed.
        workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, max_depth=500))


class TestNoRecursionErrorEscapes:
    """The invariant behind the ceiling, stated as CLAUDE.md and docs/12 state it.

    A 200-level JSON Schema used to exhaust CPython's stack inside the schema
    library's own meta-schema validation, which is not a recursion fastRAML can
    guard from the inside. It is bounded by measuring the decoded document
    before anything walks it.
    """

    @pytest.mark.parametrize('depth', [DEFAULT_MAX_DEPTH, DEFAULT_MAX_DEPTH * 3])
    def test_a_schema_past_the_default_ceiling_raises_ramlerror(self, workspace, depth):
        root = workspace({'lib.raml': LIB + 'types:\n  S: !include s.json\n', 's.json': deep_schema(depth)})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
        assert 'JSON schema nesting too deep' in messages(caught.value)

    def test_a_chain_of_names_resolution_follows_raises_ramlerror(self, workspace):
        """P7 resolves a referent out of queue order, so a chain declared top-down recurses.

        The chain is long enough to exhaust the stack unguarded. The ceiling is
        low because every declaration past it fails on its own path, which
        costs a walk to the ceiling each.
        """
        depth = DEFAULT_MAX_DEPTH * 2
        body = ''.join(f'  T{level}: T{level + 1}\n' for level in range(depth))
        root = workspace({'lib.raml': LIB + 'types:\n' + body + f'  T{depth}: string\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(max_depth=10))
        assert 'type reference chain too deep' in messages(caught.value)
        limits = [trace.info.get('limit') for chain in caught.value.chains() for trace in chain]
        assert 10 in limits

    def test_a_chain_past_the_ceiling_marks_each_shape_once(self, workspace, monkeypatch):
        """Each declaration on the chain fails, but only the first route walks to the ceiling.

        Marking every shape on every route cost the length times the ceiling
        squared: a 600-name chain took nine seconds.
        """
        from fastraml.registry import Raml

        marked = []
        original = Raml.mark

        def counting(raml, entity, error):
            marked.append(entity)
            return original(raml, entity, error)

        monkeypatch.setattr(Raml, 'mark', counting)
        depth = 400
        body = ''.join(f'  T{level}: T{level + 1}\n' for level in range(depth))
        root = workspace({'lib.raml': LIB + 'types:\n' + body + f'  T{depth}: string\n'})
        _raml, error = workspace.lenient(root / 'lib.raml', ParseOptions(max_depth=10))
        assert error is not None
        assert len(marked) == len({id(entity) for entity in marked}) <= depth

    def test_a_long_property_chain_copied_for_validation_raises_ramlerror(self, workspace):
        """Unguarded, P10's private copy exhausted the stack at about 250 levels."""
        root = workspace({'lib.raml': deep_graph(DEFAULT_MAX_DEPTH * 2)})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml', ParseOptions(validate=True))
        assert 'type graph too deep to copy' in messages(caught.value)

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
            workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
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
        workspace.parse(root / 'lib.raml', ParseOptions(unwrap=True, validate=True))
