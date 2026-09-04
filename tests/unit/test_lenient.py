"""`parse_lenient` — docs/13-public-api.md section 1.

Two properties, and they pull against each other. It must return a model useful
enough for an editor to keep working on a document mid-edit, and it must not
pretend to return one when there is nothing there.

The interesting assertions are about what *survives* a failure, not about the
error: an error is easy, and a strict parse already produces it.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path, parse_lenient

API = '#%RAML 1.0\ntitle: T\n'
LIB = '#%RAML 1.0 Library\n'
BOTH = ParseOptions(unwrap=True, validate=True)


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


class TestCleanInput:
    def test_a_valid_document_reports_no_error(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T: string\n'})
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is None
        assert raml.entry_point.types['T'].type == 'string'

    def test_the_model_matches_a_strict_parse(self, workspace):
        """Leniency changes what happens on failure, and nothing else."""
        files = {'api.raml': API + 'types:\n  T:\n    type: string\n    minLength: 2\n/r:\n  get:\n'}
        root = workspace(files)
        lenient, error = parse_lenient(root / 'api.raml', BOTH)
        strict = parse_from_path(root / 'api.raml', BOTH)
        assert error is None
        assert list(lenient.endpoints) == list(strict.endpoints)
        assert list(lenient.entry_point.types) == list(strict.entry_point.types)


class TestPartialModel:
    def test_a_bad_example_still_yields_the_endpoints(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  T:\n    type: integer\n    example: not-an-integer\n'
                + '/things:\n  get:\n  post:\n'
            }
        )
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert 'invalid example' in messages(error)
        assert list(raml.endpoints) == ['/things']
        assert sorted(raml.endpoints['/things'].operations) == ['get', 'post']

    def test_an_undeclared_type_still_yields_the_declared_ones(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  Good: string\n  Bad: NoSuchType\n'})
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert raml.entry_point.types['Good'].type == 'string'

    def test_a_decode_time_error_still_yields_an_entry_point(self, workspace):
        """The commonest state an editor sees, and the one that shaped `_FATAL`.

        `minLength: two` fails during P1-P3, before `raml.entry_point` is
        assigned — so an `entry_point is None` test for "nothing to hand back"
        would call this fatal. The fragment was registered before its body was
        decoded, so there is in fact a partial one to return.
        """
        root = workspace({'api.raml': API + 'types:\n  T:\n    type: string\n    minLength: two\n/r:\n  get:\n'})
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert raml.entry_point is not None
        assert raml.entry_point.title.value == 'T'

    def test_a_broken_library_leaves_the_entry_point_readable(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  T: string\n',
                'lib.raml': LIB + 'types:\n  B: NoSuchType\n',
            }
        )
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert raml.entry_point is not None
        assert raml.entry_point.types['T'].type == 'string'


class TestItStopsWhereStrictStops:
    """The design decision, pinned with the measurement that produced it.

    An earlier draft continued to the next pass after one failed, to collect
    the diagnostics a strict parse never reaches. It does not work, because the
    passes consume each other's output: a pass walking state an earlier one
    already reported as broken re-derives the same fault rather than finding a
    new one. One missing library used by twenty types produced **41**
    diagnostics instead of one.

    So the error is exactly what a strict parse would have raised, and the
    model is the difference. Recovering the genuinely independent diagnostics
    means skipping the broken *entities* inside P9 and P10, not the passes;
    that is an After-v1 item in docs/15.
    """

    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'dangling type name': 'types:\n  Bad: NoSuchType\n',
        'dangling used by five': 'types:\n  Bad: NoSuchType\n'
        + ''.join(f'  D{index}:\n    properties:\n      p: Bad\n' for index in range(5)),
        'missing library': 'uses:\n  lib: absent.raml\ntypes:\n'
        + ''.join(f'  D{index}:\n    properties:\n      p: lib.Thing\n' for index in range(5)),
    }

    @pytest.mark.parametrize('body', CASES.values(), ids=list(CASES))
    def test_the_error_matches_a_strict_parse_exactly(self, workspace, body):
        root = workspace({'api.raml': API + body})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', BOTH)
        _, lenient = parse_lenient(root / 'api.raml', BOTH)

        assert lenient is not None
        assert len(list(lenient.chains())) == len(list(caught.value.chains()))
        assert messages(lenient) == messages(caught.value)

    def test_one_missing_library_is_one_diagnostic(self, workspace):
        """The case that settled it, at the size that made it obvious."""
        body = 'uses:\n  lib: absent.raml\ntypes:\n' + ''.join(
            f'  D{index}:\n    properties:\n      p: lib.Thing\n' for index in range(20)
        )
        root = workspace({'api.raml': API + body})
        _, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert len(list(error.chains())) == 1


class TestStillFatal:
    """The four cases docs/13 § 1 keeps fail-fast: nothing to hand back."""

    def test_an_unreadable_entry_file_raises(self, workspace):
        root = workspace({'api.raml': API})
        with pytest.raises(RamlError) as caught:
            parse_lenient(root / 'absent.raml')
        assert 'load resource' in messages(caught.value)

    def test_a_missing_header_raises(self, workspace):
        root = workspace({'api.raml': 'title: no header here\n'})
        with pytest.raises(RamlError) as caught:
            parse_lenient(root / 'api.raml')
        assert 'unknown fragment kind' in messages(caught.value)

    def test_a_non_mapping_root_raises(self, workspace):
        root = workspace({'api.raml': API.splitlines()[0] + '\n- a\n- b\n'})
        with pytest.raises(RamlError) as caught:
            parse_lenient(root / 'api.raml')
        assert 'must be map' in messages(caught.value)

    def test_an_unsupported_fragment_kind_raises(self, workspace):
        root = workspace({'api.raml': '#%RAML 1.0 Overlay\nextends: base.raml\ntitle: T\n'})
        with pytest.raises(RamlError) as caught:
            parse_lenient(root / 'api.raml')
        assert 'fragment kind not supported' in messages(caught.value)

    def test_the_same_problem_in_an_included_file_is_not_fatal(self, workspace):
        """`_FATAL` matches the *head* of the error, and that is the point.

        A library whose root is a sequence is one bad file; the entry document
        is still worth handing back. Matching anywhere in the chain would make
        every included file's syntax error abandon the parse.
        """
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  T: string\n',
                'lib.raml': LIB + '- a\n- b\n',
            }
        )
        raml, error = parse_lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert 'must be map' in messages(error)
        assert raml.entry_point.types['T'].type == 'string'
