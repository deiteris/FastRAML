"""The example site builds clean, so it keeps showing what the extension does."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from sphinx.testing.util import SphinxTestApp

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'bookstore'


def test_the_bookstore_example_builds_without_a_warning(tmp_path):
    warning = StringIO()
    app = SphinxTestApp('html', srcdir=EXAMPLE, builddir=tmp_path, warning=warning, freshenv=True)
    try:
        app.build()
    finally:
        app.cleanup()
    # Every item of the root namespace is rendered somewhere, every link lands,
    # and the RAML has no error: any of those failing is a warning here.
    assert warning.getvalue() == ''
    guide = (tmp_path / 'html' / 'guide.html').read_text(encoding='utf-8')
    # The guide's copy of `POST /books` is not a target: its links go to the reference.
    assert 'href="reference/books.html#raml-books-method-POST-books"' in guide
