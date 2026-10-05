"""Links from prose to declarations (docs/16 § 11)."""

from __future__ import annotations

import pytest

from fastraml import ParseOptions
from fastraml.views import doclinks
from fastraml.views.doclinks import DocLinks, Kind, Outcome, prose_of
from fastraml.views.walk import address

UNWRAP = ParseOptions(unwrap=True)

LIBRARY = """#%RAML 1.0 Library
types:
  Money:
    type: number
    description: A [`Money`] amount; see [`GET /books`].
traits:
  priced:
    description: Prices are [`Money`].
"""

API = """#%RAML 1.0
title: t
uses:
  lib: lib.raml
documentation:
  - title: Getting started
    content: Read about [`Book`] first.
securitySchemes:
  oauth:
    type: Pass Through
annotationTypes:
  rateLimit: integer
types:
  Book:
    description: A book.
  Priced:
    type: lib.Money
/books:
  get:
    is: [lib.priced]
  /{isbn}:
"""


def _links(workspace, description: str, *, files: dict[str, str] | None = None, api: str = API):
    """The links of the API's own `description:`."""
    workspace({'lib.raml': LIBRARY, **(files or {})})
    raml = workspace.document(
        f'{api}description: |\n' + ''.join(f'  {line}\n' for line in description.split('\n')), UNWRAP
    )
    found = DocLinks(raml, address(raml))
    return {link.written: link for link in found.links(raml.entry_point.description, raml.entry_point)}


def _model(workspace, api: str = API):
    workspace({'lib.raml': LIBRARY})
    raml = workspace.document(api, UNWRAP)
    return raml, DocLinks(raml, address(raml))


class TestNames:
    """The name table, docs/16 § 11.1."""

    @pytest.mark.parametrize(
        ('written', 'kind', 'address'),
        [
            ('`/books/{isbn}`', Kind.ENDPOINT, 'fastraml://id#/web-api/endpoint/%2Fbooks%2F%7Bisbn%7D'),
            ('`GET /books`', Kind.METHOD, 'fastraml://id#/web-api/endpoint/%2Fbooks/supportedOperation/get'),
            ('`get /books`', Kind.METHOD, 'fastraml://id#/web-api/endpoint/%2Fbooks/supportedOperation/get'),
            ('`Book`', Kind.TYPE, 'fastraml://id#/declarations/types/Book'),
            ('`lib.Money`', Kind.TYPE, 'fastraml://id/lib.raml#/declarations/types/Money'),
            ('`oauth`', Kind.SECURITY_SCHEME, 'fastraml://id#/declarations/securitySchemes/oauth'),
            ('`(rateLimit)`', Kind.ANNOTATION_TYPE, 'fastraml://id#/declarations/annotations/rateLimit'),
            ('Getting started', Kind.DOCUMENTATION, 'fastraml://id#/web-api/documentation/0'),
        ],
        ids=['resource', 'method', 'method-verb-in-any-case', 'type', 'library-type', 'scheme', 'annotation', 'title'],
    )
    def test_each_kind_of_name_resolves_to_its_address(self, workspace, written, kind, address):
        link = _links(workspace, f'See [{written}].')[written]
        assert (link.outcome, link.target.kind, link.target.address) == (Outcome.RESOLVED, kind, address)

    def test_a_label_is_matched_as_commonmark_matches_it(self, workspace):
        # Case-folded and whitespace-collapsed: the key a renderer's table uses.
        link = _links(workspace, 'See [Getting\n  started].')['Getting\n  started']
        assert (link.label, link.outcome) == ('GETTING STARTED', Outcome.RESOLVED)

    def test_a_backslash_escape_is_undone_before_lookup(self, workspace):
        link = _links(workspace, r'See [`lib\.Money`].')[r'`lib\.Money`']
        assert link.outcome is Outcome.RESOLVED

    def test_a_built_in_type_is_not_a_target(self, workspace):
        assert _links(workspace, 'A [`string`].')['`string`'].outcome is Outcome.UNRESOLVED

    def test_a_trait_is_not_a_target_yet(self, workspace):
        assert _links(workspace, 'Uses [`lib.priced`].')['`lib.priced`'].outcome is Outcome.UNRESOLVED


class TestOneTarget:
    """docs/16 § 11.1, One target."""

    API_WITH_TWIN = API.replace('types:\n  Book:', 'types:\n  oauth: string\n  Book:')

    def test_a_name_in_two_namespaces_is_ambiguous(self, workspace):
        link = _links(workspace, 'See [`oauth`].', api=self.API_WITH_TWIN)['`oauth`']
        assert (link.outcome, sorted(target.kind for target in link.targets)) == (
            Outcome.AMBIGUOUS,
            [Kind.SECURITY_SCHEME, Kind.TYPE],
        )

    @pytest.mark.parametrize(
        ('prefix', 'kind'), [('type', Kind.TYPE), ('securityScheme', Kind.SECURITY_SCHEME)], ids=['type', 'scheme']
    )
    def test_a_kind_prefix_picks_one(self, workspace, prefix, kind):
        written = f'`{prefix}@oauth`'
        link = _links(workspace, f'See [{written}].', api=self.API_WITH_TWIN)[written]
        assert (link.outcome, link.target.kind) == (Outcome.RESOLVED, kind)

    def test_two_spellings_of_one_label_naming_two_types_are_ambiguous(self, workspace):
        api = API.replace('types:\n  Book:', 'types:\n  book: string\n  Book:')
        link = _links(workspace, 'A [`Book`] is not a [`book`].', api=api)['`Book`']
        assert (link.label, link.outcome) == ('`BOOK`', Outcome.AMBIGUOUS)

    def test_two_items_with_one_title_are_ambiguous(self, workspace):
        api = API.replace('documentation:\n', 'documentation:\n  - title: Getting started\n    content: Again.\n')
        assert _links(workspace, 'See [Getting started].', api=api)['Getting started'].outcome is Outcome.AMBIGUOUS


