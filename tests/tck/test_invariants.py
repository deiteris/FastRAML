"""Invariants checked over the whole corpus rather than over one document.

A unit test pins an invariant on an example someone chose. These run the same
checks over every fixture the parser accepts, which is where the case nobody
thought of lives. See docs/02-architecture.md section 4.
"""

from __future__ import annotations

import pytest

from tests.tck.conftest import collect_fixtures, fixture_id, tck_root

pytestmark = pytest.mark.tck


def _parsed_registries(limit: int | None = None):
    """Every valid fixture that parses, as `(id, Raml)` pairs.

    A fixture the parser rejects is skipped: these tests are about the shape of
    a successful parse, and the ratchet already tracks which fixtures fail.
    """
    from pyraml import ParseOptions, RamlError, parse_from_path

    root = tck_root()
    if root is None:
        return
    for count, path in enumerate(collect_fixtures('valid')):
        if limit is not None and count >= limit:
            return
        try:
            yield fixture_id(root, path), parse_from_path(path, ParseOptions())
        except (RamlError, OSError):
            continue


@pytest.fixture(scope='module')
def corpus() -> list:
    if tck_root() is None:
        pytest.skip('no TCK corpus; set PYRAML_TCK_DIR')
    return list(_parsed_registries())


class TestI4:
    """Every `BaseShape` is in `shapes`, and in `unresolved_shapes` iff unknown.

    P7 drains `unresolved_shapes` and swaps in the real kind. A shape missing
    from the worklist would stay an `UnknownShape` forever and violate I5; a
    resolved shape wrongly on it would be re-resolved.
    """

    def test_the_worklist_holds_exactly_the_unknown_shapes(self, corpus: list):
        from pyraml.types.complex_ import UnknownShape

        assert corpus, 'no fixture parsed; the check would be vacuous'
        offenders: list[str] = []
        for name, raml in corpus:
            unresolved = {id(shape) for shape in raml.unresolved_shapes}
            for shape in raml.shapes:
                unknown = isinstance(shape.shape, UnknownShape)
                if unknown != (id(shape) in unresolved):
                    offenders.append(f'{name}: shape {shape.id} ({shape.name!r}) type={shape.type!r} unknown={unknown}')
        assert not offenders, '\n'.join(offenders[:20])

    def test_every_shape_has_a_kind_attached(self, corpus: list):
        # `BaseShape.shape` is None only between construction and dispatch,
        # which is inside `make_shape`. Nothing outside should ever see it.
        offenders = [
            f'{name}: shape {shape.id} ({shape.name!r})'
            for name, raml in corpus
            for shape in raml.shapes
            if shape.shape is None
        ]
        assert not offenders, '\n'.join(offenders[:20])


def _reachable(raml):
    """Every shape reachable from the model's roots, by explicit stack.

    Recursion would not survive a self-referential type — `Node.next: Node` is
    legal and produces a cycle in the object graph until P9 marks it.
    """
    from pyraml.types.complex_ import ArrayShape, ObjectShape, UnionShape

    stack = [base for shapes in raml.fragment_typedefs.values() for base in shapes]
    stack += [base for declared in raml.fragment_types.values() for base in declared.values()]
    stack += [base for declared in raml.fragment_annotations.values() for base in declared.values()]

    seen: dict[int, object] = {}
    while stack:
        base = stack.pop()
        if id(base) in seen:
            continue
        seen[id(base)] = base

        stack += base.inherits
        stack += [prop.base for prop in base.custom_facet_defs.values()]
        if base.alias is not None:
            stack.append(base.alias)
        if base.link is not None and base.link.shape is not None:
            stack.append(base.link.shape)

        shape = base.shape
        if isinstance(shape, ArrayShape) and shape.items is not None:
            stack.append(shape.items)
        elif isinstance(shape, UnionShape) and shape.any_of is not None:
            stack += shape.any_of
        elif isinstance(shape, ObjectShape):
            stack += [prop.base for prop in (shape.properties or {}).values()]
            stack += [prop.base for prop in (shape.pattern_properties or {}).values()]
    return seen


