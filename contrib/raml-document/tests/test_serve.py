"""`raml_document.serve`: the parse-back every integration serves through."""

import re

import pytest

from raml_document import Document, Report, TypeDecl
from raml_document.serve import RAML_MEDIA_TYPE, BuildError, build, media_type


def _report(example: object, dropped: list[str] | None = None) -> Report:
    decl = TypeDecl(type='object', properties={'n': TypeDecl(type='integer', examples={'e0': example})})
    return Report(Document(title='T', types={'M': decl}), dropped or [])


def test_a_document_that_parses_is_projected() -> None:
    served = build(_report(1))
    assert served.text.startswith('#%RAML 1.0')
    assert 'M' in next(iter(served.tree['types'].values()))
    assert served.tree_json.startswith(b'{')


def test_a_document_that_does_not_parse_is_explained_at_its_line() -> None:
    with pytest.raises(BuildError) as caught:
        build(_report('ten'))
    message = str(caught.value)
    assert re.search(r'api\.raml:\d+:\d+ ', message)
    assert re.search(r'\n +\d+ \| ', message)
    assert caught.value.text.startswith('#%RAML 1.0')


def test_strict_refuses_what_was_left_out_and_everything_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    report = _report(1, ['M.n: exclusive minimum 0 has no RAML facet; not written'])
    with caplog.at_level('WARNING', logger='raml_document'):
        assert build(report).dropped == report.dropped
    assert any('exclusive minimum' in record.getMessage() for record in caplog.records)
    with pytest.raises(BuildError, match='leaves out'):
        build(report, strict=True)


@pytest.mark.parametrize(
    ('accept', 'expected'),
    [
        ('text/html,application/xhtml+xml,*/*;q=0.8', 'text/plain'),
        (f'text/html,{RAML_MEDIA_TYPE}', RAML_MEDIA_TYPE),
        ('*/*', RAML_MEDIA_TYPE),
        ('', RAML_MEDIA_TYPE),
    ],
)
def test_a_browser_is_answered_in_text_it_shows(accept: str, expected: str) -> None:
    assert media_type(accept) == expected