class TestScope:
    """docs/16 § 11.1, Scope: the file that wrote the prose."""

    def test_library_prose_resolves_in_the_library(self, workspace):
        raml, found = _model(workspace)
        money = raml.fragment_types[next(uri for uri in raml.fragment_types if uri.endswith('lib.raml'))]['Money']
        outcomes = {
            link.written: link.outcome for facet, owner in prose_of(money) for link in found.links(facet, owner)
        }
        assert outcomes['`Money`'] is Outcome.RESOLVED

    def test_library_prose_cannot_name_the_api(self, workspace):
        raml, found = _model(workspace)
        money = raml.fragment_types[next(uri for uri in raml.fragment_types if uri.endswith('lib.raml'))]['Money']
        outcomes = {
            link.written: link.outcome for facet, owner in prose_of(money) for link in found.links(facet, owner)
        }
        assert outcomes['`GET /books`'] is Outcome.OUT_OF_SCOPE

    def test_an_inherited_description_resolves_where_it_was_written(self, workspace):
        # `Priced` is the API's, and `Money` is not a name there: only the
        # library, which wrote the description, can resolve it.
        raml, found = _model(workspace)
        priced = raml.fragment_types[raml.location]['Priced']
        links = [link for facet, owner in prose_of(priced) for link in found.links(facet, owner)]
        assert {link.written: link.outcome for link in links}['`Money`'] is Outcome.RESOLVED

    def test_a_trait_contributed_description_resolves_in_the_traits_file(self, workspace):
        raml, found = _model(workspace)
        operation = raml.endpoints['/books'].operations['get']
        links = [link for facet, owner in prose_of(operation) for link in found.links(facet, owner)]
        assert [(link.written, link.outcome) for link in links] == [('`Money`', Outcome.RESOLVED)]

    def test_a_documentation_item_resolves_in_the_api(self, workspace):
        raml, found = _model(workspace)
        item = raml.entry_point.documentation[0]
        links = [link for facet, owner in prose_of(item) for link in found.links(facet, owner)]
        assert [(link.written, link.target.kind) for link in links] == [('`Book`', Kind.TYPE)]


class TestWhichBracketsAreLinks:
    """docs/16 § 11.2."""

    @pytest.mark.parametrize(
        ('text', 'written', 'explicit'),
        [
            ('[`Nope`]', '`Nope`', True),
            ('[the nope][Nope]', 'Nope', True),
            ('[optional]', 'optional', False),
            ('[Nope][]', 'Nope', False),
        ],
        ids=['backticks', 'full-reference', 'bare', 'collapsed'],
    )
    def test_backticks_or_a_full_reference_make_a_link_explicit(self, workspace, text, written, explicit):
        link = _links(workspace, f'See {text}.')[written]
        assert (link.outcome, link.explicit) == (Outcome.UNRESOLVED, explicit)

    def test_a_bare_label_links_when_it_names_one_target(self, workspace):
        link = _links(workspace, 'A [Book].')['Book']
        assert (link.explicit, link.outcome) == (False, Outcome.RESOLVED)

    def test_an_inline_link_is_a_url(self, workspace):
        assert _links(workspace, 'See [the books](/books).') == {}

    def test_a_label_the_prose_defines_is_the_authors_link(self, workspace):
        assert _links(workspace, 'See [Book].\n\n[Book]: https://example.com/book') == {}

    @pytest.mark.parametrize(
        'text', ['Write `[Book]` there.', '```\n[Book]\n```', '    [Book]'], ids=['span', 'fence', 'indented']
    )
    def test_a_label_in_code_is_not_a_link(self, workspace, text):
        assert _links(workspace, f'Code:\n\n{text}') == {}


class TestCost:
    """docs/16 § 11.3."""

    def test_prose_without_a_bracket_is_not_parsed(self, workspace, monkeypatch):
        raml, found = _model(workspace)
        monkeypatch.setattr(doclinks, '_markdown', _refuse)
        book = raml.fragment_types[raml.location]['Book']
        assert [link for facet, owner in prose_of(book) for link in found.links(facet, owner)] == []

    def test_a_text_shared_by_many_entities_is_parsed_once_per_scope(self, workspace, monkeypatch):
        # Three subtypes inherit one description: one parse, not four.
        api = API.replace(
            '  Priced:',
            '  Base:\n    description: A [`Book`].\n  A:\n    type: Base\n  B:\n    type: Base\n  C:\n    type: Base\n'
            '  Priced:',
        )
        raml, found = _model(workspace, api)
        parsed = []
        original = DocLinks._parse
        monkeypatch.setattr(
            DocLinks, '_parse', lambda self, text, scope: parsed.append(text) or original(self, text, scope)
        )
        declared = raml.fragment_types[raml.location]
        for name in ('Base', 'A', 'B', 'C'):
            for facet, owner in prose_of(declared[name]):
                found.links(facet, owner)
        assert parsed == ['A [`Book`].']


def _refuse() -> None:
    raise AssertionError('parsed prose that holds no [')
