"""The three constructs, and the envelope that says they mean what they mean."""

from __future__ import annotations

import pytest
from conftest import envelope

from raml_codegen import Tree
from raml_codegen.reader import UnreadableTree, is_recursion, is_ref


class TestTheEnvelopeIsChecked:
    """docs/16 § 11.9: a consumer rejects a representation it does not know.

    Emitting the envelope is only worth it if something refuses on it, and
    nothing did until this. A tree from a future version may have renamed a
    field this package reads, and reading it anyway generates a client that
    compiles and is wrong.
    """

    def test_a_tree_is_read(self):
        assert Tree.of(envelope()).document['base'] == 'fastraml://id'

    @pytest.mark.parametrize(
        'broken',
        [
            {'format': 'openapi'},
            {'format_version': 2},
            {'view': 'declared'},
        ],
    )
    def test_an_envelope_it_does_not_know_is_refused(self, broken):
        with pytest.raises(UnreadableTree):
            Tree.of(envelope(**broken))

    def test_something_that_is_not_a_document_is_refused(self):
        with pytest.raises(UnreadableTree):
            Tree.of([1, 2, 3])

    def test_the_refusal_names_what_to_do(self):
        with pytest.raises(UnreadableTree, match='regenerate'):
            Tree.of(envelope(format_version=99))


class TestTheThreeConstructs:
    def test_a_link_is_followed_once(self, tree):
        book = next(one for one in tree.types() if one.name == 'Book')
        price = book.shape['properties']['price']['type']
        assert is_ref(price)
        assert tree.resolve(price)['name'] == 'Money'

    def test_a_recursion_marker_is_not_a_link(self, tree):
        # Merging the two would break the law: a walker expanding links cannot
        # tell a repeat from a fresh subtree (docs/16 § 11.7).
        chain = next(one for one in tree.types() if one.name == 'Chain')
        marker = chain.shape['properties']['next']['type']
        assert is_recursion(marker)
        assert not is_ref(marker)
        assert tree.resolve(marker) is marker

    def test_a_recursion_marker_points_at_a_resolvable_head(self, tree):
        chain = next(one for one in tree.types() if one.name == 'Chain')
        marker = chain.shape['properties']['next']['type']
        assert tree.at(marker['head']['$ref'])['name'] == 'Chain'

    def test_containment_is_descended_without_an_ancestor_set(self, tree):
        # The index is built by a walk with no `seen` set and no depth budget,
        # over a document that contains a cycle. That it terminates is the law.
        assert tree.at('fastraml://id#/declarations/types/Book') is not None


class TestASchemaTypeIsNotALeaf:
    """docs/16 § 11.10: a consumer reads the `projection` as the type."""

    def test_a_json_shape_reads_as_its_projection(self, tree):
        invoice = next(one for one in tree.types() if one.name == 'Invoice')
        assert invoice.shape['type'] == 'json'
        assert tree.content_of(invoice.shape)['type'] == 'object'
        assert 'number' in tree.content_of(invoice.shape)['properties']

    def test_an_ordinary_shape_is_its_own_content(self, tree):
        book = next(one for one in tree.types() if one.name == 'Book')
        assert tree.content_of(book.shape) is book.shape


class TestWhatTheDocumentHolds:
    def test_declarations_arrive_in_declaration_order(self, tree):
        names = [one.name for one in tree.types() if one.file.endswith('api.raml')]
        assert names[:4] == ['Money', 'Entity', 'Isbn', 'Book']

    def test_a_declaration_carries_the_file_it_was_written_in(self, tree):
        address = next(one for one in tree.types() if one.name == 'Address')
        assert address.file == 'sample/common.raml'

    def test_an_alias_declaration_resolves_to_its_referent(self, tree):
        # `AnythingAlias: Anything` arrives as a bare link, because an alias
        # never reaches the output as a node (docs/16 § 11.8).
        alias = next(one for one in tree.types() if one.name == 'AnythingAlias')
        assert alias.shape['name'] == 'Anything'

    def test_security_schemes_arrive_with_their_settings(self, tree):
        schemes = dict(tree.security_schemes())
        assert schemes['oauth2']['type'] == 'OAuth 2.0'
