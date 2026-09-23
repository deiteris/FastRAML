"""Microbenchmarks: one leaf function, called in a loop, at the sizes that matter.

A corpus measures what a document costs, and a leaf function's cost there is
diluted by everything around it, or missed when no corpus reaches it. Where the
question is the function's constant factor, only timing the function answers
it (docs/12 § 4).

Targets are looked up by name when a case runs, not imported at module load,
so the same cases run against an older checkout: a target that does not exist
there is reported as missing rather than failing the run.
"""

from __future__ import annotations

import importlib
import timeit
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['CASES', 'Case', 'run_micro']

#: Sizes from a handful to a thousand, where a per-value constant shows.
SIZES: tuple[int, ...] = (5, 20, 100, 1000)


class _Node:
    """What `_is_subset` reads from a `DataNode`: `raw`, and nothing else."""

    __slots__ = ('raw',)

    def __init__(self, raw: Any) -> None:
        self.raw = raw


def _strings(size: int) -> list[Any]:
    return [f'code{index}' for index in range(size)]


def _numbers(size: int) -> list[Any]:
    # Integers and floats alternate, so `1` meets `1.0` on the semantic path.
    return [index if index % 2 else float(index) for index in range(size)]


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    #: `module:attribute` of the function under test.
    target: str
    #: Builds the zero-argument call to time, given the resolved function.
    build: Callable[[Callable[..., Any]], Callable[[], Any]]


def _subset(values: Callable[[int], list[Any]], size: int) -> Callable[[Callable[..., Any]], Callable[[], Any]]:
    source = [_Node(value) for value in values(size)]
    target = source[::2]
    return lambda function: lambda: function(target, source)


def _unique(values: Callable[[int], list[Any]], size: int) -> Callable[[Callable[..., Any]], Callable[[], Any]]:
    items = values(size)
    return lambda function: lambda: function(items)


def _membership(kind: str, size: int) -> Callable[[Callable[..., Any]], Callable[[], Any]]:
    """`validate_at` on a declared enum, for its last value: a scan's far end."""

    def build(function: Callable[..., Any]) -> Callable[[], Any]:
        import tempfile  # noqa: PLC0415 - only when a membership case runs

        from fastraml import ParseOptions, parse_from_string  # noqa: PLC0415 - the package under test

        values: list[Any] = [f'code{index}' for index in range(size)] if kind == 'str' else list(range(size))
        spelled = ', '.join(str(value) for value in values)
        document = f'#%RAML 1.0 Library\ntypes:\n  T:\n    type: {_KIND_NAMES[kind]}\n    enum: [{spelled}]\n'
        raml = parse_from_string(
            document, file_name='lib.raml', base_dir=tempfile.gettempdir(), options=ParseOptions(unwrap=True)
        )
        shape = raml.types_in(raml.location)['T']
        last = values[-1]
        return lambda: function(shape, last, '$')

    return build


_KIND_NAMES: dict[str, str] = {'str': 'string', 'num': 'integer'}


CASES: tuple[Case, ...] = (
    Case('same_value str', 'fastraml.types.values:same_value', lambda f: lambda: f('code1', 'code2')),
    Case('same_value int/float', 'fastraml.types.values:same_value', lambda f: lambda: f(1, 1.0)),
    *(
        Case(f'enum subset {kind} n={size}', 'fastraml.types.inherit:_is_subset', _subset(values, size))
        for kind, values in (('str', _strings), ('num', _numbers))
        for size in SIZES
    ),
    *(
        Case(f'uniqueItems {kind} n={size}', 'fastraml.types.values:unique_items', _unique(values, size))
        for kind, values in (('str', _strings), ('num', _numbers))
        for size in SIZES
    ),
    *(
        Case(f'enum membership {kind} n={size}', 'fastraml.types.base:BaseShape.validate_at', _membership(kind, size))
        for kind in ('str', 'num')
        for size in SIZES
    ),
)


def _resolve(target: str) -> Callable[..., Any] | None:
    module_name, _, path = target.partition(':')
    try:
        found: Any = importlib.import_module(module_name)
    except ImportError:
        return None
    for attribute in path.split('.'):
        found = getattr(found, attribute, None)
        if found is None:
            return None
    return found


def run_micro(pattern: str = '', *, repeat: int = 7) -> dict[str, float | None]:
    """Seconds per call for each case whose name contains `pattern`, best of `repeat`.

    `autorange` sizes each repeat to at least 0.2 s, so a fast call is timed in
    bulk rather than against the clock's resolution.
    """
    results: dict[str, float | None] = {}
    for case in CASES:
        if pattern not in case.name:
            continue
        function = _resolve(case.target)
        if function is None:
            results[case.name] = None
            continue
        timer = timeit.Timer(case.build(function))
        number, _ = timer.autorange()
        results[case.name] = min(timer.repeat(repeat=repeat, number=number)) / number
    return results
