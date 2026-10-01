"""Generators for the benchmark corpora.

Nothing here is vendored. The corpora are generated (docs/12 § 4): 7000 types
of RAML is megabytes of text nobody reads, and `bench_large` must be
regenerable at half size, because the linearity check needs two points.

Every generator is a pure function of its size arguments. No randomness, no
clock, no environment: a corpus written on one machine is byte-identical to the
same corpus written on the next, so a recorded baseline describes the same input
it was taken on.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    'ENUM_SIZES',
    'FACET_PARENTS',
    'INHERITED_UNION_WIDTHS',
    'UNION_WIDTHS',
    'UNIQUE_LENGTHS',
    'write_annotation_targets',
    'write_endpoints',
    'write_enums',
    'write_facets',
    'write_include_content',
    'write_includes',
    'write_inheritance',
    'write_inline_json',
    'write_jsonschema',
    'write_large',
    'write_reference_namespaces',
    'write_schema_allof',
    'write_small',
    'write_template_scopes',
    'write_templates',
    'write_unions',
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


# -- templates ----------------------------------------------------------------

_RESOURCE_TYPES = """
resourceTypes:
  collection:
    description: Every <<resourcePathName | !singularize>> in the store
    get:
      is: [searchable: {field: <<resourcePathName | !singularize>>Name}]
      description: List <<resourcePathName | !pluralize>>
      responses:
        200:
          body:
            application/json:
              type: <<resourcePathName | !singularize | !uppercamelcase>>[]
    post:
      description: Create one <<resourcePathName | !singularize>>
      body:
        application/json:
          type: <<resourcePathName | !singularize | !uppercamelcase>>
  item:
    description: One <<item>>, at <<resourcePath>>
    get:
      responses:
        200:
          body:
            application/json:
              type: <<item>>
    put:
      body:
        application/json:
          type: <<item>>
    delete?:
traits:
  searchable:
    queryParameters:
      <<field>>:
        description: Filter on <<field | !lowerhyphencase>>
        required: false
