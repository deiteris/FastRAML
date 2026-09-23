"""Document provenance: which extension document wrote a target-tree node (docs/19 § 5.3).

The merge that writes these marks arrives separately; here the marks are set by
hand, and each test pins one reader. What can go wrong is quiet: a node read
under the wrong file's name keeps its own positions, so a diagnostic points at a
line of a file that does not contain it.
"""

from __future__ import annotations

import pytest

from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.errors import RamlError
from fastraml.loaders import build_loader
from fastraml.parser.annotations import unmarshal_domain_extension
from fastraml.parser.facets import make_string_facet
from fastraml.parser.includes import resolve_include
from fastraml.parser.security import make_security_scheme_definition
from fastraml.parser.source_ir import make_source_endpoint
from fastraml.parser.templates import make_template_definition
from fastraml.parser.traits import TraitDefinition
from fastraml.registry import ParseCtx, Raml
from fastraml.uris import path_to_file_uri
from fastraml.yamlnode import compose, node_error, pairs

MASTER = 'file:///api/master.raml'
EXTENSION = 'file:///api/ext/extension.raml'


class Document:
    """Stands in for an `ExtensionFragment`: readers need only its location."""

    def __init__(self, location: str) -> None:
        self.location = location


def tree(text: str, uri: str = EXTENSION):
    return compose(text, uri=uri)


def value_of(node, key: str):
    return next(value for k, value in pairs(node) if k.value == key)


class TestMarking:
    def test_a_mark_covers_the_whole_subtree(self):
        raml = Raml()
        author = Document(EXTENSION)
        root = tree('a:\n  b:\n    - c\n')
        raml.mark_authored(root, author)
        leaf = value_of(value_of(root, 'a'), 'b').content[0]
        assert raml.document_anchor(leaf) is author

    def test_an_existing_mark_is_never_replaced(self):
        # Authorship is a fact about the node: a later document that grafts the
        # same subtree again did not write it.
        raml = Raml()
        first, second = Document(EXTENSION), Document('file:///api/other.raml')
        root = tree('a: 1\n')
        raml.mark_authored(root, first)
        raml.mark_authored(root, second)
        assert raml.document_anchor(value_of(root, 'a')) is first

    def test_an_unmarked_node_has_no_author(self):
        raml = Raml()
        assert raml.document_anchor(tree('a: 1\n')) is None
        assert raml.document_location(tree('a: 1\n'), MASTER) == MASTER


class TestReaderPrecedence:
    def test_location_of_names_the_author_without_an_active_overlay(self):
        # P2 root decoding runs with no unit overlay at all.
        raml = Raml()
        node = tree('a: 1\n')
        raml.mark_authored(node, Document(EXTENSION))
        assert raml.location_of(node, MASTER) == EXTENSION

    def test_the_document_mark_beats_the_unit_mark(self):
        # Static template content an extension amended keeps its author after
        # the template is grafted and marked with the template's scope.
        raml = Raml()
        node = tree('a: 1\n')
        raml.mark_authored(node, Document(EXTENSION))
        template_scope = ParseCtx(anchor=Document('file:///api/lib.raml'))  # type: ignore[arg-type]
        with raml.active_overlay({node: template_scope}):
            assert raml.location_of(node, MASTER) == EXTENSION
            assert raml.scope_for(node).anchor.location == EXTENSION

    def test_an_unmarked_node_still_reads_the_unit_mark(self):
        raml = Raml()
        raml.mark_authored(tree('other: 1\n'), Document(EXTENSION))
        node = tree('a: 1\n')
        template_scope = ParseCtx(anchor=Document('file:///api/lib.raml'))  # type: ignore[arg-type]
        with raml.active_overlay({node: template_scope}):
            assert raml.scope_for(node) is template_scope
            assert raml.location_of(node, MASTER) == 'file:///api/lib.raml'

    def test_document_location_ignores_template_scopes(self):
        # A substituted scalar keeps the template's positions; only authorship
        # may rename its file.
        raml = Raml()
        node = tree('a: 1\n')
        template_scope = ParseCtx(anchor=Document('file:///api/lib.raml'))  # type: ignore[arg-type]
        with raml.active_overlay({node: template_scope}):
            assert raml.document_location(node, MASTER) == MASTER

    def test_a_substituted_type_value_beats_the_authored_mapping(self):
        # The caller-supplied `type:` value is the more specific scope.
        raml = Raml()
        mapping = tree('type: <<item>>\n')
        raml.mark_authored(mapping, Document(EXTENSION))
        substituted = tree('User\n')
        caller = ParseCtx(anchor=Document('file:///api/caller.raml'))  # type: ignore[arg-type]
        spliced = mapping.content[:]
        spliced[1] = substituted
        mapping.content = spliced
        with raml.active_overlay({substituted: caller}):
            assert raml.scope_for(mapping) is caller

    def test_a_document_scope_keeps_the_annotation_target(self):
        raml = Raml()
        author = Document(EXTENSION)
        node = tree('a: 1\n')
        raml.mark_authored(node, author)
        raml.push_ctx(ParseCtx(target=DomainLocation.RESPONSE))
        scope = raml.scope_for(node)
        assert scope.anchor is author
        assert scope.target is DomainLocation.RESPONSE

    def test_no_marks_and_no_overlay_is_no_scope(self):
        assert Raml().scope_for(tree('a: 1\n')) is None

    def test_a_marked_scope_pushes_only_for_a_marked_node_and_always_pops(self):
        # A decoder that raises inside a provenance scope must not leave the
        # extension's namespace behind for everything decoded after it.
        raml = Raml()
        marked, unmarked = tree('a: 1\n'), tree('b: 1\n')
        raml.mark_authored(marked, Document(EXTENSION))
        with raml.provenance_scope(unmarked):
            assert raml.current_ctx().anchor is None
        with raml.provenance_scope(marked):
            assert raml.current_ctx().anchor.location == EXTENSION

        def failing_decode() -> None:
            with raml.provenance_scope(marked):
                raise ValueError('decode failed')

        with pytest.raises(ValueError, match='decode failed'):
            failing_decode()
        assert raml.current_ctx().anchor is None


