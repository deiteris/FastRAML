"""`raml:send` and `raml:expect`: instructions spelled out in place, with only validated values."""

from __future__ import annotations

import html
import json
import re

OFF = 'raml_warn_unrendered = False'


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
    assert text.index('Ask for the book by its ISBN.') < text.index('Send GET')
    # What an input means is here, not behind a link: the tenant's pattern and
    # the ISBN's, in words.
    assert 'tenant (path)' in text
    assert 'The tenant subdomain' in text
    assert 'isbn (path)' in text
    # One link, to the full entry; none resolves here, since no page renders
    # the reference, which also shows the step is no target of its own.
    assert text.endswith('Full reference: GET /books/{isbn}')
    assert built.objects() == {}


def test_a_body_with_no_example_of_its_own_gets_its_supertypes_if_it_validates(build):
    # `POST /books` takes `Book` with no example of its own; `Book`'s validates.
    built = build({'index': 'Home\n====\n\n.. raml:send:: POST /books\n'}, conf=OFF)
    request = blocks(built)[0]
    assert '"title": "Dune"' in request


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


def test_expect_spells_out_the_response(build):
    built = build({'index': 'Home\n====\n\n.. raml:expect:: POST /books 201\n'}, conf=OFF)
    text = built.text()
    # Straight to what comes back: the response's own description is the
    # reference's to show, and here it would only say `Created` again.
    assert 'You get back 201 Created With:' in text
    # The body is named, and its fields are not explained again: the block
    # shows them, and a guide has usually just listed them for the request.
    assert 'Body, application/json : Book' in text
    assert 'as printed on the cover' not in text
    assert 'Location (header)' in text
    response = blocks(built)[0]
    assert response.startswith('HTTP/1.1 201 Created')
    assert 'Location: <Location>' in response