"""


def write_reference_namespaces(root: Path, *, resource_count: int = 500) -> Path:
    """Caller names and forwarded arguments collide with library declarations (docs/08 § 4.2)."""
    api = (
        '#%RAML 1.0\ntitle: Generated reference namespace benchmark\nuses:\n  lib: lib.raml\n'
        'types:\n  Model: integer\nannotationTypes:\n  dynamic: string\n  fixed: integer\n'
        'securitySchemes:\n  chosen:\n    type: Basic Authentication\n'
        'traits:\n  chosen:\n    description: caller\n'
        'resourceTypes:\n  parent:\n    get:\n      is:\n'
        '        - lib.wrapper: {trait: chosen, scheme: chosen, model: Model, annotation: dynamic, value: marker}\n'
    )
    library = (
        '#%RAML 1.0 Library\ntypes:\n  Model: string\n'
        'annotationTypes:\n  dynamic: string\n  fixed: string\n'
        'securitySchemes:\n  chosen:\n    type: Digest Authentication\n'
        'resourceTypes:\n  parent:\n    get:\n      description: library\n  wrapper:\n    type: <<parent>>\n'
        'traits:\n  chosen:\n    description: library\n  inner:\n    queryString: <<model>>\n'
        '  wrapper:\n    is: [{<<trait>>: {}}, inner: {model: <<model>>}]\n'
        '    securedBy: [{<<scheme>>: {}}]\n    (<<annotation>>): marker\n    (fixed): <<value>>\n'
    )
    api += ''.join(f'/items{index}:\n  type: {{lib.wrapper: {{parent: parent}}}}\n' for index in range(resource_count))
    _write(root, {'api.raml': api, 'lib.raml': library})
    return root / 'api.raml'


def write_template_scopes(root: Path, *, resource_count: int = 500) -> Path:
    """Library-owned security and annotated template parameters (docs/09 § A6, § B4)."""
    library = (
        '#%RAML 1.0 Library\n'
        'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
        'annotationTypes:\n  ref:\n    type: string\n    allowedTargets: TypeDeclaration\n'
        'traits:\n  filtered: !include filtered.yaml\n  imported: !include imported.raml\n'
        'resourceTypes:\n  secured: !include secured.yaml\n'
    )
    lines = ['#%RAML 1.0', 'title: Generated template scope benchmark', 'uses:', '  lib: lib.raml']
    lines.extend(f'/items{index}:\n  type: lib.secured' for index in range(resource_count))
    _write(
        root,
        {
            'api.raml': '\n'.join(lines) + '\n',
            'lib.raml': library,
            'filtered.yaml': 'queryParameters:\n  id?:\n    type: string\n    (ref): value\n',
            'secured.yaml': 'get:\n  securedBy: [basic]\n  is: [filtered]\npost:\n  is: [imported]\n',
            'imported.raml': '#%RAML 1.0 Trait\nuses:\n  auth: auth.raml\nsecuredBy: [auth.basic]\n',
            'auth.raml': '#%RAML 1.0 Library\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n',
        },
    )
    return root / 'api.raml'


def write_templates(root: Path, *, resource_count: int = 250) -> Path:
    """Resource types and a trait with parameters and transforms, as real APIs write them.

    `endpoints` applies traits without parameters and no resource type, so it
    never runs resource type application, parameter substitution in a
    resource type, or any of the ten transforms (docs/08 § 5). Each resource
    here is a `collection` whose name every transform reads, and an `{id}` child
    whose `item` parameter names its type. `!singularize` and `!pluralize` run
    several times per resource on the same name, which is how the
    `resourcePathName` of one resource is used. `tests/bench/test_corpus.py`
    pins that both are reached at every resource.
    """
    lines = ['#%RAML 1.0', 'title: Generated template benchmark', _RESOURCE_TYPES.strip('\n'), 'types:']
    lines.extend(
        f'  W{index}widget:\n    properties:\n      id: string\n      w{index}widgetName: string'
        for index in range(resource_count)
    )
    for index in range(resource_count):
        lines.append(f'/w{index}widgets:')
        lines.append('  type: collection')
        lines.append('  /{id}:')
        lines.append(f'    type: {{item: {{item: W{index}widget}}}}')
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


# -- inline JSON --------------------------------------------------------------


def write_inline_json(root: Path, *, type_count: int = 1000) -> Path:
    """JSON-encoded strings at five data-value roots per type (docs/03 § 6)."""
    lines = [
        '#%RAML 1.0',
        'title: Generated inline JSON benchmark',
        'annotationTypes:',
        '  literal: string',
        'types:',
        '  Base:',
        '    type: string',
        '    facets:',
        '      literal: string',
    ]
    for index in range(type_count):
        encoded = "'" + json.dumps(f'{{"attr":{index}}}') + "'"
        lines.extend(
            (
                f'  T{index}:',
                '    type: Base',
                f'    enum: [{encoded}]',
                f'    default: {encoded}',
                '    example:',
                f'      value: {encoded}',
                f'    (literal): {encoded}',
                f'    literal: {encoded}',
            )
        )
    _write(root, {'api.raml': '\n'.join(lines) + '\n'})
    return root / 'api.raml'


# -- enums --------------------------------------------------------------------

#: Enum sizes from a handful to a thousand, so a per-value constant in the
#: subset check shows, and the curve with it.
ENUM_SIZES: tuple[int, ...] = (5, 20, 100, 1000)

#: `uniqueItems` example lengths, on the same principle.
UNIQUE_LENGTHS: tuple[int, ...] = (10, 50, 500)


#: One kind per family, in turn. Numbers are the case semantic equality exists
#: for (`1` and `1.0` are one value, docs/10 § 5), so a corpus of strings
#: alone would measure its cheapest path only.
_ENUM_KINDS: tuple[str, ...] = ('string', 'integer', 'number')


def _enum_values(kind: str, size: int, family: int) -> list[str]:
    """`size` distinct values of `kind`, as their YAML spelling."""
    match kind:
        case 'string':
            return [f'c{family}x{index}' for index in range(size)]
        case 'integer':
            return [str(family * 10000 + index) for index in range(size)]
        case _:
            return [f'{family}{index}.5' for index in range(size)]


def write_enums(root: Path, *, family_count: int = 40) -> Path:
    """Enum narrowing, enum membership and `uniqueItems`: semantic equality.

    Each family is a parent enum of every size in `ENUM_SIZES`, a child keeping
    half of it and a grandchild keeping a quarter, so P9 runs the subset check
    of docs/07 § 4 on two edges per size, and P10 checks the grandchild's
    example against its enum. Each family also declares one
    `uniqueItems` array per length in `UNIQUE_LENGTHS`, whose example P10
    checks. `tests/bench/test_corpus.py` pins that both paths are reached.
    """
    lines = ['#%RAML 1.0 Library', 'types:']
    for family in range(family_count):
        kind = _ENUM_KINDS[family % len(_ENUM_KINDS)]
        for size in ENUM_SIZES:
            values = _enum_values(kind, size, family)
            stem = f'F{family}S{size}'
            lines.append(f'  {stem}Parent:\n    type: {kind}\n    enum: [{", ".join(values)}]')
            lines.append(f'  {stem}Child:\n    type: {stem}Parent\n    enum: [{", ".join(values[::2])}]')
            kept = values[::4]
            # The example is the last value: the far end of a linear scan.
            lines.append(
                f'  {stem}Grandchild:\n    type: {stem}Child\n    enum: [{", ".join(kept)}]\n    example: {kept[-1]}'
            )
        for length in UNIQUE_LENGTHS:
            items = _enum_values(kind, length, family)
            lines.append(
                f'  F{family}U{length}:\n    type: {kind}[]\n    uniqueItems: true\n    example: [{", ".join(items)}]'
            )
    _write(root, {'lib.raml': '\n'.join(lines) + '\n'})
    return root / 'lib.raml'


# -- unions -------------------------------------------------------------------

#: Member counts per union, from the common two to a wide eight.
UNION_WIDTHS: tuple[int, ...] = (2, 4, 8)

#: Values each member admits for the restated enum property.
_UNION_ENUM: int = 10


def write_unions(root: Path, *, family_count: int = 60) -> Path:
    """Facets beside a union: built once, handed to every member (docs/07 § 5).

    Each family declares, per width in `UNION_WIDTHS`, that many object members,
    each admitting its own `_UNION_ENUM` values for `code`. A union of them adds
    a property beside the union and restates `code` with one value from each
    member, so every member keeps a different slice. A nested union and a union
    of arrays with `items:` beside it cover the other two paths.
    `tests/bench/test_corpus.py` pins that each is reached.
    """
    lines = ['#%RAML 1.0 Library', 'types:']
    for family in range(family_count):
        for width in UNION_WIDTHS:
            stem = f'F{family}W{width}'
            members = []
            picked = []
            for member in range(width):
                name = f'{stem}M{member}'
                values = [f'm{member}v{index}' for index in range(_UNION_ENUM)]
                lines.append(
                    f'  {name}:\n    properties:\n      id: string\n'
                    f'      code:\n        type: string\n        enum: [{", ".join(values)}]'
                )
                members.append(name)
                picked.append(values[family % _UNION_ENUM])
            beside = (
                f'    properties:\n      note?: string\n'
                f'      code:\n        type: string\n        enum: [{", ".join(picked)}]'
            )
            lines.append(f'  {stem}U:\n    type: {" | ".join(members)}\n{beside}')
            # Two members cannot nest; wider unions nest all but the first.
            nested = (
                ' | '.join(members) if width == min(UNION_WIDTHS) else f'{members[0]} | ({" | ".join(members[1:])})'
            )
            lines.append(f'  {stem}N:\n    type: {nested}\n{beside}')
        lines.append(f'  F{family}A: string[]\n  F{family}B: string[]')
        lines.append(f'  F{family}AU:\n    type: F{family}A | F{family}B\n    items:\n      maxLength: 16')
    _write(root, {'lib.raml': '\n'.join(lines) + '\n'})
    return root / 'lib.raml'


# -- custom facets ------------------------------------------------------------

#: Parent counts for multiple inheritance.
FACET_PARENTS: tuple[int, ...] = (2, 4, 8)


def write_facets(root: Path, *, family_count: int = 150) -> Path:
    """Custom facets declared up every parent of a multiply-inheriting type.

    Each family declares, per count in `FACET_PARENTS`, that many parents, each
    two levels below a root declaring one required facet, and a child
    inheriting from all of them that supplies every facet. A diamond, two
    parents sharing one root, covers the visited set. P10 walks every parent
    (docs/10 § 4); `tests/bench/test_corpus.py` pins that it does.
    """
    lines = ['#%RAML 1.0 Library', 'types:']
    for family in range(family_count):
        for count in FACET_PARENTS:
            stem = f'F{family}P{count}'
            parents = []
            supplied = []
            for parent in range(count):
                facet = f'f{parent}'
                lines.append(f'  {stem}R{parent}:\n    type: object\n    facets:\n      {facet}: integer')
                lines.append(f'  {stem}I{parent}:\n    type: {stem}R{parent}\n    {facet}: {parent}')
                lines.append(f'  {stem}Q{parent}:\n    type: {stem}I{parent}')
                parents.append(f'{stem}Q{parent}')
                supplied.append(f'    {facet}: {parent + 1}')
            lines.append(f'  {stem}C:\n    type: [{", ".join(parents)}]\n' + '\n'.join(supplied))
        root_name = f'F{family}D'
        lines.append(f'  {root_name}:\n    type: object\n    facets:\n      shared?: integer')
        lines.append(f'  {root_name}L:\n    type: {root_name}\n  {root_name}R:\n    type: {root_name}')
        lines.append(f'  {root_name}C:\n    type: [{root_name}L, {root_name}R]\n    shared: 1')
    _write(root, {'lib.raml': '\n'.join(lines) + '\n'})
    return root / 'lib.raml'


# -- unions among the parents -------------------------------------------------

#: Members of the union each multiply-inheriting type takes a parent from.
INHERITED_UNION_WIDTHS: tuple[int, ...] = (2, 4)


def _inherited_parent(name: str, own: str, bound: str) -> str:
    """A parent declaring what every other parent declares too, bounded its own way."""
    return (
        f'  {name}:\n    properties:\n      {own}: string\n'
        f'      tag:\n        type: string\n        {bound}\n'
        f'      /^x-/:\n        type: string\n        {bound}\n'
        f'      list:\n        type: array\n        items:\n          properties:\n'
        f'            code:\n              type: string\n              {bound}'
    )


def write_inheritance(root: Path, *, family_count: int = 150) -> Path:
    """Unions among a type's parents, and declarations two parents both make.

    Each family declares two parents, `H` and `O`, and per width in
    `INHERITED_UNION_WIDTHS` that many members. Every one of them declares
    `tag`, a `/^x-/` pattern property and `list` of items with `code`, each
    bounded differently, so every merge of two of them folds the like-named
    declarations (docs/07 § 4). The types inheriting from them take a union
    after an object, a union first, and a union of `H | O` with the members,
    which pairs each member with each of the two (docs/07 § 5).
    `tests/bench/test_corpus.py` pins that each path is reached.
    """
    lines = ['#%RAML 1.0 Library', 'types:']
    for family in range(family_count):
        stem = f'F{family}'
        lines.append(_inherited_parent(f'{stem}H', 'home', 'maxLength: 64'))
        lines.append(_inherited_parent(f'{stem}O', 'farm', 'minLength: 1'))
        lines.append(f'  {stem}Both:\n    type: [{stem}H, {stem}O]')
        for width in INHERITED_UNION_WIDTHS:
            members = [f'{stem}W{width}M{member}' for member in range(width)]
            for member, name in enumerate(members):
                lines.append(_inherited_parent(name, f'm{member}', f'pattern: ^m{member}'))
            union = ' | '.join(members)
            lines.append(f'  {stem}W{width}After:\n    type: [{stem}H, {union}]')
            lines.append(f'  {stem}W{width}First:\n    type: [{union}, {stem}H]')
            lines.append(f'  {stem}W{width}Pairs:\n    type: [{stem}H | {stem}O, {union}]')
    _write(root, {'lib.raml': '\n'.join(lines) + '\n'})
    return root / 'lib.raml'


# -- JSON Schema --------------------------------------------------------------

#: Examples per schema: about what a real API with example-rich schemas holds.
EXAMPLES_PER_SCHEMA: int = 5


def write_jsonschema(root: Path, *, schema_count: int = 200, shared_count: int = 20) -> Path:
    """`schema_count` schemas over `shared_count` shared `$ref` targets, each
    with `EXAMPLES_PER_SCHEMA` examples validated through its references.

    Measures the per-parse registry (docs/10 § 7). Without it each of the
    200 schemas compiles its own copy of the definition it points at, and the
    curve against `shared_count` is flat instead of falling. The examples
    measure validation across files, which resolves each `$ref` again.
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
    for index in range(schema_count):
        target, more = index % shared_count, (index % shared_count + 1) % shared_count
        lines += [f'  S{index}:', f'    type: !include schemas/s{index}.json', '    examples:']
        lines += [
            f'      e{number}: {{"id": "i{number}", "detail": {{"d{target}Name": "n", "d{target}Size": {number}}}, '
            f'"more": {{"d{more}Name": "m"}}}}'
            for number in range(EXAMPLES_PER_SCHEMA)
        ]
    files['lib.raml'] = '\n'.join(lines) + '\n'
    _write(root, files)
    return root / 'lib.raml'


