"""Whole builds, one per authoring case the directives and roles were chosen for."""

from __future__ import annotations

from os import utime

from sphinxcontrib.fastraml import apis, check_rendered
from sphinxcontrib.fastraml.domain import Target

from .conftest import FIXTURES, ROOT, sample

REFERENCE = """\
Reference
=========

.. raml:overview::

.. raml:documentation::

.. raml:endpoints::

.. raml:types::

.. raml:annotation-types::

.. raml:security-schemes::

Libraries
---------

.. raml:types::
   :file: sample/common.raml

.. raml:annotation-types::
   :file: sample/common.raml

.. raml:types::
   :file: shared/measures.raml
"""


def test_a_complete_reference_builds_clean_and_every_role_lands(build):
    built = build(
        {
            'index': """\
            Home
            ====

            Fetch :raml:method:`get /books/{isbn}` for a :raml:type:`Book` by its
            :raml:property:`Book.isbn`; a hit is :raml:response:`GET /books/{isbn} 200`.
            Sign in with :raml:security-scheme:`oauth2`, pick a :raml:base-uri-parameter:`{tenant}`,
            read :raml:documentation-item:`Getting started`, see :raml:endpoint:`/shelves`,
            mark with :raml:annotation-type:`rateLimit`, and start at :raml:api:`books`.
            """,
            'reference': REFERENCE,
        }
    )
    assert built.warnings == []
    anchors = [link.partition('#')[2] for link in built.links() if link.startswith('reference.html#')]
    assert anchors == [
        'raml-books-method-GET-books-isbn',
        'raml-books-type-sample-api.raml-Book',
        'raml-books-property-sample-api.raml-Book.isbn',
        'raml-books-response-GET-books-isbn-200',
        'raml-books-security-scheme-sample-api.raml-oauth2',
        'raml-books-base-uri-parameter-tenant',
        'raml-books-documentation-item-Getting-started',
        'raml-books-endpoint-shelves',
        'raml-books-annotation-type-sample-api.raml-rateLimit',
        'raml-books-api',
    ]
    # The API link shows its title, not the namespace the author wrote.
    assert 'start at Bookstore API' in built.text()


def test_a_documentation_item_is_a_section_in_the_toctree(build):
    built = build({'index': 'Home\n====\n', 'reference': REFERENCE})
    # The global toctree in the sidebar lists the items under the page.
    sidebar = built.html('index')
    assert 'raml-books-documentation-item-Getting-started' in sidebar
    assert 'raml-books-documentation-item-Errors' in sidebar


def test_a_name_that_is_rendered_nowhere_warns_and_one_that_says_so_does_not(build):
    built = build(
        {
            'index': """\
            Home
            ====

            Gone: :raml:method:`!GET /legacy`. Missing: :raml:method:`GET /legacy`.
            """,
            'reference': REFERENCE,
        }
    )
    assert len(built.warnings) == 1
    assert 'GET /legacy' in built.warnings[0]


def test_what_no_page_renders_is_reported_once_each(build):
    built = build({'index': 'Home\n====\n\n.. raml:overview::\n\n.. raml:endpoint:: /books\n'})
    unrendered = [warning for warning in built.warnings if 'rendered on no page' in warning]
    assert any("endpoint '/shelves'" in warning for warning in unrendered)
    assert any("method 'GET /books/{isbn}'" in warning for warning in unrendered)
    assert any(f"type '{ROOT}#Book'" in warning for warning in unrendered)
    # `/books` and its methods are rendered, so none of them is reported.
    rendered = ("'/books'", "'GET /books'", "'POST /books'")
    assert not any(name in warning for warning in unrendered for name in rendered)
    assert len(unrendered) == len(set(unrendered))


def test_the_check_is_off_when_the_site_says_so(build):
    built = build({'index': 'Home\n====\n\n.. raml:overview::\n'}, conf='raml_warn_unrendered = False')
    assert built.warnings == []


def test_rendering_one_item_twice_warns_unless_one_copy_is_not_indexed(build):
    twice = build(
        {
            'index': 'Home\n====\n',
            'reference': REFERENCE,
            'tutorial': 'Tutorial\n========\n\n.. raml:method:: GET /books\n',
        }
    )
    assert any('rendered twice' in warning and 'GET /books' in warning for warning in twice.warnings)
    copied = build(
        {
            'index': 'Home\n====\n',
            'reference': REFERENCE,
            'tutorial': 'Tutorial\n========\n\n.. raml:method:: GET /books\n   :no-index:\n',
        }
    )
    assert copied.warnings == []
    # A link goes to the reference, never to the tutorial's copy.
    assert copied.objects()['method', 'books', 'GET /books'].docname == 'reference'


