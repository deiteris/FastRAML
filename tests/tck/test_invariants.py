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
    from fastraml import ParseOptions, RamlError, parse_from_path

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
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
    return list(_parsed_registries())


class TestI4:
    """Every `BaseShape` is in `shapes`, and in `unresolved_shapes` iff unknown.

    P7 drains `unresolved_shapes` and swaps in the real kind. A shape missing
    from the worklist would stay an `UnknownShape` forever and violate I5; a
    resolved shape wrongly on it would be re-resolved.
    """

    def test_the_worklist_holds_exactly_the_unknown_shapes(self, corpus: list):
        from fastraml.types.complex_ import UnknownShape

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
    from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape

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
        from fastraml.types.complex_ import UnknownShape

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
    from fastraml import ParseOptions, RamlError, parse_from_path

    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
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
        from fastraml.types.complex_ import RecursiveShape

        heads_without_a_head = [
            f'{name}: shape {base.id}'
            for name, raml in unwrapped_corpus
            for base in _reachable(raml).values()
            if isinstance(base.shape, RecursiveShape) and base.shape.head is None
        ]
        assert not heads_without_a_head, '\n'.join(heads_without_a_head[:20])


def _endpoint_shapes(raml):  # noqa: PLR0912 - one branch per place a shape can hang off an endpoint
    """Every shape an endpoint decode created, with the endpoint that holds it."""
    stack = list(raml.endpoints.values())
    seen: set[int] = set()
    while stack:
        endpoint = stack.pop()
        if id(endpoint) in seen:
            continue
        seen.add(id(endpoint))
        stack += endpoint.endpoints.values()

        for prop in endpoint.uri_parameters.values():
            yield endpoint, prop.base
        for operation in endpoint.operations.values():
            request = operation.request
            if request is not None:
                for prop in (*request.headers.values(), *request.query_parameters.values()):
                    yield endpoint, prop.base
                if request.query_string is not None:
                    yield endpoint, request.query_string
                for body in request.bodies.values():
                    if body.shape is not None:
                        yield endpoint, body.shape
            for response in operation.responses.values():
                for prop in response.headers.values():
                    yield endpoint, prop.base
                for body in response.bodies.values():
                    if body.shape is not None:
                        yield endpoint, body.shape


class TestEndpointShapesAreRegistered:
    """Every shape a stage-2 decode creates is in `fragment_typedefs`.

    `unwrap_shapes` and `validate_shapes` iterate that index and nothing else,
    so a shape that skips `put_typedef` is silently never flattened and never
    validated. Nothing else in the suite would notice: the model still holds
    the shape, and reading it looks correct.
    """

    def test_every_endpoint_shape_reaches_the_later_passes(self, corpus: list):
        assert corpus, 'no fixture parsed; the check would be vacuous'
        offenders: list[str] = []
        checked = 0
        for name, raml in corpus:
            registered = {id(shape) for shapes in raml.fragment_typedefs.values() for shape in shapes}
            for endpoint, shape in _endpoint_shapes(raml):
                checked += 1
                if id(shape) not in registered:
                    offenders.append(f'{name}: {endpoint.full_uri} holds shape {shape.id} ({shape.name!r})')
        assert not offenders, '\n'.join(offenders[:20])
        assert checked > 0, 'no endpoint declared a shape; the check would be vacuous'


class TestTemplateProvenance:
    """A shape a template contributed knows which file it came from.

    The phase's characteristic failure: a type name resolved in the applying
    document's namespace instead of the template's *still parses*, and produces
    a model that looks right. What is checkable over the corpus is the two
    things that failure destroys — an anchor, and a location naming a file this
    parse actually read (docs/08 section 6.3).
    """

    def test_every_endpoint_shape_has_an_anchor(self, corpus: list):
        # A shape with none falls back to `resolver_at(location)`, which agrees
        # with the anchor only for a document that declares everything itself.
        assert corpus, 'no fixture parsed; the check would be vacuous'
        offenders: list[str] = []
        checked = 0
        for name, raml in corpus:
            for endpoint, shape in _endpoint_shapes(raml):
                checked += 1
                if shape.anchor is None:
                    offenders.append(f'{name}: {endpoint.full_uri} shape {shape.id} ({shape.name!r})')
        assert not offenders, '\n'.join(offenders[:20])
        assert checked > 0, 'no endpoint declared a shape; the check would be vacuous'

    def test_every_endpoint_shape_is_attributed_to_a_file_that_was_read(self, corpus: list):
        offenders = [
            f'{name}: {endpoint.full_uri} shape {shape.id} ({shape.name!r}) at {shape.location}'
            for name, raml in corpus
            for endpoint, shape in _endpoint_shapes(raml)
            if shape.location not in raml.fragments
        ]
        assert not offenders, '\n'.join(offenders[:20])

    def test_some_endpoint_shape_comes_from_another_file(self, corpus: list):
        # Non-vacuity for the two above: without provenance every endpoint shape
        # would be attributed to the entry point, and both would still pass.
        from_elsewhere = sum(
            1
            for _name, raml in corpus
            for _endpoint, shape in _endpoint_shapes(raml)
            if shape.location != raml.location
        )
        assert from_elsewhere > 0, 'no template contributed a shape; the checks above are vacuous'


class TestDomainExtensions:
    """P8 binds every application; P9 keeps the binding pointing at live shapes.

    The re-binding is the half a unit test cannot reach convincingly: it only
    matters when unwrap replaced the annotation type with a merged copy, which
    needs a declaration shaped a particular way. Over the corpus it is ordinary.
    """

    def test_every_application_is_bound(self, corpus: list):
        assert corpus, 'no fixture parsed; the check would be vacuous'
        bound = 0
        offenders: list[str] = []
        for name, raml in corpus:
            for extension in raml.domain_extensions:
                bound += 1
                if extension.defined_by is None:
                    offenders.append(f'{name}: ({extension.name}) at {extension.location}')
        assert not offenders, '\n'.join(offenders[:20])
        assert bound > 0, 'no fixture applied an annotation; the check would be vacuous'

    def test_a_binding_survives_unwrap(self, unwrapped_corpus: list):
        # A stale `defined_by` points at the pre-merge object, which unwrap
        # dropped when it rebuilt `raml.shapes` (docs/09 section B4).
        offenders: list[str] = []
        checked = 0
        for name, raml in unwrapped_corpus:
            live = {id(shape) for shape in raml.shapes}
            for extension in raml.domain_extensions:
                if extension.defined_by is None:
                    continue
                checked += 1
                if id(extension.defined_by) not in live:
                    offenders.append(f'{name}: ({extension.name}) bound to a shape unwrap replaced')
        assert not offenders, '\n'.join(offenders[:20])
        assert checked > 0, 'nothing was bound; the check would be vacuous'


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