def write_schema_allof(root: Path, *, schema_count: int = 200) -> Path:
    """Intersect bounds, enums and child declarations across independently shared refs."""
    files = {
        'base.json': json.dumps({'definitions': {'error': {'type': 'object'}}}),
        'record.json': json.dumps({'type': 'object', 'properties': {'code': {'type': 'string'}}}),
        'limit.json': json.dumps({'type': 'number', 'minimum': 5}),
        'node.json': json.dumps({'type': 'object', 'properties': {'next': {'$ref': '#'}}}),
    }
    lines = [
        '#%RAML 1.0 Library',
        'types:',
        '  Base: !include base.json',
        '  Record: !include record.json',
        '  Limit: !include limit.json',
        '  Node: !include node.json',
    ]
    definitions: dict = {'required0': {'required': ['code']}}
    for level in range(1, 9):
        definitions[f'required{level}'] = {'allOf': [{'$ref': f'#/definitions/required{level - 1}'}] * 2}
    for index in range(schema_count):
        neutral = ({'$ref': 'base.json'}, {}, True)[index % 3]
        members = [
            {
                'type': 'object',
                'properties': {
                    'extra': {'type': 'boolean'},
                    'amount': {'type': 'number', 'minimum': 0, 'multipleOf': 2},
                    'tags': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
                    'limit': {'$ref': 'limit.json'},
                    'node': {'$ref': 'node.json'},
                },
            },
            {'$ref': 'record.json'},
            {
                'properties': {
                    'code': {'type': 'string', 'enum': [f'v{index}']},
                    'amount': {'minimum': 5, 'maximum': 20, 'multipleOf': 3},
                    'tags': {'items': {'maxLength': 4}, 'uniqueItems': True},
                },
                'required': ['code'],
            },
        ]
        if index % 2:
            members.reverse()
        members.append({'$ref': '#/definitions/required8'})
        members.insert(0 if index % 2 == 0 else len(members), neutral)
        files[f's{index}.json'] = json.dumps({'definitions': definitions, 'allOf': members})
        lines += [f'  S{index}:', f'    type: !include s{index}.json', f'    example: {{code: v{index}}}']
    files['lib.raml'] = '\n'.join(lines) + '\n'
    _write(root, files)
    return root / 'lib.raml'


