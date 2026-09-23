"""The diagnostic model: chain composition, accumulation, and rendering.

See docs/11-diagnostics.md.
"""

from __future__ import annotations

import copy
import pickle
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction

import pytest

from fastraml.errors import Accumulator, ErrorKind, RamlError, Trace
from fastraml.positions import Position

LOC = 'file:///t/api.raml'
POS = Position(17, 10, 17, 14)


class TestChains:
    def test_new_starts_a_single_frame_chain(self):
        err = RamlError.new('cannot inherit from different type', LOC, POS)
        assert [f.message for f in err.frames()] == ['cannot inherit from different type']

    def test_wrap_pushes_a_frame_outermost_first(self):
        inner = RamlError.new('cannot inherit from different type', LOC, POS)
        outer = RamlError.wrap('merge shapes', inner, LOC, POS)
        assert [f.message for f in outer.frames()] == ['merge shapes', 'cannot inherit from different type']

    def test_wrapping_a_plain_exception_keeps_its_text_innermost(self):
        err = RamlError.wrap('load resource', OSError('no such file'), LOC, kind=ErrorKind.LOADING)
        assert [f.message for f in err.frames()] == ['load resource', 'no such file']
        assert err.frames()[-1].location == LOC

    def test_wrap_preserves_siblings(self):
        first = RamlError.new('bad response', LOC)
        combined = first.append(RamlError.new('bad header', LOC))
        wrapped = RamlError.wrap('decode operation', combined, LOC)
        assert len(list(wrapped.chains())) == 2

    def test_messages_reports_the_innermost_problem_of_every_chain(self):
        err = RamlError.wrap('merge shapes', RamlError.new('root cause', LOC), LOC)
        err = err.append(RamlError.new('other problem', LOC))
        assert err.messages() == ['root cause', 'other problem']


class TestAppendIsImmutable:
    def test_append_returns_a_new_error(self):
        original = RamlError.new('first', LOC)
        extended = original.append(RamlError.new('second', LOC))
        assert original is not extended
        assert original.siblings == ()
        assert len(extended.siblings) == 1

    def test_appending_none_is_a_no_op(self):
        original = RamlError.new('first', LOC)
        assert original.append(None) is original


class TestInfo:
    def test_info_is_rendered_after_the_message(self):
        # Variable values stay out of `message` so diagnostics group cleanly and
        # tests can match on the message alone. See docs/11 § 6.
        err = RamlError.new('cannot redefine built-in type', LOC, POS, info={'type': 'string'})
        assert err.head.message == 'cannot redefine built-in type'
        assert err.head.rendered_message() == 'cannot redefine built-in type: type: string'

    def test_a_frame_without_info_renders_unchanged(self):
        assert Trace('plain', LOC).rendered_message() == 'plain'


class TestWhere:
    def test_position_is_appended_to_the_location(self):
        assert Trace('m', LOC, POS).where() == f'{LOC}:17:10'

    def test_a_frame_without_a_position_reports_the_file_alone(self):
        assert Trace('m', LOC).where() == LOC


class TestRendering:
    def test_str_lists_every_chain(self):
        err = RamlError.wrap('unwrap shapes', RamlError.new('root cause', LOC, POS), LOC)
        err = err.append(RamlError.new('second problem', LOC, POS))
        text = str(err)
        assert '[0]' in text
        assert '[1]' in text
        assert 'root cause' in text
        assert 'second problem' in text

    def test_to_dict_matches_the_reference_shape(self):
        # The shape mirrors go-raml's JSON so fixtures can be diffed between the
        # two implementations. See docs/14-testing.md.
        err = RamlError.wrap(
            'unwrap shapes',
            RamlError.new('cannot inherit from different type', LOC, POS, kind=ErrorKind.UNWRAPPING),
            LOC,
            kind=ErrorKind.PARSING,
        )
        payload = err.to_dict()
        assert list(payload) == ['traces']
        stack = payload['traces'][0]['stack']
        assert [f['message'] for f in stack] == ['unwrap shapes', 'cannot inherit from different type']
        assert stack[0]['type'] == 'parsing'
        assert stack[1]['type'] == 'unwrapping'
        assert all(f['severity'] == 'error' for f in stack)
        assert stack[1]['position'] == f'{LOC}:17:10'

    def test_exception_message_is_the_head(self):
        err = RamlError.new('title is required', LOC, POS)
        assert str(err.args[0]) == 'title is required'

    def test_the_message_is_rendered_when_read_with_its_info(self):
        # Rendered on demand (docs/12 § 2), so `args` and `repr` still carry
        # what `Exception` would have held.
        err = RamlError.new('value is too long', LOC, POS, info={'maxLength': 3})
        assert err.args == ('value is too long: maxLength: 3',)
        assert repr(err) == "RamlError('value is too long: maxLength: 3')"


