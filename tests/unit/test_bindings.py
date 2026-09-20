"""The generated contracts — docs/16-graph.md § 11.11.

Two questions per backend, and the second is the one that cannot be argued with.

Is the checked-in file what the generator produces? That is the golden idiom,
and it is needed because the file is read by a build this suite never runs.

And does the generator agree with the *emitter*? Only output can answer that. A
generator reads source and can be wrong about what running it does, so a
document declaring every kind is projected and its keys are checked against the
file. Law 19 in `tests/tck/test_properties.py` asks the same of the corpus.

Both destinations sit inside a consumer — `viewer/` and `contrib/raml-codegen` —
and both are read as *text*. That is the direction docs/17 § 2 states: the gate
may look at a consumer's committed output, and may not import one.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.bindings import python, typescript
from fastraml.views.bindings.schema import contract_schema
from fastraml.views.tree import build_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TYPESCRIPT_DESTINATION = 'viewer/src/tree.d.ts'
PYTHON_DESTINATION = 'contrib/raml-codegen/raml_codegen/tree.py'


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


def python_members() -> dict[str, set[str]]:
    """Every field of every TypedDict in the generated module, by class.

    Read with `ast` rather than a regex: the file is Python, and the regex the
    TypeScript half needs is only there because TypeScript is not.
    """
    source = ast.parse((ROOT / PYTHON_DESTINATION).read_text(encoding='utf-8'))
    return {
        node.name: {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        for node in source.body
        if isinstance(node, ast.ClassDef)
    }


def python_shape_members() -> set[str]:
    """Every field accepted by at least one generated shape TypedDict."""
    classes = python_members()
    return set().union(*(fields for name, fields in classes.items() if name == 'ShapeBase' or name.endswith('Shape')))


class TestTheCheckedInTypeScriptFileIsGenerated:
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


class TestTheCheckedInPythonFileIsGenerated:
    """The same two questions of the Python backend, plus one Python only has.

    A `TypedDict` reports its own required and optional keys at run time, and
    under `from __future__ import annotations` it reports them *wrong* -- every
    annotation is a string by the time `TypedDict` reads it, so `NotRequired`
    is invisible and every key looks required. Nothing raises. The generated
    module therefore does not enable PEP 563, and this asks the object itself.
    """

    def test_regenerating_changes_nothing(self):
        current = (ROOT / PYTHON_DESTINATION).read_text(encoding='utf-8')
        assert current == python(), (
            f'run `python -m fastraml.views.bindings python -o {PYTHON_DESTINATION}` -- {PYTHON_DESTINATION} is stale'
        )

    def test_the_module_writes_the_destination_its_caller_names(self, tmp_path):
        destination = tmp_path / 'tree.py'
        result = subprocess.run(  # noqa: S603 - executable and arguments are test-owned
            [sys.executable, '-m', 'fastraml.views.bindings', 'python', '-o', str(destination)],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert str(destination.resolve()) in result.stdout
        assert destination.read_text(encoding='utf-8') == python()

    def test_the_module_can_write_stdout(self):
        result = subprocess.run(
            [sys.executable, '-m', 'fastraml.views.bindings', 'python', '-o', '-'],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.stdout == python()

    def test_it_imports_and_reports_its_own_optional_keys(self, tmp_path):
        # Imported from a copy, not from `contrib`: the gate may read a
        # consumer's committed output and may not import the consumer
        # (docs/17 § 2). What is imported here is a file this test wrote.
        (tmp_path / 'generated_tree.py').write_text(python(), encoding='utf-8')
        sys.path.insert(0, str(tmp_path))
        try:
            module = __import__('generated_tree')
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop('generated_tree', None)

        assert module.Document.__optional_keys__ == frozenset()
        assert 'properties' in module.ObjectShape.__optional_keys__
        assert module.ObjectShape.__required_keys__ == frozenset({'id', 'name', 'type'})
        assert module.Ref.__annotations__ == {'$ref': str}

    def test_closed_wire_vocabularies_are_literal_unions(self):
        generated = python()
        assert "ParameterBinding: TypeAlias = Literal['uri', 'query', 'header']" in generated
        assert "class ObjectShape(ShapeBase):\n    type: Literal['object']" in generated
        assert 'Shape: TypeAlias = (\n    AnyShape\n    | NilShape' in generated

    def test_nested_fixed_records_are_named(self):
        generated = python()
        assert "documentation: NotRequired['list[DocumentationItem]']" in generated
        assert 'class DocumentationItem(TypedDict):' in generated

    def test_an_open_vocabulary_is_not_pretended_closed(self):
        # `x-<anything>` is a scheme type the spec allows and `Literal` cannot
        # spell. Closing over the six would make a valid document unreadable.
        generated = python()
        assert "'Pass Through',\n    ]\n    | str\n)" in generated


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

    def test_every_key_that_arrives_is_declared_in_python_too(self, keys):
        # Asked of each backend separately. The key *sets* come from one schema,
        # but each backend decides which of them it writes, and a backend that
        # silently drops one is the failure this whole file exists to catch.
        declared = python_shape_members() | {'head'}
        assert not keys - declared, f'emitted but not in the contract: {sorted(keys - declared)}'

    def test_both_backends_declare_the_same_shape_fields(self, keys):
        assert declared_shape_members() == python_shape_members()

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

        raml = parse_from_path(
            ROOT / SAMPLE_SOURCE,
            ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
        )
        current = json.loads((ROOT / SAMPLE).read_text(encoding='utf-8'))
        assert current == build_tree(raml), f'run `npm run sample` in viewer/ -- {SAMPLE} is stale'


#: `raml-codegen` reads the same document, and reads it the same way the viewer
#: does: as committed JSON, with no parser installed.
CODEGEN_SAMPLE = 'contrib/raml-codegen/tests/api.json'


class TestTheCodegenSampleIsNotStale:
    """A consumer that depends on no parser still depends on its output.

    `raml-codegen` takes `fastraml tree` output and nothing else -- it does not
    install `fastraml`, so its own suite cannot notice the projection moving
    under it. Somebody has to, and the somebody is the side that owns the
    projection. This is `TestTheViewerSampleIsNotStale` for the other consumer
    on the same footing.
    """

    def test_regenerating_changes_nothing(self):
        import json

        raml = parse_from_path(
            ROOT / SAMPLE_SOURCE,
            ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
        )
        current = json.loads((ROOT / CODEGEN_SAMPLE).read_text(encoding='utf-8'))
        assert current == build_tree(raml), (
            f'run `fastraml tree {SAMPLE_SOURCE} -w {SAMPLE_ROOT} > {CODEGEN_SAMPLE}` -- it is stale'
        )
