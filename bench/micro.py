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


def _declared(types: str, name: str) -> Any:
    """Type `name` from a library whose `types:` block is `types`, unwrapped."""
    import tempfile  # noqa: PLC0415 - only when a validation case runs

    from fastraml import ParseOptions, parse_from_string  # noqa: PLC0415 - the package under test

    raml = parse_from_string(
        f'#%RAML 1.0 Library\ntypes:\n{types}',
        file_name='lib.raml',
        base_dir=tempfile.gettempdir(),
        options=ParseOptions(unwrap=True),
    )
    return raml.types_in(raml.location)[name]


def _validating(types: str, name: str, value: Any) -> Callable[[Callable[..., Any]], Callable[[], Any]]:
    """`BaseShape.validate` of `value` against `name`: what a request handler calls."""

    def build(function: Callable[..., Any]) -> Callable[[], Any]:
        shape = _declared(types, name)
        return lambda: function(shape, value)

    return build


def _object_types(size: int) -> str:
    properties = ''.join(f'      p{index}: string\n' for index in range(size))
    return f'  O:\n    properties:\n{properties}'


def _object_value(size: int) -> dict[str, Any]:
    return {f'p{index}': f'v{index}' for index in range(size)}


def _union_types(width: int, *, discriminated: bool) -> str:
    """`width` object members, told apart by a required property each, or by `kind`."""
    blocks = []
    for member in range(width):
        tag = f'    discriminatorValue: m{member}\n' if discriminated else ''
        blocks.append(f'  M{member}:\n    type: B\n{tag}    properties:\n      f{member}: integer\n')
    base = '  B:\n    type: object\n' + (
        '    discriminator: kind\n    properties:\n      kind: string\n' if discriminated else ''
    )
    members = ' | '.join(f'M{member}' for member in range(width))
    return base + ''.join(blocks) + f'  U:\n    type: {members}\n'


def _union_value(width: int, *, discriminated: bool) -> dict[str, Any]:
    """A value only the last member admits: the far end of a scan."""
    last = width - 1
    return {'kind': f'm{last}', f'f{last}': 1} if discriminated else {f'f{last}': 1}


_VALIDATE = 'fastraml.types.base:BaseShape.validate'
_SCALAR_TYPES = (
    '  S:\n    type: string\n    pattern: ^[a-z]+-\\d+$\n    maxLength: 64\n'
    '  I:\n    type: integer\n    minimum: 0\n    maximum: 1000000\n    format: int32\n'
    '  N:\n    type: number\n    minimum: 0\n    multipleOf: 0.01\n'
    '  D:\n    type: date-only\n'
    '  T:\n    type: datetime\n'
)


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
    Case('validate string pattern', _VALIDATE, _validating(_SCALAR_TYPES, 'S', 'abc-123')),
    Case('validate integer bounds', _VALIDATE, _validating(_SCALAR_TYPES, 'I', 4242)),
    Case('validate number multipleOf', _VALIDATE, _validating(_SCALAR_TYPES, 'N', 12.34)),
    Case('validate date-only', _VALIDATE, _validating(_SCALAR_TYPES, 'D', '2024-01-02')),
    Case('validate datetime', _VALIDATE, _validating(_SCALAR_TYPES, 'T', '2024-01-02T03:04:05Z')),
    *(
        Case(f'validate object n={size}', _VALIDATE, _validating(_object_types(size), 'O', _object_value(size)))
        for size in SIZES
    ),
    *(
        # Every property missing: the failure path, which builds a diagnostic.
        Case(f'validate object missing n={size}', _VALIDATE, _validating(_object_types(size), 'O', {}))
        for size in SIZES
    ),
    *(
        Case(
            f'validate pattern properties n={size}',
            _VALIDATE,
            _validating(
                '  P:\n    properties:\n      /^x-/: string\n      /^y-/: integer\n',
                'P',
                {f'y-{index}': index for index in range(size)},
            ),
        )
        for size in SIZES
    ),
    *(
        Case(
            f'validate array of objects n={size}',
            _VALIDATE,
            _validating(_object_types(5) + '  A:\n    type: O[]\n', 'A', [_object_value(5)] * size),
        )
        for size in SIZES
    ),
    *(
        Case(
            f'validate union{" discriminated" if tagged else ""} width={width}',
            _VALIDATE,
            _validating(_union_types(width, discriminated=tagged), 'U', _union_value(width, discriminated=tagged)),
        )
        for tagged in (False, True)
        for width in (2, 4, 8)
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