class TestDecodingSites:
    def test_a_scalar_facet_is_located_where_it_was_written(self):
        raml = Raml()
        root = tree('description: hola\n')
        raml.mark_authored(root, Document(EXTENSION))
        key, value = root.content
        assert make_string_facet(raml, key, value, MASTER).location == EXTENSION

    def test_a_data_node_is_located_where_it_was_written(self):
        raml = Raml()
        root = tree('example: {a: 1}\n')
        raml.mark_authored(root, Document(EXTENSION))
        key, value = root.content
        assert make_data_node(raml, key, value, MASTER).location == EXTENSION

    def test_an_include_resolves_relative_to_its_author(self, tmp_path):
        # Spec section Overlays and Extensions: paths resolve "relative to the
        # document from which the reference is made".
        (tmp_path / 'ext').mkdir()
        (tmp_path / 'ext' / 'content.txt').write_text('texto', encoding='utf-8')
        raml = Raml(loader=build_loader(str(tmp_path)))
        extension_uri = path_to_file_uri(tmp_path / 'ext' / 'extension.raml')
        root = tree('content: !include content.txt\n', extension_uri)
        raml.mark_authored(root, Document(extension_uri))
        target, content = resolve_include(raml, root.content[1], path_to_file_uri(tmp_path / 'master.raml'))
        assert target == path_to_file_uri(tmp_path / 'ext' / 'content.txt')
        assert content.value == 'texto'

    def test_an_annotation_resolves_in_its_authors_namespace(self):
        raml = Raml()
        author = Document(EXTENSION)
        root = tree('(monitor): 5\n')
        raml.mark_authored(root, author)
        raml.push_ctx(ParseCtx(target=DomainLocation.METHOD))
        key, value = root.content
        extension = unmarshal_domain_extension(raml, MASTER, key, value)
        assert extension.location == EXTENSION
        assert extension.anchor is author
        assert extension.target is DomainLocation.METHOD

    def test_a_trait_an_extension_declared_is_the_extensions(self):
        raml = Raml()
        author = Document(EXTENSION)
        raml.push_ctx(ParseCtx(anchor=Document(MASTER)))  # type: ignore[arg-type]
        root = tree('paged:\n  queryParameters:\n    page: integer\n')
        raml.mark_authored(root, author)
        key, value = root.content
        definition = make_template_definition(TraitDefinition, raml, key, value, MASTER, what='trait')
        assert definition.location == EXTENSION
        assert definition.anchor is author

    def test_a_security_scheme_an_extension_declared_is_the_extensions(self):
        raml = Raml()
        root = tree('basic:\n  type: Basic Authentication\n')
        raml.mark_authored(root, Document(EXTENSION))
        key, value = root.content
        assert make_security_scheme_definition(raml, key, value, MASTER).location == EXTENSION

    def test_a_resource_an_extension_added_is_the_extensions(self):
        raml = Raml()
        author = Document(EXTENSION)
        root = tree('/books:\n  get:\n')
        raml.mark_authored(root, author)
        key, value = root.content
        endpoint = make_source_endpoint(raml, key, value, MASTER)
        assert endpoint.location == EXTENSION
        assert endpoint.scope.anchor is author
        assert endpoint.operations['get'].location == EXTENSION

    def test_a_directive_an_extension_added_resolves_in_its_namespace(self):
        # `is: [paged]` added to a method the root API wrote.
        raml = Raml()
        author = Document(EXTENSION)
        api = ParseCtx(anchor=Document(MASTER))  # type: ignore[arg-type]
        root = tree('/books:\n  get:\n    is: [paged]\n', MASTER)
        get = value_of(value_of(root, '/books'), 'get')
        raml.mark_authored(value_of(get, 'is'), author)
        raml.push_ctx(api)
        key, value = root.content
        operation = make_source_endpoint(raml, key, value, MASTER).operations['get']
        assert operation.location == MASTER
        assert operation.scope is api
        assert operation.traits[0].scope.anchor is author
        assert operation.traits[0].location == EXTENSION


class TestDiagnostics:
    def test_an_error_on_a_marked_node_names_its_author(self):
        raml = Raml()
        root = tree('hi: 1\n')
        raml.mark_authored(root, Document(EXTENSION))
        with raml.authorship():
            error = node_error('unknown field', MASTER, root.content[0])
        assert isinstance(error, RamlError)
        assert error.head.location == EXTENSION

    def test_outside_a_parse_the_callers_location_stands(self):
        raml = Raml()
        root = tree('hi: 1\n')
        raml.mark_authored(root, Document(EXTENSION))
        assert node_error('unknown field', MASTER, root.content[0]).head.location == MASTER
