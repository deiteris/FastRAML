"""What an editor asks of a snapshot (docs/21 § 4)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from fastraml import Stage
from fastraml.service import outline, queries
from fastraml.service.queries import SymbolKind
from fastraml.service.workspace import Workspace
from fastraml.uris import path_to_file_uri
from fastraml.views import authored
from tests.unit.test_lenient import TestTheModelSaysHowFarItGot as _Lenient

if TYPE_CHECKING:
    from fastraml.service.workspace import Snapshot
    from tests.unit.conftest import MemoryWorkspace

API = """#%RAML 1.0
title: Demo
uses:
  lib: lib.raml
types:
  Entity:
    properties:
      id: string
  Book:
    type: Entity
    properties:
      author: lib.Person
      cover: !include cover.raml
traits:
  paged:
    queryParameters:
      limit: <<max>>
  spare:
    description: Never applied.
resourceTypes:
  collection:
    get:
      description: Lists them.
/books:
  type: collection
  is: [{paged: {max: integer}}]
  post:
    body:
      application/json:
        type: Book
"""

LIBRARY = """#%RAML 1.0 Library
types:
  Person:
    properties:
      name: string
"""

COVER = '#%RAML 1.0 DataType\ntype: file\n'


@pytest.fixture
def parsed(memory_workspace: MemoryWorkspace) -> tuple[Snapshot, str]:
    workspace, folder = _buffered(memory_workspace, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
    snapshot = workspace.snapshot(f'{folder}/api.raml')
    return snapshot, folder


def _where(text: str, needle: str, nth: int = 0) -> tuple[int, int]:
    """The 1-based line and column of the `nth` `needle` in `text`."""
    offset = -1
    for _ in range(nth + 1):
        offset = text.index(needle, offset + 1)
    return text.count('\n', 0, offset) + 1, offset - text.rfind('\n', 0, offset)


def _tree(symbols: list[queries.Symbol]) -> list[object]:
    """Each symbol's name, kind and detail, and its children's, if any."""
    return [
        (s.name, s.kind, s.detail, _tree(s.children)) if s.children else (s.name, s.kind, s.detail) for s in symbols
    ]


def _buffered(memory_workspace: MemoryWorkspace, files: dict[str, str]) -> tuple[Workspace, str]:
    """A service workspace over open buffers only, under a folder that does
    not exist: nothing is written, and discovery counts unsaved buffers.
    """
    folder = path_to_file_uri(memory_workspace.root)
    workspace = Workspace([folder])
    for name, text in files.items():
        workspace.open(f'{folder}/{name}', text, 1)
    return workspace, folder


def _starts(sites: list[queries.Site]) -> list[tuple[str, int, int]]:
    return [(site.uri.rsplit('/', 1)[-1], site.span.line, site.span.column) for site in sites]


class TestNames:
    def test_a_type_name_goes_to_its_declaration(self, parsed):
        snapshot, folder = parsed
        found = queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n', 0))
        assert _starts(found) == [('api.raml', *_where(API, 'Book:'))]

    def test_a_qualified_name_goes_to_the_library_and_its_prefix_to_the_uses_entry(self, parsed):
        snapshot, folder = parsed
        line, column = _where(API, 'lib.Person')
        assert _starts(queries.definition(snapshot, f'{folder}/api.raml', line, column)) == [
            ('api.raml', *_where(API, 'lib:'))
        ]
        found = queries.definition(snapshot, f'{folder}/api.raml', line, column + len('lib.'))
        assert _starts(found) == [('lib.raml', *_where(LIBRARY, 'Person'))]

    def test_a_path_goes_to_the_file(self, parsed):
        snapshot, folder = parsed
        found = queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'cover.raml'))
        assert _starts(found) == [('cover.raml', 1, 1)]

    def test_references_list_every_use_and_optionally_the_declaration(self, parsed):
        snapshot, folder = parsed
        at = _where(API, 'Entity:')
        uses = [('api.raml', *_where(API, 'Entity\n'))]
        assert _starts(queries.references(snapshot, f'{folder}/api.raml', *at, declaration=False)) == uses
        assert _starts(queries.references(snapshot, f'{folder}/api.raml', *at)) == sorted([('api.raml', *at), *uses])

    def test_highlights_mark_the_definition(self, parsed):
        snapshot, folder = parsed
        found = queries.highlights(snapshot, f'{folder}/api.raml', *_where(API, 'Entity\n'))
        assert sorted((span.line, is_definition) for span, is_definition in found) == [
            (_where(API, 'Entity:')[0], True),
            (_where(API, 'Entity\n')[0], False),
        ]

    def test_nothing_is_under_a_cursor_on_no_name(self, parsed):
        snapshot, folder = parsed
        assert queries.definition(snapshot, f'{folder}/api.raml', *_where(API, 'title')) == []

    def test_visible_names_obey_local_precedence_and_one_library_hop(self, memory_workspace):
        api = '#%RAML 1.0\ntitle: T\nuses:\n  lib: lib.raml\ntypes:\n  lib.Person: string\n'
        library = '#%RAML 1.0 Library\nuses:\n  nested: other.raml\ntypes:\n  Person: string\n  Other: integer\n'
        workspace, folder = _buffered(
            memory_workspace,
            {
                'api.raml': api,
                'lib.raml': library,
                'other.raml': '#%RAML 1.0 Library\ntypes:\n  Hidden: boolean\n',
            },
        )
        uri = f'{folder}/api.raml'
        found = queries.visible_names(workspace.snapshot(uri), uri, 'types')
        assert [(each.name, each.uri.rsplit('/', 1)[-1]) for each in found] == [
            ('lib.Person', 'api.raml'),
            ('lib.Other', 'lib.raml'),
        ]

    def test_extension_visible_names_exclude_declarations_from_later_chain_positions(self, memory_workspace):
        api = '#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n'
        extension = '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  B: string\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': api, 'ext.raml': extension})
        snapshot = workspace.snapshot(f'{folder}/ext.raml')
        assert [each.name for each in queries.visible_names(snapshot, f'{folder}/api.raml', 'types')] == ['A']
        assert [each.name for each in queries.visible_names(snapshot, f'{folder}/ext.raml', 'types')] == ['A', 'B']


class TestHover:
    def test_a_type_describes_its_identity_without_an_effective_dump(self, parsed):
        snapshot, folder = parsed
        text, span = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n'))
        assert 'data type' in text
        assert 'Specializes `Entity`' in text
        assert 'Effective summary' not in text
        assert (span.line, span.column) == _where(API, 'Book\n')

    def test_a_trait_shows_its_parameters(self, parsed):
        snapshot, folder = parsed
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'paged: {'))
        assert '`max`' in text

    def test_an_included_declaration_shows_what_its_file_holds(self, memory_workspace):
        # A declaration written `!include` holds its body in the file it
        # names: read through `getattr`, its parameters and its type were none.
        document = (
            '#%RAML 1.0\ntitle: T\n'
            'traits:\n  paged: !include paged.raml\n'
            'securitySchemes:\n  basic: !include basic.raml\n'
            '/a:\n  get:\n    is: [{paged: {max: 1}}]\n    securedBy: [basic]\n'
        )
        files = {
            'api.raml': document,
            'paged.raml': '#%RAML 1.0 Trait\nqueryParameters:\n  limit:\n    type: integer\n    maximum: <<max>>\n',
            'basic.raml': '#%RAML 1.0 SecurityScheme\ntype: Basic Authentication\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        trait, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'paged: {'))
        scheme, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(document, 'basic]'))
        assert 'parameters: `max`' in trait
        assert 'Mechanism: `Basic Authentication`' in scheme

    def test_a_built_in_says_so(self, parsed):
        snapshot, folder = parsed
        text, _ = queries.hover(snapshot, f'{folder}/api.raml', *_where(API, 'string'))
        assert 'built-in RAML type' in text
        assert 'Unicode characters' in text
        assert '`minLength`' in text


class TestSymbols:
    def test_the_outline_groups_declarations_as_the_file_does(self, parsed):
        snapshot, folder = parsed
        found = outline.document_symbols(snapshot, f'{folder}/api.raml')
        assert [(symbol.name, symbol.kind) for symbol in found] == [
            ('title', SymbolKind.METADATA),
            ('uses', SymbolKind.SECTION),
            ('types', SymbolKind.SECTION),
            ('traits', SymbolKind.SECTION),
            ('resourceTypes', SymbolKind.SECTION),
            ('/books', SymbolKind.RESOURCE),
        ]
        assert [symbol.name for symbol in found[2].children] == ['Entity', 'Book']
        # `get` came from the resource type: it is written in its declaration.
        assert [(child.name, child.detail) for child in found[-1].children] == [
            ('type', 'collection'),
            ('is', 'paged'),
            ('post', ''),
        ]

    def test_the_outline_holds_what_each_declaration_writes_and_not_what_it_inherits(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\nmediaType: [application/json, application/xml]\n'
            'annotationTypes:\n  note: string\n'
            'types:\n'
            '  Base:\n    facets:\n      unit: string\n    properties:\n      id: string\n'
            '  Item:\n    type: Base\n    unit: kg\n    (note): hi\n    properties:\n'
            '      tags:\n        type: array\n        items:\n          type: string\n'
            '      related?: Item[]\n      /^x-/: string\n'
            '    examples:\n      one:\n        id: a\n'
            '/items/{id}:\n  uriParameters:\n    id: string\n  get:\n    displayName: Read\n'
            '    headers:\n      X-Trace: string\n    queryParameters:\n      q?: string\n'
            '    responses:\n      200:\n        headers:\n          ETag: string\n        body: Item\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        section = SymbolKind.SECTION
        # Item holds no `id`: Base wrote it; nor its facet value, annotation or
        # example, which are not declarations. `related?: Item[]` holds no
        # `items`: an expression built it. The two default media types are one
        # `body:`.
        assert _tree(outline.document_symbols(snapshot, f'{folder}/api.raml')) == [
            ('title', SymbolKind.METADATA, 'T'),
            ('annotationTypes', section, '', [('note', SymbolKind.ANNOTATION_TYPE, 'string')]),
            ('types', section, '', [
                ('Base', SymbolKind.TYPE, 'object', [
                    ('facets', section, '', [('unit', SymbolKind.FACET, 'string')]),
                    ('id', SymbolKind.PROPERTY, 'string'),
                ]),
                ('Item', SymbolKind.TYPE, 'Base', [
                    ('tags', SymbolKind.PROPERTY, 'array', [('items', SymbolKind.TYPE, 'string')]),
                    ('related?', SymbolKind.PROPERTY, 'Item[]'),
                    ('/^x-/', SymbolKind.PROPERTY, 'string'),
                ]),
            ]),
            ('/items/{id}', SymbolKind.RESOURCE, '', [
                ('uriParameters', section, '', [('id', SymbolKind.PARAMETER, 'string')]),
                ('get', SymbolKind.METHOD, 'Read', [
                    ('headers', section, '', [('X-Trace', SymbolKind.PARAMETER, 'string')]),
                    ('queryParameters', section, '', [('q?', SymbolKind.PARAMETER, 'string')]),
                    ('200', SymbolKind.RESPONSE, '', [
                        ('headers', section, '', [('ETag', SymbolKind.PARAMETER, 'string')]),
                        ('body', section, '', [
                            ('application/json, application/xml', SymbolKind.BODY, 'Item'),
                        ]),
                    ]),
                ]),
            ]),
        ]  # fmt: skip

    def test_an_undeclared_uri_variable_is_not_outlined(self, memory_workspace):
        # docs/08 § 6.2: P6 synthesizes a `string` for `{part}` and `{x}`, placed
        # at the resource's key, which the author wrote; the parameter they did not.
        document = '#%RAML 1.0\ntitle: T\n/items/{id}/{part}:\n  uriParameters:\n    id: string\n  get:\n/a/{x}:\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        section, resource = SymbolKind.SECTION, SymbolKind.RESOURCE
        assert _tree(outline.document_symbols(snapshot, f'{folder}/api.raml')) == [
            ('title', SymbolKind.METADATA, 'T'),
            ('/items/{id}/{part}', resource, '', [
                ('uriParameters', section, '', [('id', SymbolKind.PARAMETER, 'string')]),
                ('get', SymbolKind.METHOD, ''),
            ]),
            ('/a/{x}', resource, ''),
        ]  # fmt: skip

    def test_a_redeclared_pattern_is_outlined_where_it_was_written(self, memory_workspace):
        """docs/07 § 4 puts a redeclared `/b/` at P's place after unwrap; the
        authored view and the outline follow the author, who wrote `/c/` first.
        """
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n'
            '  P:\n    properties:\n      /a/: string\n      /b/: integer\n'
            '  T:\n    type: P\n    properties:\n      /c/: boolean\n'
            '      /b/:\n        type: integer\n        minimum: 5\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        tree = _tree(outline.document_symbols(snapshot, f'{folder}/api.raml'))
        types = next(entry for entry in tree if entry[0] == 'types')
        subtype = next(entry for entry in types[3] if entry[0] == 'T')
        assert [member[0] for member in subtype[3]] == ['/c/', '/b/']
        assert snapshot.raml is not None
        declared = snapshot.raml.types_in(snapshot.raml.location)['T']
        assert list(declared.shape.pattern_properties) == ['a', 'b', 'c']
        assert [key for key, _ in authored.pattern_properties(declared)] == ['c', 'b']

    def test_a_fragment_file_that_is_one_declaration_outlines_its_body(self, memory_workspace):
        # docs/21 § 4: the file is the declaration, so its members are at the
        # top; the API that includes each lists none of them.
        files = {
            'api.raml': '#%RAML 1.0\ntitle: T\ndocumentation:\n  - !include home.raml\n'
            'securitySchemes:\n  basic: !include basic.raml\ntypes:\n  User: !include user.raml\n',
            'user.raml': '#%RAML 1.0 DataType\ntype: object\nproperties:\n  name: string\n',
            'basic.raml': '#%RAML 1.0 SecurityScheme\ntype: Basic Authentication\n'
            'describedBy:\n  headers:\n    Authorization: string\n  responses:\n    401:\n',
            'home.raml': '#%RAML 1.0 DocumentationItem\ntitle: Home\ncontent: hi\n',
        }
        workspace, folder = _buffered(memory_workspace, files)
        section = SymbolKind.SECTION

        def outlined(name: str):
            uri = f'{folder}/{name}'
            return _tree(outline.document_symbols(next(workspace.serving(uri)), uri))

        assert outlined('user.raml') == [('name', SymbolKind.PROPERTY, 'string')]
        assert outlined('basic.raml') == [('describedBy', section, '', [
            ('headers', section, '', [('Authorization', SymbolKind.PARAMETER, 'string')]),
            ('401', SymbolKind.RESPONSE, ''),
        ])]  # fmt: skip
        assert outlined('home.raml') == [('documentation', section, '', [('Home', SymbolKind.DOCUMENTATION, '')])]
        # `documentation:` is written here, its one item in its own file.
        assert [each[0] for each in outlined('api.raml')] == ['title', 'documentation', 'securitySchemes', 'types']

    def test_a_documentation_item_file_no_api_reads_outlines_its_title(self, memory_workspace):
        files = {'home.raml': '#%RAML 1.0 DocumentationItem\ntitle: Home\ncontent: hi\n'}
        workspace, folder = _buffered(memory_workspace, files)
        uri = f'{folder}/home.raml'
        found = outline.document_symbols(next(workspace.serving(uri)), uri)
        assert _tree(found) == [('documentation', SymbolKind.SECTION, '', [('Home', SymbolKind.DOCUMENTATION, '')])]

    def test_an_alias_or_a_subtype_lists_no_facet_it_did_not_declare(self, memory_workspace):
        # docs/16 § 10: an alias shares its referent's `facets:` container
        # (docs/07 § 3), and a subtype holds its parents' after unwrap.
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n'
            '  A:\n    type: string\n    facets:\n      f: string\n'
            '  B:\n    type: A\n    f: x\n    facets:\n      g?: integer\n'
            '  C: A\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        section, facet = SymbolKind.SECTION, SymbolKind.FACET
        types = _tree(outline.document_symbols(snapshot, f'{folder}/api.raml'))[1]
        assert types == ('types', section, '', [
            ('A', SymbolKind.TYPE, 'string', [('facets', section, '', [('f', facet, 'string')])]),
            ('B', SymbolKind.TYPE, 'A', [('facets', section, '', [('g?', facet, 'integer')])]),
            ('C', SymbolKind.TYPE, 'A'),
        ])  # fmt: skip

    def test_a_file_outlines_what_it_wrote_an_extension_what_it_added(self, memory_workspace):
        # docs/21 § 4: selected by location over the merged model. The type an
        # Extension declares sits in the master's table, and the method it
        # adds sits on the master's resource; both are the Extension's.
        api = '#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n/a:\n  get:\n'
        extension = '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  B: string\n/a:\n  post:\n/b:\n  get:\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': api, 'ext.raml': extension})
        section, resource, method = SymbolKind.SECTION, SymbolKind.RESOURCE, SymbolKind.METHOD
        added = _tree(outline.document_symbols(workspace.snapshot(f'{folder}/ext.raml'), f'{folder}/ext.raml'))
        assert added == [
            ('types', section, '', [('B', SymbolKind.TYPE, 'string')]),
            ('/a', resource, '', [('post', method, '')]),
            ('/b', resource, '', [('get', method, '')]),
        ]
        own = _tree(outline.document_symbols(workspace.snapshot(f'{folder}/api.raml'), f'{folder}/api.raml'))
        assert own == [
            ('title', SymbolKind.METADATA, 'T'),
            ('types', section, '', [('A', SymbolKind.TYPE, 'string')]),
            ('/a', resource, '', [('get', method, '')]),
        ]

    def test_a_section_spans_its_key_and_entries_and_selects_its_key(self, parsed):
        # docs/21 § 4: the decoder records the key the owner wrote.
        snapshot, folder = parsed
        types = next(s for s in outline.document_symbols(snapshot, f'{folder}/api.raml') if s.name == 'types')
        _entity, book = types.children
        line, column = _where(API, 'types:')
        assert (types.span.line, types.span.column, types.span.end_line) == (line, column, book.span.end_line)
        assert (types.selection.line, types.selection.column, types.selection.end_column) == (line, column, column + 5)

    def test_an_empty_or_deprecated_section_is_outlined_at_its_key(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\nschemas:\n  A: string\ntraits: {}\n'
            '/r:\n  uriParameters:\n  get:\n    headers:\n    responses:\n      200:\n        body:\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        found = outline.document_symbols(snapshot, f'{folder}/api.raml')
        section = SymbolKind.SECTION
        assert _tree(found) == [
            ('title', SymbolKind.METADATA, 'T'),
            ('schemas', section, '', [('A', SymbolKind.TYPE, 'string')]),
            ('traits', section, ''),
            ('/r', SymbolKind.RESOURCE, '', [
                ('uriParameters', section, ''),
                ('get', SymbolKind.METHOD, '', [
                    ('headers', section, ''),
                    ('200', SymbolKind.RESPONSE, '', [('body', section, '')]),
                ]),
            ]),
        ]  # fmt: skip
        assert found[2].selection.line == _where(document, 'traits')[0]

    def test_a_section_a_template_supplied_is_not_the_methods(self, memory_workspace):
        # The trait wrote `queryParameters:`; the method wrote `headers:` only.
        document = (
            '#%RAML 1.0\ntitle: T\ntraits:\n  paged:\n    queryParameters:\n      page: integer\n'
            '/r:\n  get:\n    is: [paged]\n    headers:\n      X-Id: string\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        resource = outline.document_symbols(snapshot, f'{folder}/api.raml')[-1]
        (method,) = resource.children
        assert [(child.name, child.selection.line) for child in method.children] == [
            ('is', _where(document, 'paged]')[0]),
            ('headers', _where(document, 'headers:')[0]),
        ]

    def test_the_parser_records_no_section_of_what_a_template_wrote(self, memory_workspace):
        # The resource type's method and response are written in the template;
        # recording their sections at each application would grow the model.
        document = (
            '#%RAML 1.0\ntitle: T\nresourceTypes:\n  rt:\n    get:\n      queryParameters:\n        q: string\n'
            '      responses:\n        200:\n          body:\n            application/json:\n'
            '/a:\n  type: rt\n/b:\n  type: rt\n  uriParameters: {}\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        raml = workspace.snapshot(f'{folder}/api.raml').raml
        assert raml is not None
        recorded = [(each.name, each.key.line) for each in raml.written_sections[f'{folder}/api.raml']]
        assert recorded == [('resourceTypes', 3), ('uriParameters', _where(document, 'uriParameters')[0])]

    def test_a_types_facets_and_a_schemes_described_by_are_placed_at_their_keys(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: T\ntypes:\n  A:\n    facets:\n      unit: string\n'
            'securitySchemes:\n  s:\n    type: Basic Authentication\n    describedBy:\n'
            '      headers:\n        Authorization: string\n'
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        _title, types, schemes = outline.document_symbols(snapshot, f'{folder}/api.raml')
        (facets,) = types.children[0].children
        (described,) = schemes.children[0].children
        (headers,) = described.children
        assert [(each.name, each.selection.line) for each in (facets, described, headers)] == [
            ('facets', _where(document, 'facets:')[0]),
            ('describedBy', _where(document, 'describedBy:')[0]),
            ('headers', _where(document, 'headers:')[0]),
        ]

    def test_an_extension_places_what_it_restated_at_its_own_keys(self, memory_workspace):
        # The merge keeps the master's `types:` and `/a:` keys; each document's
        # own are recorded before it (docs/21 § 4).
        api = '#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n/a:\n  get:\n'
        extension = '#%RAML 1.0 Extension\nextends: api.raml\ntypes:\n  B: string\n/a:\n  post:\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': api, 'ext.raml': extension})
        found = outline.document_symbols(workspace.snapshot(f'{folder}/ext.raml'), f'{folder}/ext.raml')
        assert [(each.name, each.selection.line, each.span.end_line) for each in found] == [
            ('types', _where(extension, 'types:')[0], _where(extension, 'B:')[0]),
            ('/a', _where(extension, '/a:')[0], _where(extension, 'post:')[0]),
        ]
        own = outline.document_symbols(workspace.snapshot(f'{folder}/api.raml'), f'{folder}/api.raml')
        assert [(each.name, each.selection.line) for each in own[1:]] == [
            ('types', _where(api, 'types:')[0]),
            ('/a', _where(api, '/a:')[0]),
        ]

    def test_a_symbol_spans_its_value_and_selects_its_name(self, parsed):
        snapshot, folder = parsed
        types = next(s for s in outline.document_symbols(snapshot, f'{folder}/api.raml') if s.name == 'types')
        book = next(symbol for symbol in types.children if symbol.name == 'Book')
        line, column = _where(API, 'Book:')
        assert (book.selection.line, book.selection.column, book.selection.end_column) == (line, column, column + 4)
        assert (book.span.line, book.span.end_line) == (line, _where(API, 'cover.raml')[0])

    def test_a_documentation_item_selects_its_title_and_an_included_one_is_not_listed(self, memory_workspace):
        # An item has no key: selecting its mapping's first line ran past the
        # span, which VS Code refuses for the whole outline.
        document = '#%RAML 1.0\ntitle: T\ndocumentation:\n - title: Home\n   content: |\n    a\n - !include item.raml\n'
        workspace, folder = _buffered(
            memory_workspace,
            {'api.raml': document, 'item.raml': '#%RAML 1.0 DocumentationItem\ntitle: Inc\ncontent: x\n'},
        )
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        _title, documentation = outline.document_symbols(snapshot, f'{folder}/api.raml')
        (home,) = documentation.children
        line, column = _where(document, 'Home')
        assert (home.selection.line, home.selection.column, home.selection.end_column) == (line, column, column + 4)
        assert (home.span.line, home.span.end_line) == (line, 7)

    def test_workspace_symbols_match_part_of_a_name_in_any_case(self, parsed):
        snapshot, _ = parsed
        found = queries.workspace_symbols([snapshot, snapshot], 'PERS')
        assert [(symbol.name, symbol.uri.rsplit('/', 1)[-1]) for symbol in found] == [('Person', 'lib.raml')]


@pytest.mark.tck
def test_every_outline_entry_holds_its_selection_and_lies_in_its_parent():
    # VS Code refuses an outline whose selection is outside its range: one
    # range error hid the outline of a whole file. Over every file every TCK
    # root read, each entry holds its selection and ends after it starts, and
    # a child lies in its parent.
    from tests.tck.conftest import tck_root

    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set FASTRAML_TCK_DIR')
    workspace = Workspace([path_to_file_uri(root)])
    broken: list[str] = []

    def check(symbols: list[queries.Symbol], parent: queries.Symbol | None) -> None:
        for each in symbols:
            span = each.span
            if (
                not span.contains(each.selection)
                or (span.end_line, span.end_column) < (span.line, span.column)
                or (parent is not None and not parent.span.contains(span))
            ):
                broken.append(f'{each.uri}: {each.name} at {span} in {parent.name if parent else "the file"}')
            check(each.children, each)

    for found in workspace.roots():
        snapshot = workspace.snapshot(found)
        for uri in () if snapshot.raml is None else snapshot.raml.source_texts:
            check(outline.document_symbols(snapshot, uri), None)
    assert not broken, broken


class TestStructure:
    def test_links_are_each_path_with_its_target(self, parsed):
        snapshot, folder = parsed
        found = queries.links(snapshot, f'{folder}/api.raml')
        assert sorted((site.uri.rsplit('/', 1)[-1], site.span.line) for site in found) == [
            ('cover.raml', _where(API, 'cover.raml')[0]),
            ('lib.raml', _where(API, 'lib.raml')[0]),
        ]

    def test_a_path_to_a_file_that_is_no_fragment_links_goes_and_hovers(self, memory_workspace):
        # docs/16 § 9: a path records the file it resolved to, so a schema or
        # a text file is reached as a fragment is, not paired with the path
        # text written on its line.
        document = '#%RAML 1.0\ntitle: T\ndescription: !include notes.md\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document, 'notes.md': 'Notes.'})
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        at = _where(document, 'notes.md')
        assert [(site.uri, site.span.line) for site in queries.links(snapshot, f'{folder}/api.raml')] == [
            (f'{folder}/notes.md', at[0])
        ]
        assert _starts(queries.definition(snapshot, f'{folder}/api.raml', *at)) == [('notes.md', 1, 1)]
        hovered = queries.hover(snapshot, f'{folder}/api.raml', *at)
        assert hovered is not None
        assert '`notes.md`' in hovered[0]
        assert 'Included or imported file' in hovered[0]

    def test_a_block_folds_from_its_key_to_its_last_line(self):
        folded = (_where(API, 'traits:')[0], _where(API, 'Never applied')[0])
        assert folded in queries.folding_ranges(API, 'file:///api.raml')

    def test_a_selection_grows_from_the_token_outward(self):
        line, column = _where(API, 'Lists')
        found = queries.selection_ranges(API, 'file:///api.raml', line, column)
        assert (found[0].line, found[0].column) == (line, column)
        assert [span.line for span in found[1:4]] == [line, line, _where(API, 'get:')[0]]
        assert found[-1].line == 2, 'the document'

    def test_text_that_does_not_compose_has_no_structure(self):
        assert queries.folding_ranges('a: [', 'file:///api.raml') == []
        assert queries.selection_ranges('a: [', 'file:///api.raml', 1, 1) == []


class TestTypeHierarchy:
    def test_a_type_names_its_supertypes_and_its_subtypes(self, parsed):
        snapshot, folder = parsed
        book = queries.type_at(snapshot, f'{folder}/api.raml', *_where(API, 'Book\n'))
        assert [symbol.name for symbol in queries.supertypes(snapshot, book)] == ['Entity']
        entity = queries.supertypes(snapshot, book)[0]
        assert [symbol.name for symbol in queries.subtypes(snapshot, entity)] == ['Book']

    def test_an_item_is_found_again_in_a_later_snapshot_by_where_it_is_written(self, memory_workspace):
        workspace, folder = _buffered(memory_workspace, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        root = f'{folder}/api.raml'
        book = queries.type_at(workspace.snapshot(root), root, *_where(API, 'Book:'))
        workspace.change(root, API + 'version: v2\n', 2)
        assert [symbol.name for symbol in queries.supertypes(workspace.snapshot(root), book)] == ['Entity']


class TestDiagnostics:
    def test_a_remote_fragment_is_a_lint_warning_after_remote_loading(self, memory_workspace):
        class RemoteClient:
            def get(self, _url):
                return SimpleNamespace(status_code=200, content=b'#%RAML 1.0 DataType\ntype: string\n')

        folder = path_to_file_uri(memory_workspace.root)
        workspace = Workspace([folder], http_client=RemoteClient())
        uri = f'{folder}/api.raml'
        workspace.open(uri, '#%RAML 1.0\ntitle: t\ntypes:\n  T: !include https://example.test/t.raml\n', 1)
        snapshot = workspace.snapshot(uri)
        assert queries.diagnostics(snapshot, lint=False) == {}
        warnings = [d for d in queries.diagnostics(snapshot)[uri] if d.code == 'remote-fragment']
        assert [(d.severity, d.info, d.site.span.line) for d in warnings] == [
            ('warning', {'path': 'https://example.test/t.raml'}, 4)
        ]

    def test_a_chain_is_reported_at_its_innermost_frame_with_a_position(self, memory_workspace):
        workspace, folder = _buffered(
            memory_workspace,
            {'api.raml': API.replace('lib.Person', 'lib.Nobody'), 'lib.raml': LIBRARY, 'cover.raml': COVER},
        )
        snapshot = workspace.snapshot(f'{folder}/api.raml')
        (found,) = queries.diagnostics(snapshot, lint=False)[f'{folder}/api.raml']
        assert (found.site.span.line, found.site.span.column) == _where(API, 'lib.Person')
        assert found.info.get('type') == 'lib.Nobody'
        assert found.source == queries.SOURCE

    def test_the_constraint_a_value_broke_is_related_information(self, memory_workspace):
        document = '#%RAML 1.0\ntitle: T\ntypes:\n  Name:\n    minLength: 5\n    example: d\n'
        workspace, folder = _buffered(memory_workspace, {'api.raml': document})
        (found,) = queries.diagnostics(workspace.snapshot(f'{folder}/api.raml'), lint=False)[f'{folder}/api.raml']
        assert (found.code, found.site.span.line, found.site.span.column) == ('value is too short', 6, 14)
        assert [(r.message, r.site.span.line) for r in found.related] == [('declared here', 5)]

    def test_a_file_without_problems_has_no_entry(self, parsed):
        snapshot, _ = parsed
        assert queries.diagnostics(snapshot, lint=False) == {}

    def test_a_finding_is_a_diagnostic_with_its_rule_as_the_code(self, parsed):
        snapshot, folder = parsed
        (found,) = (d for d in queries.diagnostics(snapshot)[f'{folder}/api.raml'] if d.source != queries.SOURCE)
        assert (found.code, found.site.span.line) == ('unused-trait', _where(API, 'spare:')[0])

    def test_the_suppression_line_silences_the_finding(self, memory_workspace):
        workspace, folder = _buffered(memory_workspace, {'api.raml': API, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        root = f'{folder}/api.raml'
        snapshot = workspace.snapshot(root)
        (finding,) = (d for d in queries.diagnostics(snapshot)[root] if d.source == queries.LINT_SOURCE)
        lines = API.splitlines(keepends=True)
        line = finding.site.span.line
        lines.insert(line - 1, queries.suppression(lines[line - 1], finding.code))
        workspace.change(root, ''.join(lines), 2)
        assert root not in queries.diagnostics(workspace.snapshot(root))


#: One mistake at each stage, including locally recovered failures.
FAILURES = _Lenient.FAILURES


class TestAStoppedParseAnswersFromItsStages:
    """docs/21 § 4: a query on a snapshot that stopped early answers from the
    stages it completed, and never raises.
    """

    @pytest.mark.parametrize('stage', list(FAILURES), ids=[s.value for s in FAILURES])
    def test_every_query_answers(self, memory_workspace, stage):
        failure = FAILURES[stage]
        # A second `types:` would be a duplicate key: the mistake joins the first.
        text = (
            API.replace('types:\n', failure, 1)
            if failure.startswith('types:\n')
            else API.replace('uses:\n', failure + 'uses:\n', 1)
        )
        workspace, folder = _buffered(memory_workspace, {'api.raml': text, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        root = f'{folder}/api.raml'
        snapshot = workspace.snapshot(root)
        assert snapshot.raml is not None
        if stage in (Stage.SECURITY, Stage.ANNOTATIONS):
            assert snapshot.raml.stopped_at is None
            assert snapshot.raml.completed == list(Stage)
        else:
            assert snapshot.raml.stopped_at is stage
        for line, column in (_where(text, 'Book\n'), _where(text, 'paged: {'), _where(text, 'lib.Person')):
            queries.definition(snapshot, root, line, column)
            queries.references(snapshot, root, line, column)
            queries.highlights(snapshot, root, line, column)
            queries.hover(snapshot, root, line, column)
            item = queries.type_at(snapshot, root, line, column)
            if item is not None:
                queries.supertypes(snapshot, item)
                queries.subtypes(snapshot, item)
        outline.document_symbols(snapshot, root)
        queries.workspace_symbols([snapshot], 'b')
        queries.links(snapshot, root)
        assert queries.diagnostics(snapshot)[root]

    def test_a_declaration_answers_before_the_names_that_use_it_are_bound(self, memory_workspace):
        # P4 stops at the unknown trait; P7, which binds type names, never runs.
        text = API.replace('is: [{paged', 'is: [nosuch, {paged')
        workspace, folder = _buffered(memory_workspace, {'api.raml': text, 'lib.raml': LIBRARY, 'cover.raml': COVER})
        root = f'{folder}/api.raml'
        snapshot = workspace.snapshot(root)
        assert snapshot.raml.stopped_at is Stage.ENDPOINTS
        assert _starts(queries.definition(snapshot, root, *_where(text, 'Entity:'))) == [
            ('api.raml', *_where(text, 'Entity:'))
        ]
        assert queries.definition(snapshot, root, *_where(text, 'Entity\n')) == []
