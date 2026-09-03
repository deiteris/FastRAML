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
