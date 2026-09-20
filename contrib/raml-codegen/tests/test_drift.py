"""What the generated client does when the API is not what the document said.

A generated client is written against a document and then run against a server,
and the two drift. This is the one question the golden record cannot answer and
the type checker cannot either, so it is asked of a running client.

**The rule is that a payload never takes the caller down.** A client that raises
on a response mismatch stops working the first time the API moves, and it fails
on the whole body rather than on the one property that changed — including every
property that did arrive, which is usually all of them and usually all the
caller wanted.

| the server | the client | where the discrepancy goes |
|---|---|---|
| adds a property | ignores it | nowhere; this is not a discrepancy |
| adds an `enum` value | passes it through | nowhere |
| drops a required property | leaves the attribute `UNSET`, keeps the rest | `Response.mismatches` |
| sends a value that cannot be read | returns no parsed body, keeps `content` | `Response.mismatches` |
| sends the wrong shape entirely | returns no parsed body, keeps `content` | `Response.mismatches` |
| changes a property's type | passes it through | nowhere; this does not validate |
| answers with an undocumented status | returns `None`, or raises `UnexpectedStatus` when asked | |

`Client(strict=True)` turns the third row back into an exception, for a caller
that would rather stop than proceed on a payload the document does not describe.
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest

BOOK = {
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'GBP'},
    'id': 'bk-1',
    'createdAt': '2024-01-01T00:00:00',
}


@pytest.fixture(scope='module')
def client_package(generated, tmp_path_factory):
    destination = tmp_path_factory.mktemp('drift')
    generated.write(destination)
    sys.path.insert(0, str(destination))
    try:
        yield importlib.import_module('bookstore_api')
    finally:
        sys.path.remove(str(destination))
        for name in [name for name in sys.modules if name.startswith('bookstore_api')]:
            del sys.modules[name]


def models():
    return importlib.import_module('bookstore_api.models')


def errors():
    return importlib.import_module('bookstore_api.errors')


def types():
    return importlib.import_module('bookstore_api.types')


def answering(client_package, payload, status=200, **settings):
    """A client wired to a server that answers with `payload`."""
    client = client_package.Client(base_url='https://acme.books.example.com/v2', **settings)
    client.set_httpx_client(
        httpx.Client(
            base_url=client.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
        )
    )
    return client


def listed(client_package, payload, **settings):
    books = importlib.import_module('bookstore_api.api.books.get_books')
    return books.sync_detailed(client=answering(client_package, payload, **settings))


class TestTheServerAddedSomething:
    def test_an_undescribed_property_is_ignored(self, client_package):
        result = listed(client_package, [BOOK | {'subtitle': 'a new field'}])
        assert result.parsed[0].title == 'Dune'
        assert result.matched

    def test_an_unlisted_enum_value_passes_through(self, client_package):
        # `currency` is `enum: [USD, EUR, GBP]`, so the annotation is a
        # `Literal`. Refusing a fourth currency would make the client the thing
        # that broke when the API grew one.
        money = models().Money.from_dict({'amount': 1.0, 'currency': 'JPY'})
        assert money.currency == 'JPY'


class TestTheServerDroppedSomething:
    def test_the_call_still_answers(self, client_package):
        result = listed(client_package, [{key: value for key, value in BOOK.items() if key != 'isbn'}])
        assert result.status_code == 200
        assert result.parsed[0].title == 'Dune'

    def test_the_missing_property_is_unset_and_falsy(self, client_package):
        result = listed(client_package, [{key: value for key, value in BOOK.items() if key != 'isbn'}])
        assert isinstance(result.parsed[0].isbn, types().Unset)
        assert not result.parsed[0].isbn

    def test_the_discrepancy_is_reported_rather_than_raised(self, client_package):
        result = listed(client_package, [{key: value for key, value in BOOK.items() if key != 'isbn'}])
        assert not result.matched
        assert [(one.model, one.field) for one in result.mismatches] == [('Book', 'isbn')]

    def test_a_nested_model_names_itself_and_not_its_holder(self, client_package):
        result = listed(client_package, [BOOK | {'price': {'amount': 1.0}}])
        assert result.mismatches[0].model == 'Money'
        assert result.mismatches[0].field == 'currency'

    def test_every_missing_property_is_reported_not_just_the_first(self, client_package):
        # Failing on the first would make a caller fix one field, re-run, and
        # find the next. The whole payload is read before anything is said.
        result = listed(client_package, [{'title': 'Dune'}])
        assert {one.field for one in result.mismatches} == {'isbn', 'price', 'id', 'createdAt'}

    def test_a_missing_optional_property_is_not_a_mismatch(self, client_package):
        result = listed(client_package, [BOOK])
        assert isinstance(result.parsed[0].tags, types().Unset)
        assert result.matched


class TestTheServerSentSomethingUnreadable:
    def test_a_value_that_cannot_be_read_costs_the_body_and_not_the_call(self, client_package):
        result = listed(client_package, [BOOK | {'createdAt': 'not-a-date'}])
        assert result.status_code == 200
        assert result.parsed is None
        # The bytes are still here, which is what makes this recoverable.
        assert b'not-a-date' in result.content
        assert 'ValueError' in result.mismatches[0].reason

    def test_an_object_where_an_array_was_promised_is_not_iterated(self, client_package):
        # Iterating the wrong thing is worse than failing to: a `dict` yields
        # its keys, and the client would hand back a list of models built from
        # strings without anything having gone wrong.
        result = listed(client_package, {'oops': 1})
        assert result.parsed is None
        assert 'expected an array, got dict' in result.mismatches[0].reason

    def test_nothing_about_a_payload_escapes_the_client(self, client_package):
        for payload in ([BOOK | {'createdAt': 'no'}], {'oops': 1}, [None], 'a string', 7):
            result = listed(client_package, payload)
            assert result.status_code == 200


class TestTheServerChangedAType:
    def test_it_passes_through_unchecked(self, client_package):
        # Named so the absence is a decision rather than an oversight. Checking
        # here would restate a rule of the language on this side of the line
        # (docs/17 § 2), and the client still could not say whether the *server*
        # is right.
        assert listed(client_package, [BOOK | {'title': 42}]).parsed[0].title == 42


class TestStrictIsAvailableAndIsNotTheDefault:
    def test_strict_raises_on_a_missing_required_property(self, client_package):
        with pytest.raises(errors().UnexpectedPayload) as raised:
            listed(client_package, [{key: value for key, value in BOOK.items() if key != 'isbn'}], strict=True)
        assert (raised.value.model, raised.value.field) == ('Book', 'isbn')

    def test_the_default_is_not_strict(self, client_package):
        assert client_package.Client(base_url='https://x').strict is False

    def test_from_dict_outside_a_response_neither_collects_nor_raises(self, client_package):
        # Somebody calling `Model.from_dict` on their own is not reading a
        # response, so there is nothing to report it to and nothing to be strict
        # about.
        book = models().Book.from_dict({'title': 'Dune'})
        assert book.title == 'Dune'
        assert isinstance(book.isbn, types().Unset)


class TestAUnionSurvivesWhatItCanRecognise:
    def test_each_member_is_decoded_as_itself(self, client_package):
        paged = models().Paged.from_dict(
            {
                'total': 3,
                'items': [
                    BOOK,
                    {'rating': 5, 'author': {'name': 'A', 'verified': True}},
                    {'kind': 'monthly', 'title': 'T', 'issue': 3},
                ],
            }
        )
        assert [type(one).__name__ for one in paged.items] == ['Book', 'Review', 'Magazine']

    def test_what_the_document_does_not_distinguish_arrives_undecoded(self, client_package):
        paged = models().Paged.from_dict({'total': 1, 'items': [[{'note': 'x'}]]})
        assert paged.items == [[{'note': 'x'}]]