class TestI5:
    """After P7 no reachable shape is an `UnknownShape`.

    The strongest form is asserted first: on a parse that succeeded, *no* shape
    the registry created is still unknown, reachable or not. The walk then ties
    I5 back to I4 — a shape reachable through the model but absent from
    `raml.shapes` would escape every other check in this file.
    """

    def test_nothing_is_still_unknown_after_a_parse(self, corpus: list):
        from pyraml.types.complex_ import UnknownShape

        assert corpus, 'no fixture parsed; the check would be vacuous'
        offenders = [
            f'{name}: shape {shape.id} ({shape.name!r}) type={shape.type!r}'
            for name, raml in corpus
            for shape in raml.shapes
            if isinstance(shape.shape, UnknownShape)
        ]
        assert not offenders, '\n'.join(offenders[:20])

    def test_the_worklist_is_drained(self, corpus: list):
        offenders = [f'{name}: {len(raml.unresolved_shapes)} left' for name, raml in corpus if raml.unresolved_shapes]
        assert not offenders, '\n'.join(offenders[:20])

    def test_every_reachable_shape_was_registered(self, corpus: list):
        offenders: list[str] = []
        reached = 0
        for name, raml in corpus:
            registered = {id(shape) for shape in raml.shapes}
            found = _reachable(raml)
            reached += len(found)
            offenders += [
                f'{name}: shape {base.id} ({base.name!r}) is reachable but not in raml.shapes'
                for key, base in found.items()
                if key not in registered
            ]
        assert not offenders, '\n'.join(offenders[:20])
        assert reached > 0, 'the walk found nothing; it would be vacuous'


@pytest.fixture(scope='module')
def unwrapped_corpus() -> list:
    """The same fixtures, parsed with `unwrap=True`.

    A separate fixture on purpose: I4 and I5 are about the model *before*
    flattening, so the plain `corpus` must stay un-unwrapped.
    """
    from pyraml import ParseOptions, RamlError, parse_from_path

    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set PYRAML_TCK_DIR')
    parsed = []
    for path in collect_fixtures('valid'):
        try:
            parsed.append((fixture_id(root, path), parse_from_path(path, ParseOptions(unwrap=True))))
        except (RamlError, OSError):
            continue
    return parsed


class TestI6:
    """After P9 every reachable shape has `_unwrapped` set and `link` cleared."""

    def test_every_shape_is_flattened_and_unlinked(self, unwrapped_corpus: list):
        assert unwrapped_corpus, 'no fixture parsed; the check would be vacuous'
        offenders: list[str] = []
        for name, raml in unwrapped_corpus:
            if not raml.is_unwrapped:
                offenders.append(f'{name}: registry not marked unwrapped')
            offenders += [
                f'{name}: shape {shape.id} ({shape.name!r}) unwrapped={shape._unwrapped} link={shape.link}'
                for shape in raml.shapes
                if not shape._unwrapped or shape.link is not None
            ]
        assert not offenders, '\n'.join(offenders[:20])

    def test_the_reachable_graph_is_finite(self, unwrapped_corpus: list):
        # Recursion marking turned every cycle into a back-edge, so the walk
        # from § I5 terminates without its own visited set doing the work.
        from pyraml.types.complex_ import RecursiveShape

        heads_without_a_head = [
            f'{name}: shape {base.id}'
            for name, raml in unwrapped_corpus
            for base in _reachable(raml).values()
            if isinstance(base.shape, RecursiveShape) and base.shape.head is None
        ]
        assert not heads_without_a_head, '\n'.join(heads_without_a_head[:20])


class TestI1:
    """Every `location` is a `file://` or `http(s)://` URI, never an OS path."""

    def test_every_shape_location_is_a_uri(self, corpus: list):
        offenders = [
            f'{name}: {shape.location!r}'
            for name, raml in corpus
            for shape in raml.shapes
            if not shape.location.startswith(('file://', 'http://', 'https://'))
        ]
        assert not offenders, '\n'.join(offenders[:20])


class TestDeclarationOrder:
    """Declaration order is preserved everywhere the model is exposed."""

    def test_a_types_map_keeps_its_written_order(self, corpus: list):
        # `types_in` reads the per-file index; it must agree with the order the
        # shapes were created in, which is the order they were written.
        offenders: list[str] = []
        for name, raml in corpus:
            for uri, declared in raml.fragment_types.items():
                created = [shape.name for shape in raml.shapes if shape.name in declared]
                first_seen = list(dict.fromkeys(created))
                if first_seen != list(declared):
                    offenders.append(f'{name}: {uri} declared={list(declared)} created={first_seen}')
        assert not offenders, '\n'.join(offenders[:10])