# -- includes -----------------------------------------------------------------

#: One in this many `includes` examples is tab-indented, as an editor that
#: indents with tabs writes it, and one in `_LEADING_TAB_EVERY` begins with a
#: tab before its `{`, the case YAML refuses (docs/03 § 4.2).
_TABBED_EVERY: int = 4
_LEADING_TAB_EVERY: int = 16


def _example_json(index: int) -> str:
    body = f'{{\n  "id": "i{index}",\n  "count": {index},\n  "tags": ["a", "b"]\n}}\n'
    if (index + 1) % _LEADING_TAB_EVERY == 0:
        return '\t' + body.replace('  ', '\t')
    if (index + 1) % _TABBED_EVERY == 0:
        return body.replace('  ', '\t')
    return body


def write_includes(root: Path, *, resource_count: int = 500) -> Path:
    """Examples supplied by `!include`, a `.json` and a `.yaml` file per resource,
    and a Trait fragment per resource.

    The general corpora include nothing but libraries and `.json` schemas, so
    none of them reads a data include (docs/03 § 4.2): the compose cache, the
    header check, and the `.json` whitespace rule; nor has a typed-fragment
    include read once to see its header. Every file here is its own, so each is
    read and composed once; `tests/bench/test_corpus.py` pins that every one
    is, that tabbed ones take the whitespace path, and that each trait's
    header is read.
    """
    files: dict[str, str] = {}
    lines = [
        '#%RAML 1.0',
        'title: Generated include benchmark',
        'types:',
        '  Item:',
        '    properties:',
        '      id: string',
        '      count: integer',
        '      tags: string[]',
        'traits:',
        *(f'  t{index}: !include traits/t{index}.raml' for index in range(resource_count)),
    ]
    for index in range(resource_count):
        files[f'examples/e{index}.json'] = _example_json(index)
        files[f'examples/e{index}.yaml'] = f'id: y{index}\ncount: {index}\ntags: [c]\n'
        files[f'traits/t{index}.raml'] = f'#%RAML 1.0 Trait\ndescription: trait {index}\n'
        lines += [
            f'/r{index}:',
            '  get:',
            f'    is: [t{index}]',
            '    responses:',
            '      200:',
            '        body:',
            '          application/json:',
            '            type: Item',
            '            examples:',
            f'              json: !include examples/e{index}.json',
            f'              yaml: !include examples/e{index}.yaml',
        ]
    files['api.raml'] = '\n'.join(lines) + '\n'
    _write(root, files)
    return root / 'api.raml'


