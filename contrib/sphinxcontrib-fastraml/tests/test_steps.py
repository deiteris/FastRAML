"""`raml:send` and `raml:expect`: instructions spelled out in place, with only validated values."""

from __future__ import annotations

import html
import json
import re

OFF = 'raml_warn_unrendered = False'


def rows(built, page: str = 'index') -> list[list[str]]:
    """Every table row on the page, each cell as text."""
    found = re.findall(r'<tr class="row-(?:odd|even)">(.*?)</tr>', built.html(page), re.DOTALL)
    cells = [re.findall(r'<td>(.*?)</td>', row, re.DOTALL) for row in found]
    # The header row has `<th>` cells, and so none here.
    return [[' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', cell)).split()) for cell in row] for row in cells if row]


def blocks(built, page: str = 'index') -> list[str]:
    """Every HTTP block on the page, as text."""
    found = re.findall(
        r'<div class="highlight-http[^"]*"><div class="highlight"><pre>(.*?)</pre>', built.html(page), re.DOTALL
    )
    return [html.unescape(re.sub(r'<[^>]+>', '', block)) for block in found]


def test_a_step_leads_with_the_authors_words_and_links_out_once(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: GET /books/{isbn}\n\n   Ask for the book by its ISBN.\n'},
        conf=OFF,
    )
    text = built.text()
    assert text.startswith('Home ¶ Ask for the book by its ISBN.')
    # What an input means is here, not behind a link, one row each: `{tenant}`
    # is in the host, so it is in the URL rather than the path.
    table = rows(built)
    assert table[0] == ['tenant', 'URL', 'yes', 'The tenant subdomain Matching ^[a-z0-9-]+$ .']
    assert table[1][:3] == ['isbn', 'URL', 'yes']
    assert table[2][:3] == ['Authorization', 'header', 'yes']
    assert table[2][3].startswith('Added by oauth2.')
    # No line restating the method and URL: the message starts with them, with
    # `{version}` bound (`bound_base_uri`).
    assert 'Send GET' not in text
    assert blocks(built)[0].startswith('GET /v2/books/')
    # One link, to the full entry; none resolves here, since no page renders
    # the reference, which also shows the step is no target of its own.
    assert text.endswith('Full reference: GET /books/{isbn}')
    assert built.objects() == {}


def test_a_body_with_no_example_of_its_own_gets_its_supertypes_if_it_validates(build):
    # `POST /books` takes `Book` with no example of its own; `Book`'s validates.
    built = build({'index': 'Home\n====\n\n.. raml:send:: POST /books\n'}, conf=OFF)
    request = blocks(built)[0]
    assert '"title": "Dune"' in request


def test_a_grandparents_example_is_only_shown_when_it_validates_for_the_input(build, tmp_path):
    spec = tmp_path / 'ancestor.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\ntypes:\n'
        '  Root:\n    type: string\n    example: ok\n'
        '  Middle:\n    type: Root\n'
        '/items:\n  get:\n    queryParameters:\n'
        '      accepted:\n        type: Middle\n'
        '      narrowed:\n        type: Middle\n        minLength: 4\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: GET /items\n   :with: accepted narrowed\n'},
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert built.warnings == []
    assert 'accepted=ok' in blocks(built)[0]
    assert 'narrowed=<narrowed>' in blocks(built)[0]


def test_a_narrowing_subtype_never_borrows_its_parents_example(build, tmp_path):
    spec = tmp_path / 'narrow.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\ntypes:\n'
        '  Pet:\n    properties:\n      name: string\n    example: {name: Rex}\n'
        '/pets:\n  post:\n    body:\n      application/json:\n'
        '        type: Pet\n        properties:\n          chip: string\n',
        encoding='utf-8',
    )
    built = build({'index': 'Home\n====\n\n.. raml:send:: POST /pets\n'}, apis=f"'t': {str(spec)!r}", conf=OFF)
    assert built.warnings == []
    # `chip` is required in the body and missing from `Pet`'s example: none is
    # shown, and the block stops at its headers rather than holding a
    # placeholder that is not JSON.
    request = blocks(built)[0]
    assert 'Rex' not in request
    assert request.rstrip().endswith('Content-Type: application/json')
    assert 'The specification has no example of this Pet body.' in built.text()


def test_an_example_its_author_marked_not_strict_is_never_shown(build, tmp_path):
    spec = tmp_path / 'loose.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/pets:\n  get:\n    queryParameters:\n'
        '      size:\n        type: integer\n        example:\n          value: huge\n          strict: false\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: GET /pets\n   :with: size\n'}, apis=f"'t': {str(spec)!r}", conf=OFF
    )
    assert 'size=<size>' in blocks(built)[0]