def _chained() -> RamlError:
    """Two frames, a sibling, a position, and a `Fraction` in `info`."""
    inner = RamlError.new(
        'value is below the minimum',
        LOC,
        POS,
        kind=ErrorKind.VALIDATING,
        info={'path': '$.x', 'minimum': Fraction(1, 10)},
    )
    outer = RamlError.wrap('value matches no member of the union', inner, LOC, Position(1, 1))
    return outer.append(RamlError.new('title is required', 'file:///t/other.raml'))


def _raise_chained() -> None:
    raise _chained()


class TestPickling:
    """docs/11 § 1: an error survives a process boundary and `copy`."""

    def test_a_chain_with_siblings_round_trips(self):
        err = _chained()
        back = pickle.loads(pickle.dumps(err))  # noqa: S301 - our own bytes
        assert type(back) is RamlError
        assert back.to_dict() == err.to_dict()
        assert [frame.position for frame in back.frames()] == [Position(1, 1), POS]
        assert back.frames()[1].kind is ErrorKind.VALIDATING
        assert back.frames()[1].info == {'path': '$.x', 'minimum': Fraction(1, 10)}
        assert len(back.siblings) == 1

    def test_copy_keeps_the_chain(self):
        err = _chained()
        assert copy.copy(err).to_dict() == err.to_dict()

    def test_an_error_raised_in_a_worker_reaches_the_parent(self):
        with ProcessPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_raise_chained)
            with pytest.raises(RamlError) as caught:
                future.result()
        assert caught.value.to_dict() == _chained().to_dict()


class TestAccumulator:
    def test_empty_accumulator_yields_nothing(self):
        acc = Accumulator()
        assert not acc
        assert acc.result() is None
        acc.raise_if_any()  # must not raise

    def test_none_is_ignored_so_callers_need_no_guard(self):
        acc = Accumulator()
        acc.add(None)
        assert acc.result() is None

    def test_a_single_error_is_returned_unchanged(self):
        acc = Accumulator()
        only = RamlError.new('one', LOC)
        acc.add(only)
        assert acc.result() is only

    def test_several_errors_become_siblings_of_the_first(self):
        acc = Accumulator()
        acc.add(RamlError.new('first', LOC))
        acc.add(RamlError.new('second', LOC))
        acc.add(RamlError.new('third', LOC))
        result = acc.result()
        assert result is not None
        assert result.messages() == ['first', 'second', 'third']
        assert len(acc) == 3

    def test_raise_if_any_raises_the_combined_error(self):
        acc = Accumulator()
        acc.add(RamlError.new('first', LOC))
        acc.add(RamlError.new('second', LOC))
        with pytest.raises(RamlError) as excinfo:
            acc.raise_if_any()
        assert excinfo.value.messages() == ['first', 'second']

    def test_existing_siblings_are_preserved_when_combining(self):
        acc = Accumulator()
        acc.add(RamlError.new('a', LOC).append(RamlError.new('b', LOC)))
        acc.add(RamlError.new('c', LOC))
        result = acc.result()
        assert result is not None
        assert result.messages() == ['a', 'b', 'c']