def write_annotation_targets(root: Path, *, family_count: int = 250) -> Path:
    """Included restrictions, nested declaration sites, template roots and scalars.

    Each family reaches the paths whose restrictions the general workloads do
    not exercise (docs/09 § B4). Counts grow together for the linearity gate.
    """
    files: dict[str, str] = {}
    declarations = [
        'annotationTypes:',
        '  traitMeta: {allowedTargets: Trait}',
        '  dynamic: {allowedTargets: Trait}',
        '  resourceMeta: {allowedTargets: ResourceType}',
        '  requestMeta: {allowedTargets: RequestBody}',
        '  responseMeta: {allowedTargets: ResponseBody}',
        '  left: {allowedTargets: [TypeDeclaration, API]}',
        '  right: {allowedTargets: [TypeDeclaration, Trait]}',
    ]
    types = ['types:', '  Base: object']
    traits = ['traits:']
    resource_types = ['resourceTypes:']
    endpoints: list[str] = []
    for index in range(family_count):
        name = f'data{index}'
        declarations.append(f'  {name}: !include annotations/a{index}.raml')
        declarations.extend(
            [
                f'  combined{index}:',
                '    type: [left, right]',
                '    allowedTargets: TypeDeclaration',
            ]
        )
        files[f'annotations/a{index}.raml'] = (
            '#%RAML 1.0 AnnotationTypeDeclaration\ntype: string\nallowedTargets: TypeDeclaration\n'
        )
        types.extend(
            [
                f'  T{index}:',
                '    type:',
                '      value: string',
                f'      (combined{index}): type',
                '    default:',
                f'      value: v{index}',
                f'      ({name}): default',
                f'  O{index}:',
                '    type: object',
                '    discriminator: kind',
                '    properties:',
                '      kind: string',
                '    discriminatorValue:',
                f'      value: v{index}',
                f'      ({name}): discriminator',
            ]
        )
        traits.extend(
            [
                f'  t{index}:',
                '    (traitMeta): definition',
                '    (<<tag>>): <<text>>',
                '    queryString:',
                '      type:',
                '        value: <<item>>',
                f'        ({name}): queryType',
                f'      ({name}): query',
                '      properties:',
                '        q:',
                '          type: string',
                f'          ({name}): property',
            ]
        )
        resource_types.extend(
            [
                f'  r{index}:',
                '    (resourceMeta): definition',
                '    post:',
                '      body:',
                '        application/json:',
                '          type: Base',
                '          (requestMeta): body',
                '          properties:',
                '            p:',
                '              type: string',
                f'              ({name}): property',
            ]
        )
        endpoints.extend(
            [
                f'/r{index}:',
                f'  type: r{index}',
                '  get:',
                f'    is: [{{t{index}: {{tag: dynamic, text: v{index}, item: Base}}}}]',
                '    responses:',
                '      200:',
                '        body:',
                '          application/json:',
                '            type: array',
                '            (responseMeta): body',
                '            items:',
                '              type: string',
                f'              ({name}): items',
            ]
        )
    files['api.raml'] = (
        '\n'.join(
            [
                '#%RAML 1.0',
                'title: Generated annotation-target benchmark',
                *declarations,
                *types,
                *traits,
                *resource_types,
                *endpoints,
            ]
        )
        + '\n'
    )
    _write(root, files)
    return root / 'api.raml'