def test_rendering_one_item_twice_on_the_same_page_warns(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:type:: AnythingAlias\n\n.. raml:type:: AnythingAlias\n'},
        conf='raml_warn_unrendered = False',
    )
    assert len([warning for warning in built.warnings if 'rendered twice' in warning]) == 1
    assert built.objects()['type', 'books', f'{ROOT}#AnythingAlias'].anchor == (
        'raml-books-type-sample-api.raml-AnythingAlias'
    )


def test_an_authors_note_follows_the_raml_prose_and_precedes_the_fields(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:method:: GET /books/{isbn}

                   Rate-limited to ten a minute.
                """,
        },
        conf='raml_warn_unrendered = False',
    )
    text = built.text()
    assert text.index('Returns one book by its ISBN-13.') < text.index('Rate-limited to ten a minute.')
    assert text.index('Rate-limited to ten a minute.') < text.index('Security')


def test_levels_of_detail(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:endpoint:: /books
                   :depth: 1
                   :detail: summary

                .. raml:method:: GET /books/{isbn}
                   :detail: request
                """,
        },
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    objects = built.objects()
    # A summary is a list of links, and registers nothing.
    assert ('endpoint', 'books', '/books') not in objects
    assert ('method', 'books', 'GET /books/{isbn}') in objects
    # `request` leaves the responses out, and so their targets.
    assert not any(kind == 'response' for kind, _, _ in objects)
    assert 'Responses' not in built.text()


def test_depth_renders_the_endpoints_below(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:endpoint:: /books\n   :depth: 1\n   :methods: get\n'},
        conf='raml_warn_unrendered = False',
    )
    objects = built.objects()
    assert ('endpoint', 'books', '/books/{isbn}') in objects
    assert ('method', 'books', 'GET /books/{isbn}') in objects
    assert ('method', 'books', 'POST /books') not in objects


def test_a_body_of_a_declared_type_links_to_it_rather_than_repeating_it(build):
    built = build({'index': 'Home\n====\n', 'reference': REFERENCE})
    # Book's description is in Book's entry, once; its uses as a body link there.
    assert built.text('reference').count('One book in the catalogue.') == 1


def test_an_inline_subtype_shows_narrowed_inherited_properties(build, tmp_path):
    spec = tmp_path / 'narrow.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\ntypes:\n  Parent:\n    properties:\n'
        '      code: string\n      unchanged: string\n/items:\n  post:\n    body:\n'
        '      application/json:\n        type: Parent\n        properties:\n'
        '          code:\n            type: string\n            minLength: 5\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:method:: POST /items\n'},
        apis=f"'t': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    assert 'code : string' in built.text()
    assert 'at least 5 characters' in built.text().lower()
    assert 'unchanged' not in built.text()


def test_a_json_schema_body_lists_the_properties_of_its_projection(build, tmp_path):
    """docs/10 § 7: a schema is shown through the nearest RAML shape, not as an opaque leaf."""
    spec = tmp_path / 'schema.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/items:\n  post:\n    body:\n      application/json:\n'
        '        type: \'{"type": "object", "properties": {"sku": {"type": "string"}}}\'\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:method:: POST /items\n'},
        apis=f"'t': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    assert 'sku : string' in built.text()