def test_the_authors_values_are_used_once_fastraml_accepts_them(build, tmp_path):
    body = tmp_path / 'source' / 'book.json'
    body.parent.mkdir(parents=True, exist_ok=True)
    book = {
        'id': 'b-9',
        'createdAt': '2024-05-05T00:00:00Z',
        'title': 'Kindred',
        'isbn': '9780807083697',
        'price': {'amount': 7.5, 'currency': 'USD'},
    }
    body.write_text(json.dumps(book), encoding='utf-8')
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:send:: POST /books
                   :values:
                      tenant = acme
                   :body: book.json
                """,
        },
        conf=OFF,
    )
    assert built.warnings == []
    request = blocks(built)[0]
    assert 'Host: acme.books.example.com' in request
    assert '"title": "Kindred"' in request


def test_a_value_fastraml_rejects_is_a_warning_and_a_placeholder(build, tmp_path):
    body = tmp_path / 'source' / 'partial.json'
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text(json.dumps({'title': 'Half a book'}), encoding='utf-8')
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:send:: POST /books
                   :values:
                      tenant = ACME
                   :body: partial.json
                """,
        },
        conf=OFF,
    )
    # fastraml's own reasons, one per value; neither value is shown.
    assert any('tenant' in warning and 'pattern' in warning for warning in built.warnings), built.warnings
    assert any('missing required properties' in warning for warning in built.warnings), built.warnings
    request = blocks(built)[0]
    assert 'Host: <tenant>.books.example.com' in request
    assert 'Half a book' not in request
    assert request.rstrip().endswith('Content-Type: application/json')
    # Nothing but those two warnings: the block itself highlights cleanly.
    assert len(built.warnings) == 2, built.warnings


def test_optional_inputs_appear_only_when_named(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:send:: GET /books

                .. raml:send:: GET /books
                   :with: limit
                   :values:
                      offset = 40
                """,
        },
        conf=OFF,
    )
    plain, asked = blocks(built)
    assert '?' not in plain.splitlines()[0]
    # `limit` shows its default; `offset` the author's value, read as text.
    assert 'limit=20' in asked
    assert 'offset=40' in asked
    # Named, so shown; optional, and the table says so.
    assert ['limit', 'query', 'no'] in [row[:3] for row in rows(built)]


def test_a_query_string_is_shown_and_validated_as_a_whole(build, tmp_path):
    spec = tmp_path / 'query.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/items:\n  get:\n    queryString:\n'
        '      type: string\n      pattern: ^token=.+$\n      example: token=abc\n',
        encoding='utf-8',
    )
    built = build(
        {
            'index': 'Home\n====\n\n.. raml:send:: GET /items\n\n'
            '.. raml:send:: GET /items\n   :values:\n      queryString = token=other\n',
        },
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert built.warnings == []
    assert blocks(built)[0].startswith('GET /items?token=abc HTTP/1.1')
    assert blocks(built)[1].startswith('GET /items?token=other HTTP/1.1')


def test_an_object_query_string_is_encoded_and_an_invalid_one_is_not_shown(build, tmp_path):
    spec = tmp_path / 'query-object.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/items:\n  get:\n    queryString:\n'
        '      type: object\n      properties:\n        search: string\n'
        '      example: {search: "red blue"}\n',
        encoding='utf-8',
    )
    built = build(
        {
            'index': 'Home\n====\n\n.. raml:send:: GET /items\n\n'
            '.. raml:send:: GET /items\n   :values:\n      queryString = {"other": 2}\n',
        },
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert blocks(built)[0].startswith('GET /items?search=red%20blue HTTP/1.1')
    assert blocks(built)[1].startswith('GET /items?<queryString> HTTP/1.1')
    assert any('queryString' in warning and 'not valid' in warning for warning in built.warnings)


def test_uri_template_values_are_encoded_before_splitting_the_url(build, tmp_path):
    spec = tmp_path / 'uri.raml'
    spec.write_text('#%RAML 1.0\ntitle: T\n/items/{id}:\n  get:\n', encoding='utf-8')
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: GET /items/{id}\n   :values:\n      id = a#b?c/d\n'},
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert built.warnings == []
    assert blocks(built)[0].startswith('GET /items/a%23b%3Fc%2Fd HTTP/1.1')


def test_a_name_the_method_does_not_have_is_a_warning(build):
    built = build({'index': 'Home\n====\n\n.. raml:send:: GET /books\n   :with: sort\n'}, conf=OFF)
    assert any("has no input named 'sort'" in warning for warning in built.warnings)


def test_the_security_asked_for_is_the_one_shown(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:send:: GET /books/{isbn}
                   :security: none

                .. raml:send:: GET /books/{isbn}
                   :security: basic
                """,
        },
        conf=OFF,
    )
    anonymous = blocks(built)[0]
    assert 'Authorization' not in anonymous
    assert 'No authentication is needed.' in built.text()
    # `basic` does not secure this method: said so, and the first scheme is used.
    assert any("is not secured by 'basic'" in warning for warning in built.warnings)
    assert 'Authorization: <Authorization>' in blocks(built)[1]


