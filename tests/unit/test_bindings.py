"""The generated TypeScript contract — docs/16-graph.md § 11.11.

Two questions, and the second is the one that cannot be argued with.

Is the checked-in file what the generator produces? That is the golden idiom,
and it is needed because the file is read by a build this suite never runs.

And does the generator agree with the *emitter*? Only output can answer that. A
generator reads source and can be wrong about what running it does, so a
document declaring every kind is projected and its keys are checked against the
file. Law 19 in `tests/tck/test_properties.py` asks the same of the corpus.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.bindings import typescript
from fastraml.views.bindings.schema import contract_schema
from fastraml.views.tree import build_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TYPESCRIPT_DESTINATION = 'viewer/src/tree.d.ts'


def declared_members() -> dict[str, set[str]]:
    """Every member of every interface in the generated file, by interface."""
    text = (ROOT / TYPESCRIPT_DESTINATION).read_text(encoding='utf-8')
    out: dict[str, set[str]] = {}
    for block in re.finditer(r'export interface (\w+)(?: extends \w+)? \{(.*?)\n\}', text, re.DOTALL):
        name, body = block.group(1), block.group(2)
        out[name] = set(re.findall(r'^\s{2}(\$?\w+)\??:', body, re.MULTILINE))
    return out


def declared_shape_members() -> set[str]:
    """Every field accepted by at least one generated shape variant."""
    interfaces = declared_members()
    return set().union(
        *(members for name, members in interfaces.items() if name == 'ShapeBase' or name.endswith('Shape'))
    )


class TestTheCheckedInFileIsGenerated:
    def test_regenerating_changes_nothing(self):
        # The golden idiom, for the same reason the goldens use it: the file is
        # read by a build this suite does not run, so nothing else would notice
        # it drifting from its source.
        current = (ROOT / TYPESCRIPT_DESTINATION).read_text(encoding='utf-8')
        assert current == typescript(), (
            'run `python -m fastraml.views.bindings typescript '
            f'-o {TYPESCRIPT_DESTINATION}` -- {TYPESCRIPT_DESTINATION} is stale'
        )

    def test_the_module_writes_the_destination_its_caller_names(self, tmp_path):
        # `python -m` is the documented way to regenerate, so it is worth one
        # test: an entry point that raises on import is a broken instruction.
        destination = tmp_path / 'tree.d.ts'
        result = subprocess.run(  # noqa: S603 - executable and arguments are test-owned
            [sys.executable, '-m', 'fastraml.views.bindings', 'typescript', '-o', str(destination)],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert str(destination.resolve()) in result.stdout
        assert destination.read_text(encoding='utf-8') == typescript()

    def test_the_module_can_write_stdout(self):
        result = subprocess.run(
            [sys.executable, '-m', 'fastraml.views.bindings', 'typescript', '-o', '-'],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.stdout == typescript()

    def test_nested_fixed_records_are_named(self):
        generated = typescript()
        assert 'documentation?: DocumentationItem[];' in generated
        assert 'export interface DocumentationItem {' in generated
        assert not re.search(r'^\s+\w+\??: \{', generated, re.MULTILINE)

    def test_closed_wire_vocabularies_are_not_bare_strings(self):
        generated = typescript()
        assert "export type ShapeType = 'any' | 'nil' | 'null'" in generated
        assert "export type ParameterBinding = 'uri' | 'query' | 'header';" in generated
        assert "export interface ObjectShape extends ShapeBase {\n  type: 'object';" in generated
        assert 'export type Shape = AnyShape | NilShape | BooleanShape' in generated
        assert 'binding: ParameterBinding;' in generated
        assert 'type: ShapeNode | null;' in generated


class TestTheSchemaIsLanguageNeutral:
    def test_it_contains_every_fact_a_backend_needs_to_enumerate_the_contract(self):
        schema = contract_schema()
        assert {'format', 'format_version', 'view', 'types', 'endpoints'} <= set(schema.projector['model'].required)
        assert {kind.name for kind in schema.shape_kinds} >= {'string', 'object', 'array', 'union', 'json'}
        object_facets = {facet.name: facet for facet in schema.shape_facets['ObjectShape']}
        assert object_facets['properties'].annotation == 'dict[str, Property] | None'
        assert object_facets['properties'].wire_form == 'annotation'
        number_facets = {facet.name: facet for facet in schema.shape_facets['NumberShape']}
        assert all(number_facets[name].wire_form == 'exact_decimal' for name in schema.exact_decimal_slots)
        assert {'minimum', 'maximum', 'multiple_of'} == schema.exact_decimal_slots


DOCUMENT = """#%RAML 1.0
title: Every kind
types:
  Bounded:
    type: number
    minimum: 0.5
    maximum: 9.5
    multipleOf: 0.01
  Counted:
    type: integer
    minimum: 1
    maximum: 10
  Text:
    type: string
    minLength: 1
    maxLength: 8
    pattern: ^a
  Listed:
    type: array
    items: Text
    minItems: 1
    maxItems: 3
    uniqueItems: true
  Structured:
    type: object
    minProperties: 1
    maxProperties: 4
    additionalProperties: false
    discriminator: kind
    properties:
      kind: string
      /^x-/: string
  Either: string | number
  Upload:
    type: file
    fileTypes: [image/png]
    minLength: 1
    maxLength: 2
  When:
    type: datetime
    format: rfc3339
  Chain:
    properties:
      next?: Chain
