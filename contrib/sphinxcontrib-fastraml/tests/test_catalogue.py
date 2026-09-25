"""What is addressable, and the one key each spelling of a name comes to."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sphinxcontrib.fastraml import apis

from .conftest import FIXTURES, ROOT, SAMPLE

if TYPE_CHECKING:
    from sphinxcontrib.fastraml.catalogue import Catalogue


@pytest.fixture(scope='module')
def catalogue() -> Catalogue:
    loaded = apis._load(apis.Source('books', SAMPLE, FIXTURES))
    assert loaded is not None
    return loaded.catalogue


def test_the_root_file_is_keyed_as_the_tree_keys_files(catalogue):
    # Relative to the workspace root, so a bare name finds the root file's.
    assert catalogue.root_file == ROOT
    assert f'{ROOT}#Book' in catalogue.declared('type')


def test_the_root_file_defaults_to_its_own_name(tmp_path):
    # Without a workspace root the entry's directory is the root (docs/13 § 1).
    (tmp_path / 'api.raml').write_text('#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n', encoding='utf-8')
    loaded = apis._load(apis.Source('t', tmp_path / 'api.raml', None))
    assert loaded is not None
    assert loaded.catalogue.root_file == 'api.raml'
    assert loaded.catalogue.normalise('type', 'A') == 'api.raml#A'


@pytest.mark.parametrize(
    ('kind', 'written', 'key'),
    [
        ('method', 'get /books/{isbn}', 'GET /books/{isbn}'),
        ('method', 'GET   /books', 'GET /books'),
        ('response', 'get /books 200', 'GET /books 200'),
        ('type', 'Book', f'{ROOT}#Book'),
        ('type', 'sample/common.raml#Page', 'sample/common.raml#Page'),
        ('property', 'Book.isbn', f'{ROOT}#Book.isbn'),
        ('property', 'shared/measures.raml#Parcel.weight', 'shared/measures.raml#Parcel.weight'),
        ('base-uri-parameter', '{tenant}', 'tenant'),
        ('documentation-item', 'Getting  started', 'Getting started'),
    ],
)
def test_every_spelling_comes_to_one_key(catalogue, kind, written, key):
    assert catalogue.normalise(kind, written) == key


@pytest.mark.parametrize(
    ('kind', 'written'),
    [('method', 'FETCH /books'), ('method', 'GET books'), ('endpoint', 'books'), ('property', 'Book')],
)
def test_what_is_not_a_name_of_its_kind_has_no_key(catalogue, kind, written):
    assert catalogue.normalise(kind, written) is None


def test_a_library_is_reached_by_file_and_never_by_its_uses_key(catalogue):
    # `common.Page` is the root file's private spelling of `common.raml#Page`.
    assert catalogue.exists('type', 'sample/common.raml#Page')
    assert not catalogue.exists('type', catalogue.normalise('type', 'common.Page') or '')


def test_depth_counts_path_segments_as_the_viewer_nests_them(catalogue):
    assert catalogue.under('/books', 0) == ['/books']
    assert catalogue.under('/books', 1) == ['/books', '/books/{isbn}']
    assert catalogue.under('/books', -1) == ['/books', '/books/{isbn}']
    # A prefix of a segment is not a parent: `/book` is not over `/books`.
    assert catalogue.under('/book', -1) == []


def test_a_complete_reference_is_the_root_namespace(catalogue):
    addressable = set(catalogue.addressable())
    assert ('method', 'GET /books/{isbn}') in addressable
    assert ('type', f'{ROOT}#Book') in addressable
    # A security scheme included as a fragment is named in the root file.
    assert ('security-scheme', f'{ROOT}#machineToken') in addressable
    # A library's declarations are rendered when linked to, not required.
    assert not any(key.startswith('sample/common.raml#') for _, key in addressable)
    # Responses and properties are parts of their method and type.
    assert not any(kind in {'response', 'property'} for kind, _ in addressable)


def test_a_repeated_documentation_title_is_no_target(tmp_path):
    (tmp_path / 'api.raml').write_text(
        '#%RAML 1.0\ntitle: T\ndocumentation:\n'
        '  - title: Same\n    content: a\n  - title: Same\n    content: b\n  - title: Other\n    content: c\n',
        encoding='utf-8',
    )
    loaded = apis._load(apis.Source('t', tmp_path / 'api.raml', None))
    assert loaded is not None
    assert loaded.catalogue.duplicate_titles() == {'Same'}
    assert loaded.catalogue.documentation_item('Same') is None
    assert ('documentation-item', 'Same') not in set(loaded.catalogue.addressable())
