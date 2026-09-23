"""Generators for the five benchmark corpora.

Nothing here is vendored. The corpora are generated (docs/12 § 4): 7000 types
of RAML is megabytes of text nobody reads, and `bench_large` must be
regenerable at half size, because the linearity check needs two points.

Every generator is a pure function of its size arguments. No randomness, no
clock, no environment: a corpus written on one machine is byte-identical to the
same corpus written on the next, so a recorded baseline describes the same input
it was taken on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    'write_endpoints',
    'write_jsonschema',
    'write_large',
    'write_small',
    'write_validate',
]


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8', newline='\n')


# -- types --------------------------------------------------------------------


def _plain(name: str, _ordinal: int, _previous: str) -> str:
    return (
        f'  {name}:\n'
        f'    type: object\n'
        f'    properties:\n'
        f'      id: string\n'
        f'      count: integer\n'
        f'      active: boolean\n'
        f'      created: date-only\n'
    )


def _derived(name: str, _ordinal: int, previous: str) -> str:
    return (
        f'  {name}:\n'
        f'    type: {previous}\n'
        f'    properties:\n'
        f'      label?: string\n'
        f'      weight:\n'
        f'        type: number\n'
        f'        minimum: 0\n'
    )


def _array(name: str, _ordinal: int, previous: str) -> str:
    return f'  {name}: {previous}[]\n'


def _union(name: str, _ordinal: int, previous: str) -> str:
    return f'  {name}: {previous} | string\n'


def _enum(name: str, _ordinal: int, _previous: str) -> str:
    return (
        f'  {name}:\n'
        f'    type: string\n'
        f'    enum: [alpha, beta, gamma, delta, epsilon]\n'
        f'    description: a closed set of five\n'
    )


def _cross_library(name: str, ordinal: int, previous: str) -> str:
    return (
        f'  {name}:\n'
        f'    type: object\n'
        f'    properties:\n'
        f'      key: common.Id\n'
        f'      body?: {previous}\n'
        f'    example:\n'
        f'      key: k-{ordinal}\n'
    )


#: The forms the type generator cycles through. Each exercises a different part
#: of the pipeline — inheritance, the expression parser, unions, enums, and a
#: cross-library reference that only P7 can settle. A corpus of 7000 independent
#: objects would exercise none of them.
_FORMS = (_plain, _derived, _array, _union, _enum, _cross_library)


def _type_block(ordinal: int, previous: str | None) -> str:
    """One type declaration, in the form this ordinal calls for.

    `previous` is the type declared just before it in the same library, which is
    what gives the corpus its inheritance chains and inner references. The first
    type in a library has no predecessor, so it takes the one form that needs
    none.
    """
    name = f'T{ordinal}'
    if previous is None:
        return _plain(name, ordinal, '')
    return _FORMS[ordinal % len(_FORMS)](name, ordinal, previous)


_COMMON = """#%RAML 1.0 Library
types:
  Id:
    type: string
    minLength: 1
    maxLength: 64
  Timestamps:
    type: object
    properties:
      createdAt: datetime
      updatedAt?: datetime
"""


def _library(index: int, type_count: int, *, depth: int) -> str:
    """One library of `type_count` types, `uses:`-ing the shared `common`.

    `depth` is how many directory levels up `common.raml` sits, so the include
    path is relative like a real project's — and so every library reaches the
    *same* file through a different spelling of the path. That is the diamond
    the compose cache exists for (docs/12 § 1); if canonicalisation ever
    regresses, this corpus goes quadratic and the linearity check catches it.
    """
    lines = ['#%RAML 1.0 Library', f'usage: generated library {index}', 'uses:']
    lines.append(f'  common: {"../" * depth}common.raml')
    lines.append('types:')
    previous: str | None = None
    for ordinal in range(type_count):
        lines.append(_type_block(ordinal, previous).rstrip('\n'))
        previous = f'T{ordinal}'
    return '\n'.join(lines) + '\n'


def _api_using(libraries: list[str]) -> str:
    lines = [
        '#%RAML 1.0',
        'title: Generated benchmark API',
        'version: v1',
        'baseUri: https://example.test/{version}',
        'uses:',
    ]
    lines.extend(f'  lib{index}: {path}' for index, path in enumerate(libraries))
    return '\n'.join(lines) + '\n'


def write_small(root: Path, *, type_count: int = 100) -> Path:
    """~100 types in one library: the fixed overhead of a parse."""
    _write(
        root,
        {
            'common.raml': _COMMON,
            'lib/types.raml': _library(0, type_count, depth=1),
            'api.raml': _api_using(['lib/types.raml']),
        },
    )
    return root / 'api.raml'


def write_large(root: Path, *, type_count: int = 7000, library_count: int = 150) -> Path:
    """`type_count` types spread over `library_count` libraries.

    Sized after go-raml's published corpus (7124 types, 148 libraries). Halve
    `type_count` and `library_count` together for the linearity check.
    """
    per_library, remainder = divmod(type_count, library_count)
    files = {'common.raml': _COMMON}
    paths = []
    for index in range(library_count):
        # Two directory levels, so `common.raml` is reached by two spellings.
        path = f'lib/g{index % 10}/l{index}.raml'
        files[path] = _library(index, per_library + (1 if index < remainder else 0), depth=2)
        paths.append(path)
    files['api.raml'] = _api_using(paths)
    _write(root, files)
    return root / 'api.raml'


# -- endpoints ----------------------------------------------------------------

_TRAITS = """
traits:
  paged:
    queryParameters:
      offset?:
        type: integer
        default: 0
      limit?:
        type: integer
        maximum: 100
  filtered:
    queryParameters:
      q?: string
    headers:
      X-Filter-Version?: string
  audited:
    headers:
      X-Request-Id: string
    responses:
      500:
        description: audited failure