"""


class TestEveryKindLandsInTheContract:
    """One document declaring every kind, checked key by key against the file.

    The corpus form of this is law 19 in `tests/tck/test_properties.py`, which
    needs a checkout. This one always runs, so a facet added to a kind fails
    here on any machine.
    """

    @pytest.fixture
    def keys(self, workspace):
        root = workspace({'api.raml': DOCUMENT})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        seen: set[str] = set()
        _observe(build_tree(raml), seen)
        return seen

    def test_every_key_that_arrives_is_declared(self, keys):
        declared = declared_shape_members() | {'head'}
        assert not keys - declared, f'emitted but not in the contract: {sorted(keys - declared)}'

    def test_the_document_reaches_the_facets_it_was_written_for(self, keys):
        # A vacuous pass is how a corpus-shaped test fails: an empty set of
        # observed keys satisfies the check above and proves nothing.
        assert {'minimum', 'multiple_of', 'pattern', 'items', 'properties', 'any_of', 'file_types'} <= keys

    def test_a_bound_arrives_as_an_exact_decimal_string(self, workspace):
        # The contract says so in a comment; this is what makes the comment
        # true. It read `1/100` and `1/2` -- exact, and neither what the author
        # wrote nor anything a consumer could show without long division.
        root = workspace({'api.raml': DOCUMENT})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        bounded = build_tree(raml)['types']['api.raml']['Bounded']
        assert bounded['multiple_of'] == '0.01'
        assert bounded['minimum'] == '0.5'


def _observe(node: object, into: set[str]) -> None:
    """The keys of every mapping that is a shape.

    A mapping carrying `id`, `name` and `type` is one -- the three keys
    `shape()` always writes, and the reason it always writes them.
    """
    if isinstance(node, dict):
        if {'id', 'name', 'type'} <= set(node):
            into.update(node)
        for value in node.values():
            _observe(value, into)
    elif isinstance(node, list):
        for item in node:
            _observe(item, into)


#: What `npm run sample` writes, and what `smoke` and `shots` then read.
SAMPLE = 'viewer/public/api.json'
#: The document itself is a repo-level fixture, not the viewer's: `fastmcp-raml`
#: measures its route building against the same file.
SAMPLE_SOURCE = 'fixtures/sample/api.raml'
SAMPLE_ROOT = 'fixtures'


class TestTheViewerSampleIsNotStale:
    """The viewer's checked-in data, held to the contract's own standard.

    `tree.d.ts` is gate-checked and the data beside it was not, so a change to
    the projection left the viewer's two gates -- `smoke` and `shots` -- running
    against the *previous* shape of the tree. They pass, because a page rendered
    from old data is still a page; what they stop measuring is the emitter.

    Found the way it would be: wrapping an example in a record left
    `items.example` a bare string in the committed sample, so the type page
    dropped it and only the reachability check noticed, by a route that had
    nothing to do with examples.

    Compared as parsed JSON rather than as text: the file is written through a
    shell redirect, so its line endings are the platform's and are not the
    contract.
    """

    def test_regenerating_changes_nothing(self):
        import json

        from fastraml.views.tree import build_tree

        raml = parse_from_path(
            ROOT / SAMPLE_SOURCE,
            ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
        )
        current = json.loads((ROOT / SAMPLE).read_text(encoding='utf-8'))
        assert current == build_tree(raml), f'run `npm run sample` in viewer/ -- {SAMPLE} is stale'