def test_a_library_type_renders_by_file_and_links_by_file(build):
    built = build(
        {
            'index': """\
                Home
                ====

                A :raml:type:`sample/common.raml#Page` of results.

                .. raml:types::
                   :file: sample/common.raml
                """,
        },
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    assert ('type', 'books', 'sample/common.raml#Page') in built.objects()


def test_several_apis_by_namespace(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:api:: shop

                .. raml:type:: Book

                .. raml:method:: GET /books
                   :api: books

                :raml:type:`Book` is the shop's; :raml:method:`books:GET /books` is the other's.
                """,
        },
        apis=f'{sample("books")}, {sample("shop")}',
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    objects = built.objects()
    assert ('type', 'shop', f'{ROOT}#Book') in objects
    assert ('method', 'books', 'GET /books') in objects
    assert ('type', 'books', f'{ROOT}#Book') not in objects


def test_without_a_namespace_several_apis_are_ambiguous(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:overview::\n'},
        apis=f'{sample("books")}, {sample("shop")}',
        conf='raml_warn_unrendered = False',
    )
    assert any('which RAML API' in warning for warning in built.warnings)


def test_value_roles_write_the_apis_values(build):
    built = build(
        {'index': 'Home\n====\n\nVersion :raml:version:`books` at :raml:base-uri:`books`.\n'},
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    # The base URI a caller uses: `{version}` bound by fastraml, `{tenant}` the caller's.
    assert 'Version v2 at https://{tenant}.books.example.com/v2 .' in built.text()


def test_a_page_depends_on_every_file_the_api_was_read_from(build):
    built = build({'index': 'Home\n====\n\n.. raml:overview::\n'}, conf='raml_warn_unrendered = False')
    depends = {path.name for path in built.app.env.dependencies['index']}
    assert {'api.raml', 'common.raml', 'measures.raml', 'machine.raml', 'invoice.json'} <= depends


def test_pages_with_only_link_roles_depend_on_the_api_files(build):
    built = build(
        {
            'index': 'Home\n====\n\nSee :raml:type:`Book`.\n',
            'api': 'API\n===\n\nSee :raml:api:`books`.\n',
            'reference': 'Reference\n=========\n\n.. raml:overview::\n\n.. raml:type:: Book\n',
        },
        conf='raml_warn_unrendered = False',
    )
    assert built.warnings == []
    for docname in ('index', 'api'):
        depends = {path.name for path in built.app.env.dependencies[docname]}
        assert {'api.raml', 'common.raml', 'measures.raml', 'machine.raml', 'invoice.json'} <= depends


def test_a_role_only_page_depends_on_an_unreadable_api_file(build, tmp_path):
    spec = tmp_path / 'later.raml'
    built = build(
        {'index': 'Home\n====\n\nSee :raml:type:`Book`.\n'},
        apis=f"'t': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    assert spec in built.app.env.dependencies['index']


def test_a_new_page_read_refreshes_its_cached_api(build, tmp_path):
    spec = tmp_path / 'changing.raml'
    spec.write_text('#%RAML 1.0\ntitle: First\n', encoding='utf-8')
    built = build(
        {'index': 'Home\n====\n\n.. raml:overview::\n'},
        apis=f"'t': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    env = built.app.env
    env.prepare_settings('index')
    first = apis.page_api(env, 't')
    assert first is apis.page_api(env, 't')
    assert first is not None
    assert first.catalogue.title == 'First'

    spec.write_text('#%RAML 1.0\ntitle: Second\n', encoding='utf-8')
    later = spec.stat().st_mtime + 2
    utime(spec, (later, later))
    apis.clear_page_cache(built.app, 'index', [])
    env.prepare_settings('index')
    second = apis.page_api(env, 't')
    assert second is not None
    assert second.catalogue.title == 'Second'


def test_parallel_target_merges_warn_and_keep_the_first_target(build, monkeypatch):
    built = build({'index': 'Home\n====\n'}, conf='raml_warn_unrendered = False')
    domain = built.app.env.get_domain('raml')
    key = ('type', 'books', f'{ROOT}#Book')
    domain.merge_domaindata({'first'}, {'objects': {key: Target('first', 'first-id', 'Book')}, 'linked': {}})
    warnings = []
    monkeypatch.setattr('sphinxcontrib.fastraml.domain.logger.warning', lambda *args, **_kwargs: warnings.append(args))
    domain.merge_domaindata({'second'}, {'objects': {key: Target('second', 'second-id', 'Book')}, 'linked': {}})
    assert domain.objects[key].docname == 'first'
    assert len(warnings) == 1
    assert 'rendered twice' in warnings[0][0]


def test_clearing_a_page_preserves_links_from_other_pages(build, monkeypatch):
    built = build({'index': 'Home\n====\n'}, conf='raml_warn_unrendered = False')
    domain = built.app.env.get_domain('raml')
    key = ('type', 'books', 'sample/common.raml#Address')
    domain.note_link(*key, 'first')
    domain.note_link(*key, 'second')
    domain.clear_doc('first')
    assert domain.linked[key] == {'second'}
    domain.merge_domaindata({'third'}, {'objects': {}, 'linked': {key: {'third', 'unrelated'}}})
    assert domain.linked[key] == {'second', 'third'}
    warnings = []
    monkeypatch.setattr('sphinxcontrib.fastraml.logger.warning', lambda *args, **_kwargs: warnings.append(args))
    built.app.config.raml_warn_unrendered = True
    check_rendered(built.app, built.app.env)
    assert len([message for message in warnings if key[2] in message and 'second' in message]) == 1


def test_a_diagnostic_is_a_warning_at_the_raml_line(build, tmp_path):
    spec = tmp_path / 'broken.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\ntypes:\n  Age:\n    type: integer\n    example: old\n',
        encoding='utf-8',
    )
    built = build({'index': 'Home\n====\n\n.. raml:overview::\n'}, apis=f"'t': {str(spec)!r}")
    # At the example, where the author has to act, naming the type it failed.
    placed = [warning for warning in built.warnings if f'{spec}:6' in warning]
    assert len(placed) == 1, built.warnings
    assert 'invalid example' in placed[0]
    assert 'expected: integer' in placed[0]


def test_a_cached_diagnostic_is_reported_on_every_build(build, tmp_path):
    spec = tmp_path / 'broken.raml'
    spec.write_text('#%RAML 1.0\ntitle: T\ntypes:\n  Age:\n    type: integer\n    example: old\n', encoding='utf-8')
    pages = {'index': 'Home\n====\n\n.. raml:overview::\n'}
    first = build(pages, apis=f"'t': {str(spec)!r}", conf='raml_warn_unrendered = False')
    second = build(pages, apis=f"'t': {str(spec)!r}", conf='raml_warn_unrendered = False')
    for result in (first, second):
        assert len([warning for warning in result.warnings if 'invalid example' in warning]) == 1


def test_two_namespaces_for_one_file_report_its_diagnostic_once(build, tmp_path):
    spec = tmp_path / 'broken.raml'
    spec.write_text('#%RAML 1.0\ntitle: T\ntypes:\n  Age:\n    type: integer\n    example: old\n', encoding='utf-8')
    built = build(
        {'index': 'Home\n====\n'},
        apis=f"'first': {str(spec)!r}, 'second': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    assert len([warning for warning in built.warnings if 'invalid example' in warning]) == 1


def test_an_unreadable_api_is_reported_once_per_build_and_retried_when_it_appears(build, tmp_path):
    spec = tmp_path / 'later.raml'
    pages = {'index': 'Home\n====\n\n.. raml:overview::\n'}
    config = f"'t': {str(spec)!r}"
    for _ in range(2):
        built = build(pages, apis=config, conf='raml_warn_unrendered = False')
        assert len([warning for warning in built.warnings if "RAML API 't' could not be read:" in warning]) == 1
    spec.write_text('#%RAML 1.0\ntitle: Arrived\n', encoding='utf-8')
    built = build(pages, apis=config, conf='raml_warn_unrendered = False')
    assert built.warnings == []
    assert 'Arrived' in built.text()


def test_a_workspace_refusal_names_the_root_it_needed(build):
    built = build({'index': 'Home\n====\n'}, apis=f"'b': {str(FIXTURES / 'sample' / 'api.raml')!r}")
    assert any('suggested_root' in warning for warning in built.warnings)


def test_a_linked_library_type_rendered_nowhere_is_reported_once(build):
    built = build({'index': 'Home\n====\n\n.. raml:types::\n'})
    linked = [warning for warning in built.warnings if 'linked from' in warning]
    # `Delivery` has an `address: common.Address`, and no page renders the library.
    assert any("'sample/common.raml#Address'" in warning for warning in linked), built.warnings
    assert len(linked) == len(set(linked))


def test_a_parallel_read_merges_every_pages_targets(build):
    # Serial on Windows, where Sphinx cannot fork; CI's Linux job reads in parallel.
    pages = {'index': 'Home\n====\n', 'reference': REFERENCE}
    pages.update({f'page{n}': f'Page {n}\n{"=" * 6}\n\nSee :raml:method:`GET /books`.\n' for n in range(6)})
    built = build(pages, parallel=4)
    assert built.warnings == []
    assert 'reference.html#raml-books-method-GET-books' in built.html('page3')


def test_a_declared_array_union_or_annotation_shows_what_it_holds(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:type:: Prices

                .. raml:type:: Search

                .. raml:annotation-type:: rateLimit
                """,
        },
        conf='raml_warn_unrendered = False',
    )
    text = built.text()
    assert 'Prices : array of Money' in text
    assert 'Search : string | number' in text
    assert 'perMinute : integer' in text
    # `rateLimit`'s value is an object; its properties are listed, not addressable.
    assert not any(kind == 'property' for kind, _, _ in built.objects())