def test_asking_for_authentication_on_an_open_method_warns(build, tmp_path):
    spec = tmp_path / 'open.raml'
    spec.write_text('#%RAML 1.0\ntitle: T\n/open:\n  get:\n', encoding='utf-8')
    built = build(
        {
            'index': 'Home\n====\n\n.. raml:send:: GET /open\n   :security: basic\n\n'
            '.. raml:send:: GET /open\n   :security: none\n',
        },
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert len(built.warnings) == 1
    assert "is not secured by 'basic'" in built.warnings[0]
    assert 'Authenticate with basic' not in built.text()


def test_expect_spells_out_the_response(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:expect:: GET /books/{isbn} 200

                .. raml:expect:: POST /books 201
                   :fields: none
                """,
        },
        conf=OFF,
    )
    text = built.text()
    # Straight to what comes back: no line announcing the status, which the
    # message starts with, and not the response's own description.
    assert text.startswith('Home ¶ Body, application/json : Book Field Type Required Meaning')
    assert 'OK OK' not in text
    # What comes back is explained as a request body is: every field of the
    # payload, marked required or not.
    table = [row[:3] for row in rows(built)]
    assert ['title', 'string', 'yes'] in table
    assert ['tags', 'array of string', 'no'] in table
    # `:fields: none` for a response that only echoes what a step above sent.
    # `Location` has no description, so it has no row either: the message
    # shows it.
    echo = text[text.index('Full reference: GET /books/{isbn} 200') :]
    assert 'Field Type' not in echo
    assert 'Location' not in echo.partition('HTTP')[0]
    assert blocks(built)[1].startswith('HTTP/1.1 201 Created')
    assert 'Location: <Location>' in blocks(built)[1]


def test_the_body_reads_as_a_table_with_constraints_in_words(build):
    built = build({'index': 'Home\n====\n\n.. raml:send:: POST /books\n'}, conf=OFF)
    fields = {row[0]: row for row in rows(built) if row[0] in {'title', 'isbn'}}
    # One row a field: its name, its type in words, whether it is required,
    # and its meaning with the constraints as a caller reads them.
    assert fields['title'][:3] == ['title', 'string', 'yes']
    assert fields['title'][3].endswith('as printed on the cover. 1–200 characters.')
    assert fields['isbn'][3].endswith('Exactly 13 characters, matching ^\\d{13}$ .')
    # Every field, the optional ones too: the example body carries `tags`, and
    # the table is where a reader learns it may be left out.
    assert ['tags', 'array of string', 'no'] in [row[:3] for row in rows(built)]
    assert 'not shown' not in built.text()


def test_an_input_is_explained_once_per_page(build):
    built = build(
        {
            'index': """\
                Home
                ====

                .. raml:send:: GET /books/{isbn}

                .. raml:send:: DELETE /books/{isbn}
                """,
        },
        conf=OFF,
    )
    explained = [row[0] for row in rows(built)]
    # `tenant`, `isbn` and `Authorization` once each; the second step still
    # carries their values in its message.
    assert explained.count('tenant') == 1
    assert explained.count('isbn') == 1
    _, delete = blocks(built)
    assert 'Host: <tenant>.books.example.com' in delete


def test_a_response_header_the_spec_explains_gets_a_row(build, tmp_path):
    spec = tmp_path / 'headers.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/pets:\n  post:\n    responses:\n      201:\n        headers:\n'
        '          Location:\n            description: Where the new pet lives.\n'
        '          X-Trace:\n',
        encoding='utf-8',
    )
    built = build({'index': 'Home\n====\n\n.. raml:expect:: POST /pets 201\n'}, apis=f"'t': {str(spec)!r}", conf=OFF)
    assert rows(built) == [['Location', 'yes', 'Where the new pet lives.']]
    assert 'X-Trace: <X-Trace>' in blocks(built)[0]


def test_fields_can_follow_the_example(build):
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: POST /books\n   :fields: example\n'},
        conf=OFF,
    )
    shown = [row[0] for row in rows(built) if row[1] not in {'URL', 'header', 'query'}]
    # `Book`'s example: every required field, and of the optional ones none --
    # so `tags`, which it does not carry, has no row.
    assert shown == ['title', 'isbn', 'price', 'id', 'createdAt']


def test_fields_follow_every_field_when_there_is_no_example(build, tmp_path):
    spec = tmp_path / 'bare.raml'
    spec.write_text(
        '#%RAML 1.0\ntitle: T\n/pets:\n  post:\n    body:\n      application/json:\n'
        '        properties:\n          name: string\n          nick?: string\n',
        encoding='utf-8',
    )
    built = build(
        {'index': 'Home\n====\n\n.. raml:send:: POST /pets\n   :fields: example\n'},
        apis=f"'t': {str(spec)!r}",
        conf=OFF,
    )
    assert [row[0] for row in rows(built)] == ['name', 'nick']


def test_a_declared_type_in_a_step_links_to_its_entry(build):
    built = build(
        {
            'index': 'Home\n====\n\n.. raml:send:: POST /books\n',
            'types': 'Types\n=====\n\n.. raml:type:: Book\n\n.. raml:type:: Money\n',
        },
        conf=OFF,
    )
    links = built.links()
    # The body's type, and a field's.
    assert 'types.html#raml-books-type-sample-api.raml-Book' in links
    assert 'types.html#raml-books-type-sample-api.raml-Money' in links