"""

_METHODS = ('get', 'post', 'put', 'delete')


def write_endpoints(root: Path, *, resource_count: int = 500) -> Path:
    """`resource_count` resources, four methods each, three traits on each method.

    Measures the two-stage build (docs/12 § 1): 6000 method-level trait
    applications, every one of them a tree merge rather than a model merge.
    """
    lines = [
        '#%RAML 1.0',
        'title: Generated endpoint benchmark',
        'baseUri: https://example.test',
        'types:',
        '  Item:',
        '    type: object',
        '    properties:',
        '      id: string',
        '      name: string',
        _TRAITS.strip('\n'),
    ]
    for index in range(resource_count):
        lines.append(f'/res{index}:')
        lines.append(f'  displayName: Resource {index}')
        for method in _METHODS:
            lines.append(f'  {method}:')
            lines.append('    is: [paged, filtered, audited]')
            lines.append('    responses:')
            lines.append('      200:')
            lines.append('        body:')
            lines.append('          application/json:')
            lines.append('            type: Item')
    _write(root, {'api.raml': '\n'.join(lines) + '\n'})
    return root / 'api.raml'


# -- extensions ---------------------------------------------------------------


def write_extensions(root: Path, *, resource_count: int = 500) -> Path:
    """The endpoints corpus as a root API, under an Overlay and then an Extension.

    The Overlay translates every resource and method, which is its common use,
    and applies an annotation it declares to every method; the Extension on top
    adds a `patch` to every tenth resource and one type. Measures the chain
    load, both merges, the overlay check, and document provenance on a target
    tree whose every method carries nodes from three files (docs/19 § 8).
    """
    write_endpoints(root, resource_count=resource_count)
    overlay = [
        '#%RAML 1.0 Overlay',
        'extends: api.raml',
        'annotationTypes:',
        '  reviewed: boolean',
    ]
    extension = [
        '#%RAML 1.0 Extension',
        'extends: overlay.raml',
        'types:',
        '  Patch:',
        '    type: Item',
        '    properties:',
        '      reason?: string',
    ]
    for index in range(resource_count):
        overlay.append(f'/res{index}:')
        overlay.append(f'  description: Recurso {index}')
        for method in _METHODS:
            overlay.append(f'  {method}:')
            overlay.append(f'    description: Operación {method} sobre el recurso {index}')
            overlay.append('    (reviewed): true')
        if index % 10 == 0:
            extension.append(f'/res{index}:')
            extension.append('  patch:')
            extension.append('    is: [paged]')
            extension.append('    body:')
            extension.append('      application/json:')
            extension.append('        type: Patch')
    _write(root, {'overlay.raml': '\n'.join(overlay) + '\n', 'extension.raml': '\n'.join(extension) + '\n'})
    return root / 'extension.raml'


# -- validation ---------------------------------------------------------------

_EXAMPLE_KEYS = 50


def write_validate(root: Path, *, type_count: int = 1000) -> Path:
    """`type_count` types, each with a `_EXAMPLE_KEYS`-key example. P10's cost."""
    properties = '\n'.join(f'      k{key}: string' for key in range(_EXAMPLE_KEYS))
    lines = ['#%RAML 1.0 Library', 'types:']
    for index in range(type_count):
        example = '\n'.join(f'      k{key}: v{index}-{key}' for key in range(_EXAMPLE_KEYS))
        lines.append(f'  V{index}:')
        lines.append('    type: object')
        lines.append('    properties:')
        lines.append(properties)
        lines.append('    example:')
        lines.append(example)
    _write(root, {'lib.raml': '\n'.join(lines) + '\n'})
    return root / 'lib.raml'


# -- JSON Schema --------------------------------------------------------------


def write_jsonschema(root: Path, *, schema_count: int = 200, shared_count: int = 20) -> Path:
    """`schema_count` schemas over `shared_count` shared `$ref` targets.

    Measures the per-parse registry (docs/10 § 7). Without it each of the
    200 schemas compiles its own copy of the definition it points at, and the
    curve against `shared_count` is flat instead of falling.
    """
    files: dict[str, str] = {}
    for index in range(shared_count):
        files[f'schemas/defs/d{index}.json'] = (
            '{\n'
            '  "type": "object",\n'
            '  "properties": {\n'
            f'    "d{index}Name": {{"type": "string"}},\n'
            f'    "d{index}Size": {{"type": "integer", "minimum": 0}}\n'
            '  },\n'
            f'  "required": ["d{index}Name"]\n'
            '}\n'
        )
    for index in range(schema_count):
        target = index % shared_count
        files[f'schemas/s{index}.json'] = (
            '{\n'
            '  "$schema": "http://json-schema.org/draft-07/schema#",\n'
            '  "type": "object",\n'
            '  "properties": {\n'
            f'    "id": {{"type": "string"}},\n'
            f'    "detail": {{"$ref": "defs/d{target}.json"}},\n'
            f'    "more": {{"$ref": "defs/d{(target + 1) % shared_count}.json"}}\n'
            '  }\n'
            '}\n'
        )
    lines = ['#%RAML 1.0 Library', 'types:']
    lines.extend(f'  S{index}: !include schemas/s{index}.json' for index in range(schema_count))
    files['lib.raml'] = '\n'.join(lines) + '\n'
    _write(root, files)
    return root / 'lib.raml'