def test_a_methods_own_security_list_is_the_one_shown(build, tmp_path):
    # An explicit empty list on the method replaces the resource's (docs/09 § A4);
    # showing the resource's would re-add a requirement the method removed.
    spec = tmp_path / 'secured.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n'
        '/open:\n  securedBy: [basic]\n  get:\n    securedBy: []\n  post:\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:endpoint:: /open\n'},
        apis=f"'t': {str(spec)!r}",
        conf='raml_warn_unrendered = False',
    )
    text = built.text()
    get, post = text.index('GET /open'), text.index('POST /open')
    assert 'Security' not in text[get:post]
    assert 'Security : basic' in text[post:]


def test_model_values_read_as_the_author_wrote_them(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:type:: Money\n\n.. raml:type:: AnythingAlias\n'},
        conf='raml_warn_unrendered = False',
    )
    text = built.text()
    # A bound is the decimal the author wrote, not the ratio fastraml keeps it as,
    # and in words a caller reads rather than RAML's facet name.
    # `amount` is `minimum: 0` and `multipleOf: 0.01`: one sentence, bounds first.
    assert 'At least 0 , a multiple of 0.01 .' in text
    # An alias names what it aliases, and shares everything with it (docs/07 § 3).
    assert 'AnythingAlias = Anything' in text
