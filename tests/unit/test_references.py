"""Name resolution rules. See docs/04-fragments-and-namespaces.md § 3."""

from __future__ import annotations

import pytest

from fastraml import Library, parse_from_path
from fastraml.parser.references import (
    UnresolvedReferenceError,
    cut_last,
    resolve_library_reference,
    resolve_reference,
)
from fastraml.registry import Raml


def library_with(raml: Raml, location: str, **tables) -> Library:
    library = Library(raml, location)
    for name, table in tables.items():
        setattr(library, name, table)
    return library


def linked_uses(raml: Raml, **libraries):
    from fastraml.parser.fragments import LibraryLink
    from fastraml.positions import UNKNOWN

    uses = {}
    for prefix, library in libraries.items():
        link = LibraryLink(raml.next_id(), f'{prefix}.raml', 'file:///a.raml', UNKNOWN, UNKNOWN)
        link.link = library
        uses[prefix] = link
    return uses


def pick_type(library: Library, name: str):
    return library.types.get(name)


class TestCutLast:
    @pytest.mark.parametrize(
        ('name', 'expected'),
        [
            ('Type', ('', 'Type', False)),
            ('lib.Type', ('lib', 'Type', True)),
            # RAML type names may contain dots, so the *last* dot separates.
            ('a.b.c', ('a.b', 'c', True)),
        ],
    )
    def test_the_split_is_on_the_last_separator(self, name: str, expected: tuple[str, str, bool]):
        assert cut_last(name, '.') == expected


class TestResolveReference:
    def test_an_unqualified_name_comes_from_the_local_table(self):
        assert resolve_reference({'User': 'shape'}, None, 'User', pick_type) == 'shape'

    def test_a_missing_local_name_is_an_error_naming_it(self):
        with pytest.raises(UnresolvedReferenceError) as caught:
            resolve_reference({}, None, 'User', pick_type)
        assert (caught.value.reason, caught.value.name) == ('reference not found', 'User')

    def test_a_qualified_name_comes_from_the_linked_library(self):
        raml = Raml()
        library = library_with(raml, 'file:///lib.raml', types={'User': 'shape'})
        assert resolve_reference({}, linked_uses(raml, lib=library), 'lib.User', pick_type) == 'shape'

    def test_a_local_key_containing_a_dot_wins_over_the_library_hop(self):
        raml = Raml()
        library = library_with(raml, 'file:///lib.raml', types={'User': 'from library'})
        local = {'lib.User': 'declared locally'}
        assert resolve_reference(local, linked_uses(raml, lib=library), 'lib.User', pick_type) == 'declared locally'

    def test_namespace_chaining_is_refused(self):
        # Spec: processors MUST NOT allow composition of namespaces across
        # libraries. `files.file-type` is simply not a key in uses.
        raml = Raml()
        inner = library_with(raml, 'file:///inner.raml', types={'File': 'shape'})
        outer = library_with(raml, 'file:///outer.raml', uses=linked_uses(raml, **{'file-type': inner}))
        uses = linked_uses(raml, files=outer)
        with pytest.raises(UnresolvedReferenceError) as caught:
            resolve_reference({}, uses, 'files.file-type.File', pick_type)
        assert (caught.value.reason, caught.value.name) == ('library not found', 'files.file-type')

    def test_an_unlinked_library_is_distinguished_from_a_missing_one(self):
        from fastraml.parser.fragments import LibraryLink
        from fastraml.positions import UNKNOWN

        uses = {'lib': LibraryLink(1, 'lib.raml', 'file:///a.raml', UNKNOWN, UNKNOWN)}
        with pytest.raises(UnresolvedReferenceError) as caught:
            resolve_reference({}, uses, 'lib.User', pick_type)
        assert caught.value.reason == 'library not resolved'


class TestResolveLibraryReference:
    def test_an_unqualified_name_cannot_resolve(self):
        # This is what makes a typed fragment self-contained: it never sees its
        # includer's namespace (docs/01 § 4.3).
        with pytest.raises(UnresolvedReferenceError) as caught:
            resolve_library_reference({}, 'User', pick_type)
        assert caught.value.reason == 'invalid reference'

    def test_a_qualified_name_resolves_through_uses(self):
        raml = Raml()
        library = library_with(raml, 'file:///lib.raml', types={'User': 'shape'})
        assert resolve_library_reference(linked_uses(raml, lib=library), 'lib.User', pick_type) == 'shape'


class TestAnnotationTypeFallback:
    def test_an_annotation_type_reference_falls_back_to_types(self, workspace):
        # `annotationTypes: {ConfigInstance: Config}` must find Config among the
        # types of the library it was imported from. docs/04 § 3.
        root = workspace(
            {'api.raml': '#%RAML 1.0\ntitle: T\nuses:\n  l: lib.raml\n', 'lib.raml': '#%RAML 1.0 Library\n'}
        )
        raml = parse_from_path(root / 'api.raml')
        api = raml.entry_point
        library = api.uses['l'].link
        library.types['Config'] = 'the data type'

        assert api.reference_annotation_type('l.Config') == 'the data type'

        library.annotation_types['Config'] = 'the annotation type'
        assert api.reference_annotation_type('l.Config') == 'the annotation type'

    def test_the_fallback_also_applies_to_a_uses_only_fragment(self, workspace):
        root = workspace(
            {
                'dt.raml': '#%RAML 1.0 DataType\nuses:\n  l: lib.raml\ntype: string\n',
                'lib.raml': '#%RAML 1.0 Library\n',
            }
        )
        fragment = parse_from_path(root / 'dt.raml').entry_point
        fragment.uses['l'].link.types['Config'] = 'the data type'
        assert fragment.reference_annotation_type('l.Config') == 'the data type'