def write_include_content(root: Path, *, resource_count: int = 250) -> Path:
    """Resources, traits and a `types:` map written in files of their own, as content.

    `/rN: !include resources/rN.yaml`, `tN: !include traits/tN.yaml` and
    `types: !include types.yaml`, with no RAML header, read as if written in
    place (docs/03 § 4.2); each trait is a typed position reading content, and
    its body is grafted from its own file. Each resource
    file holds a method, its parameters and responses, and a child resource,
    so the body is decoded in the included file at every level.
    """
    files: dict[str, str] = {}
    files['types.yaml'] = ''.join(
        f'T{index}:\n  properties:\n    id: string\n    n{index}: integer\n' for index in range(resource_count)
    )
    lines = ['#%RAML 1.0', 'title: Generated include-content benchmark', 'types: !include types.yaml', 'traits:']
    lines += [f'  t{index}: !include traits/t{index}.yaml' for index in range(resource_count)]
    for index in range(resource_count):
        files[f'traits/t{index}.yaml'] = f'description: trait {index}\n'
        files[f'resources/r{index}.yaml'] = (
            f'displayName: R{index}\n'
            'get:\n'
            f'  is: [t{index}]\n'
            '  queryParameters:\n'
            '    page: integer\n'
            '  responses:\n'
            '    200:\n'
            '      body:\n'
            f'        application/json: T{index}\n'
            '/{id}:\n'
            '  delete:\n'
            '    responses:\n'
            '      204:\n'
        )
        lines.append(f'/r{index}: !include resources/r{index}.yaml')
    files['api.raml'] = '\n'.join(lines) + '\n'
    _write(root, files)
    return root / 'api.raml'
