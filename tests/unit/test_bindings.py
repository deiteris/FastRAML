"""The generated contracts — docs/16-graph.md § 7.

Each backend is asked three things.

**Is the checked-in file what the generator produces?** The golden idiom, needed
because the file is read by a build this suite never runs. TypeScript and Python
only: Go has no checked-in consumer.

**Does the generator agree with the emitter?** A generator reads source and can
be wrong about what running it does, so a document declaring every kind is
projected and its keys are checked against each backend's declarations.
`TestNothingArrivesUndeclared` in `tests/tck/test_properties.py` asks the same
of the corpus.

**Does the hand-written half reach the output unchanged?** Each backend copies
`static/<file>` verbatim apart from Go's package clause, and each static file is
checked by the tools of the language it is written in.

Both golden destinations sit inside a consumer, `viewer/` and
`contrib/raml-codegen`, and both are read as *text*. docs/17 § 1 states that
direction: the gate may read a consumer's committed output and may not import
one.

Go is compiled and run as well, because nothing else here runs it: a throwaway
module decodes two documents through the generated types and writes them back.
Every skip in this file is Go's, and CI's `bindings` job installs a toolchain
and fails if any of them skip.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
from unittest import mock

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views import tree as tree_module
from fastraml.views.bindings import golang, python, typescript
from fastraml.views.bindings import main as bindings_main
from fastraml.views.bindings.conformance import SOURCES
from fastraml.views.bindings.golang import golang_runtime
from fastraml.views.bindings.python import python_runtime
from fastraml.views.bindings.schema import Container, Holds, Structural, contract_schema
from fastraml.views.bindings.typescript import typescript_runtime
from fastraml.views.tree import build_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TYPESCRIPT_DESTINATION = 'viewer/src/tree.d.ts'
PYTHON_DESTINATION = 'contrib/raml-codegen/raml_codegen/tree.py'
#: The reading halves, vendored beside the types they read. A stale one is the
#: same hazard as a stale declaration and a quieter one: the types still
#: typecheck, and the walk silently stops descending a key that now holds a
#: shape (docs/16 § 7).
TYPESCRIPT_RUNTIME = 'viewer/src/walk.ts'
PYTHON_RUNTIME = 'contrib/raml-codegen/raml_codegen/walk.py'


#: Generated records that carry shape fields but are not named `*Shape`.
#: `Recursion` is one: P9 builds a `RecursiveShape` and `shape()` projects
#: it down the generic path, so a marker is a shape (docs/16 § 6.1).
SHAPE_RECORDS = frozenset({'ShapeBase', 'Recursion'})


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
        *(members for name, members in interfaces.items() if name in SHAPE_RECORDS or name.endswith('Shape'))
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
    return set().union(*(fields for name, fields in classes.items() if name in SHAPE_RECORDS or name.endswith('Shape')))


def go_members() -> dict[str, set[str]]:
    """Every *wire* key of every generated struct, by struct.

    The struct tag and not the Go field name: `display_name` is `DisplayName`
    there, and what the contract is about is the key on the wire. Read with a
    regex for the reason the TypeScript half is -- nothing here parses Go.
    """
    text = golang()
    out: dict[str, set[str]] = {}
    for block in re.finditer(r'type (\w+) struct \{(.*?)\n\}', text, re.DOTALL):
        name, body = block.group(1), block.group(2)
        out[name] = set(re.findall(r'`json:"(\$?\w+)', body))
    return out


def go_declares(pattern: str, generated: str | None = None) -> bool:
    """Whether the generated Go holds a line matching `pattern`.

    Columns are padded to whatever the widest neighbour needs, the way `gofmt`
    pads them, so a literal `in` check on a field would be an assertion about
    the *other* fields' names. A space in `pattern` matches any run of them.
    """
    spaced = r'[ \t]+'.join(re.escape(part) for part in pattern.split(' '))
    return re.search(rf'^\t{spaced}$', generated if generated is not None else golang(), re.MULTILINE) is not None


def go_shape_members() -> set[str]:
    """Every field accepted by at least one generated shape struct.

    A variant embeds `ShapeBase` rather than restating it, so the union of the
    two is what a shape may carry -- the same arithmetic as the other backends.
    """
    structs = go_members()
    return set().union(*(fields for name, fields in structs.items() if name in SHAPE_RECORDS or name.endswith('Shape')))


class TestTheCheckedInReadingHalvesAreGenerated:
    """Both vendored runtimes, checked the way their type halves are.

    The failure this catches is the one the type checkers cannot: `walk.ts` and
    `walk.py` carry `CHILDREN`, so a kind that grows a shape-bearing facet
    leaves a stale copy walking the old table. Everything still compiles;
    the walk just stops arriving somewhere, which is indistinguishable from a
    document that had nothing there.
    """

    def test_the_typescript_half_is_current(self):
        current = (ROOT / TYPESCRIPT_RUNTIME).read_text(encoding='utf-8')
        assert current == typescript_runtime(), (
            'run `python -m fastraml.views.bindings typescript '
            f'-o {TYPESCRIPT_DESTINATION} --runtime {TYPESCRIPT_RUNTIME}` -- it is stale'
        )

    def test_the_python_half_is_current(self):
        current = (ROOT / PYTHON_RUNTIME).read_text(encoding='utf-8')
        assert current == python_runtime(), (
            'run `python -m fastraml.views.bindings python '
            f'-o {PYTHON_DESTINATION} --runtime {PYTHON_RUNTIME}` -- it is stale'
        )

    def test_the_walk_table_is_in_all_three(self):
        """Every backend renders `shape_bearing()`, in whatever form suits it.

        Three shapes, and each is the language's and not the contract's. Python
        keeps the table beside the types, since a module can hold a value.
        TypeScript keeps it in the reading half, since a `.d.ts` cannot.
        Go has no table at all: it cannot index a struct by a string key, so the
        same fact is generated as code.

        What must not differ is which keys are in it. Nothing here asks that —
        `tests/unit/test_conformance.py` asks it of the three *walks*, which is
        the only place the answer means anything.
        """
        assert "('items', 'one', 'shape_node', '')" in python()
        assert 'CHILDREN: Final[' not in python_runtime()  # imported from `tree.py`, not restated
        assert "['items', 'one', 'shape_node', '']" in typescript_runtime()
        assert 'const CHILDREN' not in typescript()
        assert 'case *ArrayShape:' in golang_runtime()
        assert 'out = append(out, v.Items)' in golang_runtime()


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

    def test_it_imports_and_reports_its_own_optional_keys(self, tmp_path):
        # Imported from a copy, not from `contrib`: the gate may read a
        # consumer's committed output and may not import the consumer
        # (docs/17 § 1). What is imported here is a file this test wrote.
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
        # Unquoted since the fixed records moved into the static half: they are
        # declared ahead of everything now, so this reaches ahead of nothing.
        assert 'documentation: NotRequired[list[DocumentationItem]]' in generated
        assert 'class DocumentationItem(TypedDict):' in generated

    def test_an_open_vocabulary_is_not_pretended_closed(self):
        # `x-<anything>` is a scheme type the spec allows and `Literal` cannot
        # spell. Closing over the six would make a valid document unreadable.
        generated = python()
        assert "'Pass Through',\n    ]\n    | str\n)" in generated


class TestTheGoBackendDeclaresTheContract:
    """No checked-in consumer, so no golden; every other check instead.

    Go has no union, no literal type, no optional field and no ordered map, so
    it is the backend that would expose any TypeScript or Python assumption left
    in `ContractSchema`. These tests assert the Go spelling of each: the
    constants a closed vocabulary becomes, the interface and table a union
    becomes, `,omitzero` on optional fields, and `*orderedmap.OrderedMap` on
    every map whose keys are data.
    """

    def test_the_caller_names_the_package_too(self):
        # The one flag the other two backends do not have. A Go file cannot omit
        # its package clause, and the destination decides what it should be.
        assert golang().startswith('// Package tree is')
        assert '\npackage tree\n' in golang()
        assert '\npackage ramltree\n' in golang('ramltree')

    def test_closed_wire_vocabularies_are_named_constants(self):
        # Go has no literal type, so a closed vocabulary is a defined string
        # type and one constant per member. A bare `string` would leave the
        # vocabulary undeclared, which is the failure this file exists to catch.
        generated = golang()
        assert 'type ShapeType string' in generated
        assert go_declares('ShapeTypeObject ShapeType = "object"', generated)
        assert go_declares('ShapeTypeDatetimeOnly ShapeType = "datetime-only"', generated)
        assert go_declares('HttpMethodGet HttpMethod = "get"', generated)
        assert go_declares('FragmentKindAPI FragmentKind = "API"', generated)
        assert go_declares('AnnotationTargetRequestBody AnnotationTarget = "RequestBody"', generated)

    def test_the_union_is_an_interface_over_a_generated_table(self):
        """What is generated is the table; what reads it is hand-written.

        `Shape`, `UnmarshalShape` and `shapeOf` hold nothing derived, so they
        live in `static/tree.go` where `gofmt` can reach them. The backend emits
        `shapeKinds`, which is `KIND_TO_CLASS` as data.
        """
        generated = golang()
        # Hand-written, and reaching the output through the static half.
        # Two methods: the discriminator, and `Base`, which is what lets the
        # walk read `id` off a Shape without a type switch (`static/walk.go`).
        assert 'type Shape interface {\n\tShapeKind() ShapeType\n\tBase() *ShapeBase\n}' in generated
        assert 'func (b *ShapeBase) Base() *ShapeBase { return b }' in generated
        assert 'func UnmarshalShape(data []byte) (Shape, error) {' in generated
        assert 'func shapeOf(kind ShapeType) (Shape, error) {' in generated
        assert 'empty, found := shapeKinds[kind]' in generated
        # Generated, and `nil` and `null` reach one variant exactly where the
        # parser shares an implementation -- the table is `KIND_TO_CLASS`.
        assert 'var shapeKinds = map[ShapeType]func() Shape{' in generated
        assert go_declares('ShapeTypeNil: func() Shape { return &NilShape{} },', generated)
        assert go_declares('ShapeTypeNull: func() Shape { return &NilShape{} },', generated)
        assert go_declares('ShapeTypeObject: func() Shape { return &ObjectShape{} },', generated)
        assert 'func (s *ObjectShape) ShapeKind() ShapeType {' in generated

    def test_the_pointer_valued_map_aliases_are_declared_as_listed(self):
        """`_POINTER_ELEMENTS` against the file it describes.

        The walk addresses a map's element unless the alias already holds a
        pointer, and which aliases do is a fact about `static/tree.go`. The
        generator used to read that file with a regex at generation time; the
        list is clearer, and this is what stops it going stale.
        """
        _, module = backend('golang')
        declared = module._STATIC.read_text(encoding='utf-8')
        pointer_valued = set(
            re.findall(r'^	(\w+)\s*=\s*\*orderedmap\.OrderedMap\[\w+, \*ShapeNode\]$', declared, re.MULTILINE)
        )
        assert pointer_valued == module._POINTER_ELEMENTS

    def test_every_map_whose_keys_are_data_is_ordered(self):
        # Declaration order is preserved everywhere the model is exposed
        # (AGENTS.md), and a plain Go map would discard that without failing.
        generated = golang()
        assert 'orderedmap "github.com/wk8/go-ordered-map/v2"' in generated
        assert go_declares('PropertiesByName = *orderedmap.OrderedMap[string, Property]', generated)
        assert go_declares('EndpointsByPath = *orderedmap.OrderedMap[EndpointPath, Endpoint]', generated)
        assert go_declares('OperationsByMethod = *orderedmap.OrderedMap[HttpMethod, Operation]', generated)
        assert go_declares('Properties PropertiesByName `json:"properties,omitzero"`', generated)
        # And no unordered one reached a field by accident.
        assert not re.search(r'\bmap\[[^]]*\][^`\n]*`json:', generated)

    def test_an_optional_scalar_is_a_pointer_and_a_nilable_type_is_not_wrapped(self):
        # The zero `string` is `""`, so the tag alone on a bare one conflates
        # the absent key with the empty one -- the silent loss the contract is
        # against.
        generated = golang()
        assert go_declares('MinLength *int `json:"min_length,omitzero"`', generated)
        assert go_declares('Pattern *string `json:"pattern,omitzero"`', generated)
        assert go_declares('Items *ShapeNode `json:"items,omitzero"`', generated)
        # A slice, a map and a pointer are nil-able already and are left alone.
        assert go_declares('AnyOf []ShapeNode `json:"any_of,omitzero"`', generated)
        assert go_declares('Examples ExamplesByName `json:"examples,omitzero"`', generated)
        assert go_declares('Example *Example `json:"example,omitzero"`', generated)
        # A required key keeps its plain tag, nullable or not.
        assert go_declares('ID *Address `json:"id"`', generated)
        assert go_declares('Scopes []string `json:"scopes"`', generated)

    def test_nested_fixed_records_are_named(self):
        generated = golang()
        assert go_declares('Documentation []DocumentationItem `json:"documentation,omitzero"`', generated)
        assert 'type DocumentationItem struct {' in generated

    def test_an_open_vocabulary_is_not_pretended_closed(self):
        # `x-<anything>` is a scheme type the spec allows. A defined string type
        # is open by construction, so Go needs no widening -- it needs the six
        # named, which is the half that is closed.
        generated = golang()
        assert 'type SecuritySchemeType string' in generated
        assert go_declares('SecuritySchemeTypeOAuth20 SecuritySchemeType = "OAuth 2.0"', generated)

    def test_acronyms_are_spelled_the_way_go_spells_them(self):
        generated = golang()
        for field, key in (
            ('ID', 'id'),
            ('BaseURI', 'base_uri'),
            ('URIParameters', 'uri_parameters'),
            ('JSONSchema', 'json_schema'),
            ('XML', 'xml'),
        ):
            assert re.search(rf'^\t{field}[ \t]+\S+[ \t]+`json:"{key}', generated, re.MULTILINE), field


def _go_module(tmp_path, *sources: tuple[str, str]):
    """A throwaway Go module holding the generated contract, or a skip.

    Skips rather than fails without a toolchain, so the suite still runs on a
    machine without Go; CI's `bindings-go` job installs one and fails if this
    skips. The tests above cover the declarations everywhere. This one needs a
    compiler: whether the file builds, and whether the ordered maps behave as
    the backend assumes, can only be answered by running it.

    `go.mod` declares `go 1.24` because `,omitzero` requires it, and because that
    is what the generated package comment tells a consumer to declare. An older
    toolchain upgrades itself rather than ignoring the tag.
    """
    go = shutil.which('go')
    if go is None:
        pytest.skip('no go toolchain')
    # The generated file is written with LF whatever the platform: `gofmt` reads
    # its own output back, and CRLF is a rewrite it would report as a diff.
    (tmp_path / 'tree.go').write_text(golang(), encoding='utf-8', newline='\n')
    for name, body in sources:
        (tmp_path / name).write_text(body, encoding='utf-8', newline='\n')
    done = _run(go, 'mod', 'init', 'example.com/contract', cwd=tmp_path)
    if done.returncode != 0:
        pytest.skip(f'go mod init: {done.stderr.strip()[:200]}')
    module = tmp_path / 'go.mod'
    module.write_text(
        re.sub(r'^go .*$', f'go {GO_FLOOR}', module.read_text(encoding='utf-8'), count=1, flags=re.MULTILINE),
        encoding='utf-8',
        newline='\n',
    )
    done = _run(go, 'mod', 'tidy', cwd=tmp_path)
    if done.returncode != 0:
        pytest.skip(f'go mod tidy: {done.stderr.strip()[:200]}')
    return tmp_path


def _recursion_markers(node, into: list[dict]) -> list[dict]:
    """Every `type: "recursive"` node in a tree."""
    if isinstance(node, dict):
        if node.get('type') == 'recursive':
            into.append(node)
        for value in node.values():
            _recursion_markers(value, into)
    elif isinstance(node, list):
        for item in node:
            _recursion_markers(item, into)
    return into


#: The floor `omitzero` needs. Named once: the generated package comment states
#: it, and a module that does not declare it encodes an empty list as an absent
#: key -- silently, which is what this whole subsystem is against.
GO_FLOOR = '1.24'


def _run(executable: str, *arguments: str, cwd) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable is resolved, arguments are test-owned
        [executable, *arguments], check=False, capture_output=True, text=True, cwd=cwd
    )


GO_TEST = """\
package tree

import (
	"encoding/json"
	"os"
	"testing"

	orderedmap "github.com/wk8/go-ordered-map/v2"
)

// read decodes one tree and writes back what it decoded, for the Python side to
// diff. Encoding is half the contract and the only way to measure it is to ask
// for the bytes: a field this file never mentions is one a `for` loop over a
// struct would not catch either.
func read(t *testing.T, name string) Document {
	t.Helper()
	data, err := os.ReadFile(name + ".json")
	if err != nil {
		t.Fatal(err)
	}
	var doc Document
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Format != Format || doc.FormatVersion != FormatVersion || doc.View != View {
		t.Fatalf("%s header: %q %d %q", name, doc.Format, doc.FormatVersion, doc.View)
	}
	again, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(name+".roundtrip", again, 0o600); err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestEveryKind(t *testing.T) {
	doc := read(t, "kinds")
	declarations, _ := doc.Types.Get("api.raml")
	if declarations == nil {
		t.Fatal("no declarations for api.raml")
	}
	var order []string
	for pair := declarations.Oldest(); pair != nil; pair = pair.Next() {
		order = append(order, pair.Key)
	}
	want := []string{"Bounded", "Counted", "Text", "Listed", "Structured", "Either", "Upload", "When", "Chain"}
	for index, name := range want {
		if index >= len(order) || order[index] != name {
			t.Fatalf("declaration order: %v", order)
		}
	}

	bounded, _ := declarations.Get("Bounded")
	number, ok := bounded.Shape.(*NumberShape)
	if !ok {
		t.Fatalf("Bounded decoded as %T", bounded.Shape)
	}
	if *number.MultipleOf != "0.01" || *number.Minimum != "0.5" {
		t.Fatalf("exact decimals: %q %q", *number.MultipleOf, *number.Minimum)
	}
	if number.ShapeKind() != ShapeTypeNumber {
		t.Fatalf("ShapeKind: %q", number.ShapeKind())
	}

	listed, _ := declarations.Get("Listed")
	array := listed.Shape.(*ArrayShape)
	if array.Items.Ref == nil {
		t.Fatalf("Listed.items is not a link: %+v", array.Items)
	}

	either, _ := declarations.Get("Either")
	if union, ok := either.Shape.(*UnionShape); !ok || len(union.AnyOf) != 2 {
		t.Fatalf("Either decoded as %T", either.Shape)
	}

	structured, _ := declarations.Get("Structured")
	object := structured.Shape.(*ObjectShape)
	if object.AdditionalProperties == nil || *object.AdditionalProperties {
		t.Fatal("additional_properties lost")
	}
	if first := object.Properties.Oldest(); first == nil || first.Key != "kind" {
		t.Fatal("properties lost their order")
	}
	if object.PatternProperties == nil || object.PatternProperties.Len() != 1 {
		t.Fatal("pattern properties lost")
	}

	chain, _ := declarations.Get("Chain")
	next, _ := chain.Shape.(*ObjectShape).Properties.Get("next")
	if next.Type.Recursion == nil || next.Type.Recursion.Head.Ref == "" {
		t.Fatalf("Chain.next is not a recursion marker: %+v", next.Type)
	}
}

// TestTheWorkedDocument reaches what one type declaration cannot: endpoints,
// operations keyed by an HttpMethod, responses keyed by a status, bodies keyed
// by a media type, parameters, security settings and annotation values. Those
// are the map keys that are *defined string types*, and whether the ordered map
// decodes one is a question only a run answers.
func TestTheWorkedDocument(t *testing.T) {
	doc := read(t, "sample")
	if doc.Endpoints == nil || doc.Endpoints.Len() == 0 {
		t.Fatal("no endpoints")
	}
	operations, responses, bodies, parameters := 0, 0, 0, 0
	for endpoint := doc.Endpoints.Oldest(); endpoint != nil; endpoint = endpoint.Next() {
		if endpoint.Value.Operations == nil {
			continue
		}
		for method := endpoint.Value.Operations.Oldest(); method != nil; method = method.Next() {
			if method.Key == "" {
				t.Fatalf("%s: an HttpMethod key decoded empty", endpoint.Key)
			}
			operations++
			parameters += count(method.Value.Headers) + count(method.Value.QueryParameters)
			if method.Value.Responses == nil {
				continue
			}
			for response := method.Value.Responses.Oldest(); response != nil; response = response.Next() {
				if response.Key == "" {
					t.Fatalf("%s: a StatusCode key decoded empty", endpoint.Key)
				}
				responses++
				if response.Value.Bodies == nil {
					continue
				}
				for body := response.Value.Bodies.Oldest(); body != nil; body = body.Next() {
					if body.Key == "" {
						t.Fatalf("%s: a MediaType key decoded empty", endpoint.Key)
					}
					bodies++
					if body.Value != nil && body.Value.Shape == nil && body.Value.Ref == nil {
						t.Fatalf("%s %s: a body decoded to nothing", endpoint.Key, body.Key)
					}
				}
			}
		}
	}
	if operations == 0 || responses == 0 || bodies == 0 || parameters == 0 {
		t.Fatalf("operations=%d responses=%d bodies=%d parameters=%d", operations, responses, bodies, parameters)
	}

	// A settings value is a scalar or a list, and this document has both.
	scalars, lists := 0, 0
	for file := doc.SecuritySchemes.Oldest(); file != nil; file = file.Next() {
		for scheme := file.Value.Oldest(); scheme != nil; scheme = scheme.Next() {
			if scheme.Value.Settings == nil {
				continue
			}
			for setting := scheme.Value.Settings.Oldest(); setting != nil; setting = setting.Next() {
				switch {
				case setting.Value.List != nil:
					lists++
				case setting.Value.Scalar != nil:
					scalars++
				default:
					t.Fatalf("%s.%s decoded to neither", scheme.Key, setting.Key)
				}
			}
		}
	}
	if scalars == 0 || lists == 0 {
		t.Fatalf("security settings: %d scalars, %d lists", scalars, lists)
	}

	// An annotation value is author data, so it arrives raw. These are the three
	// ways to read one, and a consumer needs all three.
	if len(doc.Annotations) == 0 {
		t.Fatal("no annotations")
	}
	for _, annotation := range doc.Annotations {
		if annotation.Value == nil {
			t.Fatalf("%s: value is absent, which the contract says is impossible", annotation.Name)
		}
		if annotation.Value.IsNull() {
			continue
		}
		var into any
		if err := annotation.Value.Decode(&into); err != nil {
			t.Fatalf("%s: %v", annotation.Name, err)
		}
		if len(annotation.Value) > 0 && annotation.Value[0] == '{' {
			object, err := annotation.Value.Object()
			if err != nil {
				t.Fatalf("%s: %v", annotation.Name, err)
			}
			if object.Len() == 0 {
				t.Fatalf("%s: an object annotation decoded empty", annotation.Name)
			}
		}
	}
}

func count[V any](m *orderedmap.OrderedMap[string, V]) int {
	if m == nil {
		return 0
	}
	return m.Len()
}
"""


#: The three backends, by the name their module has.
BACKENDS = ('typescript', 'python', 'golang')


def backend(name: str):
    """A backend's renderer and the module holding its private constants.

    Out of `sys.modules` and not by dotted name: `bindings/__init__.py` binds
    each entry point onto the package, so `fastraml.views.bindings.golang`
    resolves to the *function* and both `import ... as` and a dotted
    monkeypatch target get that instead of the module.
    """
    render = {'typescript': typescript, 'python': python, 'golang': golang}[name]
    return render, sys.modules[f'fastraml.views.bindings.{name}']


class TestTheEntryPointWritesWhereItsCallerSays:
    def test_python_m_runs(self, tmp_path):
        # `python -m` is the documented way to regenerate, so it is worth one
        # subprocess: an entry point that raises on import is a broken
        # instruction. The dispatch is shared; one backend proves it.
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

    @pytest.mark.parametrize('name', BACKENDS)
    def test_it_writes_the_destination_its_caller_names(self, name, tmp_path, capsys):
        render, _ = backend(name)
        destination = tmp_path / 'out'
        bindings_main([name, '-o', str(destination)])
        assert str(destination.resolve()) in capsys.readouterr().out
        assert destination.read_text(encoding='utf-8') == render()

    @pytest.mark.parametrize('name', BACKENDS)
    def test_it_can_write_stdout(self, name, capsys):
        render, _ = backend(name)
        bindings_main([name, '-o', '-'])
        assert capsys.readouterr().out == render()


class TestTheHandWrittenHalvesAreFilesNotStrings:
    """The part of each artifact that never varies, kept in its own language.

    Each backend emits two halves. One is derived from `ContractSchema`; the
    other is hand-authored text the backend copies verbatim, and it lives in
    `static/` as a file of the target language rather than as a Python string.
    A syntax error then fails at the file it was typed in.

    That only holds while the files stay valid source, so these tests assert
    there is no placeholder in any of them. Go substitutes its package clause;
    nothing else substitutes at all.
    """

    @pytest.mark.parametrize('name', BACKENDS)
    def test_the_static_half_reaches_the_output_verbatim(self, name):
        render, module = backend(name)
        contract = module._STATIC.read_text(encoding='utf-8').strip('\n')
        if name == 'golang':
            # The one substitution: the default package is what the file is
            # written with, so rendering it changes nothing.
            assert contract in render('tree')
            assert 'package other' in render('other')
        else:
            assert contract in render()

    @pytest.mark.parametrize('name', BACKENDS)
    def test_the_static_half_names_itself_as_the_place_to_edit(self, name):
        """The header has to be true in two files at once.

        It is copied into the generated artifact, which a consumer must not
        edit, and it is also the first thing a maintainer sees when they open
        the static half, which is exactly the file they should edit. So it says
        both: do not edit the assembled file, edit this one.
        """
        _, module = backend(name)
        text = module._STATIC.read_text(encoding='utf-8')
        assert 'Do not edit the\n' in text or 'Do not edit the ' in text
        assert f'static/{module._STATIC.name}' in text

    def test_a_renamed_go_package_clause_fails_by_name(self, tmp_path, monkeypatch):
        # Go's is the one file with a substitution, so it is the one that can
        # lose it, and a file with two package clauses is what would result.
        _, module = backend('golang')
        broken = tmp_path / 'tree.go'
        broken.write_text(
            module._STATIC.read_text(encoding='utf-8').replace('package tree', 'package other'), encoding='utf-8'
        )
        monkeypatch.setattr(module, '_STATIC', broken)
        with pytest.raises(LookupError, match='package tree'):
            golang()

    def test_the_go_half_is_gofmt_clean_where_it_lives(self):
        """Checked in place rather than through the generator.

        That is the difference a `.go` file makes: a malformed one fails at the
        file it was typed in, with a line number, instead of surfacing as a
        syntax error in output three steps later.
        """
        gofmt = shutil.which('gofmt')
        if gofmt is None:
            pytest.skip('no gofmt')
        _, module = backend('golang')
        done = _run(gofmt, '-l', module._STATIC.name, cwd=module._STATIC.parent)
        assert done.returncode == 0, done.stderr
        assert not done.stdout.strip(), f'run `gofmt -w {module._STATIC}`'

    def test_the_python_half_is_a_stub_and_cannot_be_imported(self):
        """It holds half a contract, so importing it must not be possible.

        As `static/tree.py` it *was*: `static/` has no `__init__.py`, but a
        namespace package needs none, so the import resolved and succeeded,
        handing back a fragment with no `Document` and no vocabularies. A
        consumer reading a key off a half-contract is the silent failure this
        subsystem exists to prevent.

        `.pyi` closes it at the root rather than guarding it: Python imports
        `.py` and `.pyw` only, and a stub is what the file holds -- aliases,
        `TypedDict`s and no runtime.
        """
        _, module = backend('python')
        assert module._STATIC.suffix == '.pyi'
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module('fastraml.views.bindings.static.tree')
        # The runtime half *is* a `.py` -- it holds runtime, so a stub cannot
        # carry it. The hazard is the same one and it is closed the same way:
        # `walk.py` reads its table `from .tree import`, and `static/` has no
        # `tree.py`, so importing it where it lives fails at the first line
        # rather than handing back a walk with no table.
        for name in ('walk', 'conform'):
            with pytest.raises(ImportError):
                importlib.import_module(f'fastraml.views.bindings.static.{name}')
        importable = {path.name for path in module._STATIC.parent.iterdir() if path.suffix in {'.py', '.pyw'}}
        assert importable == {'walk.py', 'conform.py'}

    def test_the_python_half_parses_where_it_lives(self):
        """Its equivalent of `gofmt -l`, and the reason it is not ruff's job.

        `static/tree.pyi` is excluded from ruff and mypy because its style is
        the *contract's* rather than this project's: `TypeAlias` over `type` is
        load-bearing for the PEP 563 reason in the module docstring, and it
        names types only the generated half declares. `raml-codegen` already
        exempts the same bytes. What remains is that it parses.
        """
        _, module = backend('python')
        ast.parse(module._STATIC.read_text(encoding='utf-8'), filename=str(module._STATIC))

    def test_the_typescript_half_is_checked_by_the_viewer(self):
        """TypeScript needs no in-place check here.

        `tree.d.ts` is in the viewer's `tsconfig`, so `npm run check` typechecks
        the assembled file, covering the static half and its *names* too -- more
        than `gofmt` or `ast.parse` covers. The golden test keeps the committed
        copy equal to what generation produces, so the two meet.
        """
        _, module = backend('typescript')
        contract = module._STATIC.read_text(encoding='utf-8').strip('\n')
        assert contract in (ROOT / TYPESCRIPT_DESTINATION).read_text(encoding='utf-8')


class TestTheRecursionGuardTerminatesAnUnmarkedCycle:
    """What `_Projector.recursion()` is for, demonstrated rather than asserted.

    Nothing in any corpus fires it, and that is not evidence it is dead. It
    guards against a bug in three *other* modules, each of which makes the shape
    graph a DAG before this layer walks it:

    * `unwrap.py:_make_recursive` — every model cycle, under `unwrap=True`.
    * `jsonschema_.py` — `$ref` cycles inside a projection, which P9 never sees.
    * `shape()`'s own alias check — every self-reference under `unwrap=False`,
      which is why that mode reaches the guard least of all.

    This splices a cycle none of the three covers, which is the state a
    regression in any of them produces. With the guard the document projects and
    carries one extra marker; without it the projection does not terminate
    (docs/16 § 6.1).
    """

    #: An anonymous inner type whose only property is spliced back onto its
    #: declared ancestor. Written as one literal so the splice below is the
    #: only interesting line in the fixture.
    SOURCE = """#%RAML 1.0
title: spliced
types:
  Outer:
    properties:
      inner:
        properties:
          leaf: string
"""

    @pytest.fixture
    def cyclic(self, workspace):
        root = workspace({'api.raml': self.SOURCE})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        shapes = raml.shapes if isinstance(raml.shapes, dict) else {shape.id: shape for shape in raml.shapes}
        outer = next(shape for shape in shapes.values() if shape.name == 'Outer')
        inner = outer.shape.properties['inner'].base
        # No alias and no `RecursiveShape`: a back-edge of the kind the three
        # marking passes exist to remove, left in place.
        inner.shape.properties['leaf'].base = outer
        return raml

    def test_it_projects_and_marks_instead_of_recursing(self, cyclic):
        calls: list[str] = []
        original = tree_module._Projector.recursion

        def spy(self, base):
            calls.append(base.name)
            return original(self, base)

        with mock.patch.object(tree_module._Projector, 'recursion', spy):
            projected = build_tree(cyclic)

        assert calls == ['Outer'], 'the guard did not fire on an unmarked cycle'
        markers = _recursion_markers(projected, [])
        assert len(markers) == 1
        # The fallback spelling, which is fine for what it is: a consumer that
        # handles a marker handles this one. It is only wrong as a *source for
        # the contract*, which is why `Recursion` is hand-declared.
        assert markers[0]['type'] == 'recursive'
        assert markers[0]['head']['$ref'].endswith('/types/Outer')

    def test_without_it_the_projection_does_not_terminate(self, cyclic):
        """What the guard buys, priced.

        The same model with the `seen` check defeated. If this ever stops
        raising, the graph has another cycle-breaker and § 11.11c should say
        which -- it does not mean the guard became unnecessary.
        """
        original = tree_module._Projector.shape

        def unguarded(self, base, seen=frozenset()):
            return original(self, base, frozenset())

        with mock.patch.object(tree_module._Projector, 'shape', unguarded), pytest.raises(RecursionError):
            build_tree(cyclic)


class TestEveryProjectorMethodTheGeneratorReadsActuallyRuns:
    """The general form of § 11.11c, asserted so the next instance fails by name.

    `schema.py` derives the contract by reading each `_Projector` method's keys
    out of its AST. That read is only as good as the assumption that the method
    *runs*: `recursion()` is a literal three-key dict, reads perfectly, and
    emits nothing, so the contract it produced declared three keys where seven
    ship.

    One method is in that position, and it is a deliberate guard. A second one
    would be the same bug again, so the set is pinned rather than the single
    name: a new `_Projector` method that never fires is one the generator cannot
    safely read, and this says so before a consumer finds out.
    """

    @pytest.fixture
    def ran(self, workspace):
        counted = dict.fromkeys(contract_schema().projector, 0)
        patches = []
        for name in counted:
            original = getattr(tree_module._Projector, name)

            def spy(self, *arguments, _name=name, _original=original, **keywords):
                counted[_name] += 1
                return _original(self, *arguments, **keywords)

            patches.append(mock.patch.object(tree_module._Projector, name, spy))
        # Two documents: one declaring every kind, and the worked sample, which
        # is the one with endpoints, security schemes and a JSON-schema type.
        # Neither needs the TCK checkout, so this always runs.
        root = workspace({'api.raml': DOCUMENT})
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            build_tree(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
            build_tree(
                parse_from_path(ROOT / SAMPLE_SOURCE, ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT))
            )
        return counted

    def test_exactly_one_method_never_fires_and_it_is_the_guard(self, ran):
        silent = {name for name, count in ran.items() if count == 0}
        assert silent == {'recursion'}, (
            f'{sorted(silent)} never ran. A method the generator reads and the emitter never calls is '
            'what docs/16 § 6.1 is about -- declare its record by hand, or find out why it is dead.'
        )


class TestARecursionMarkerIsDeclaredAsAShape:
    """A marker is a shape, and the contract now says so. docs/16 § 6.1.

    It was declared as a standalone `{type, name, head}` record, generated from
    `_Projector.recursion()` -- which never runs, because P9 marks recursion
    before this layer walks anything. What reaches the wire is a
    `RecursiveShape` through the generic `shape()` path, carrying `id` and
    whatever `ShapeBase` fields the type it stands for had. A consumer holding
    the old record saw a marker's `description` and `annotations` as absent.

    Neither `declared_shape_members()` nor the corpus contract check could
    catch that: both ask
    whether a key is declared *somewhere* among the shape members, and all of
    them are on `ShapeBase`. Only writing a decoded document back found it.

    Each backend now declares `Recursion` as a `ShapeBase` variant plus `type`
    and `head`. `head` is hand-declared in all three, because `shape()` writes
    it through a loop over `_BACK_POINTERS` and no AST read resolves the key.
    """

    @pytest.fixture
    def markers(self):
        raml = parse_from_path(
            ROOT / SAMPLE_SOURCE,
            ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
        )
        found = _recursion_markers(build_tree(raml), [])
        assert found, 'the sample no longer contains a recursive type'
        return found

    def test_every_key_a_marker_carries_is_declared(self, markers):
        emitted = set().union(*(set(marker) for marker in markers))
        # The four that went undeclared, plus `head`, which the older tests
        # added to the declared set by hand rather than declaring.
        assert {'id', 'description', 'custom_facets', 'annotations', 'head'} <= emitted
        for declared in (declared_shape_members(), python_shape_members(), go_shape_members()):
            assert not emitted - declared, sorted(emitted - declared)

    def test_the_spelling_that_ships_is_not_the_one_recursion_writes(self, markers):
        """Which of `tree.py`'s two spellings actually runs, measured not read.

        `_Projector.recursion()` writes `{type, name, head}` and reads perfectly
        as source, which is how the contract came to be generated from it. It is
        also the path that does not run: the tree expects an unwrapped model, so
        P9 has marked every cycle and the projector meets each marker fresh.

        Asserted rather than commented, because the assumption has been made
        twice. If this ever fails, `recursion()` has become live and docs/16
        § 6.1 is the thing to reread -- not a line to delete.
        """
        calls: list[str] = []
        original = tree_module._Projector.recursion

        def spy(self, base):
            calls.append(base.name)
            return original(self, base)

        raml = parse_from_path(ROOT / SAMPLE_SOURCE, ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT))
        with mock.patch.object(tree_module._Projector, 'recursion', spy):
            projected = build_tree(raml)

        assert _recursion_markers(projected, []), 'the sample no longer contains a recursive type'
        assert calls == [], f'`recursion()` ran: {calls}'
        # And what did ship is the ShapeBase spelling, which is seven keys where
        # `recursion()` writes three.
        assert all({'id', 'name', 'type', 'head'} <= set(marker) for marker in markers)

    def test_unwrap_false_reaches_the_guard_least_of_all(self):
        """The part that is easy to get backwards.

        `recursion()` looks like the `unwrap=False` path and is not: without
        unwrap a cycle runs through a *declaration*, `reference()` short-circuits
        it to a `$ref`, and `shape()` never re-enters. So that mode produces no
        markers and no calls either. The guard stays for the cycle that reaches
        `shape()` past neither a declaration nor a marker.
        """
        calls: list[str] = []
        original = tree_module._Projector.recursion

        def spy(self, base):
            calls.append(base.name)
            return original(self, base)

        raml = parse_from_path(ROOT / SAMPLE_SOURCE, ParseOptions(unwrap=False, workspace_root=ROOT / SAMPLE_ROOT))
        with mock.patch.object(tree_module._Projector, 'recursion', spy):
            projected = build_tree(raml)

        assert _recursion_markers(projected, []) == []
        assert calls == []

    def test_it_extends_the_shape_base_in_every_backend(self):
        # Asked of each separately: one schema supplies the key sets, but what
        # each backend writes is its own, and this one is hand-declared.
        assert 'export interface Recursion extends ShapeBase {' in typescript()
        assert 'class Recursion(ShapeBase):' in python()
        assert 'type Recursion struct {\n\tShapeBase\n' in golang()
        # And it is not a `Shape`: that is what a declaration and a `projection`
        # hold, and a marker is neither. `ShapeNode` carries it as its own arm.
        assert 'Shape = AnyShape' in typescript()
        assert 'Recursion' not in typescript().split('export type Shape =')[1].split(';')[0]
        assert 'func (s *Recursion) ShapeKind()' not in golang()


class TestTheGeneratedGoCompilesAndReadsTheTree:
    """Compiles the generated Go and decodes two real documents through it.

    `tree.d.ts` is compiled by the viewer's gate and `tree.py` is imported by a
    test above; Go has neither, so this builds a throwaway module against the
    real ordered-map dependency and runs it.

    The union dispatch, the `$ref` discrimination and the ordered maps are all
    runtime. No check that reads generated output can settle any of them.
    """

    @pytest.fixture
    def decoded(self, tmp_path, workspace):
        """Both documents, decoded by the generated Go and written back."""
        module = _go_module(tmp_path, ('contract_test.go', GO_TEST))
        root = workspace({'api.raml': DOCUMENT})
        trees = {
            'kinds': build_tree(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))),
            'sample': build_tree(
                parse_from_path(
                    ROOT / SAMPLE_SOURCE,
                    ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
                )
            ),
        }
        for name, tree in trees.items():
            (module / f'{name}.json').write_text(json.dumps(tree), encoding='utf-8', newline='\n')
        # `-v`, because `go test` also exits 0 when it matched no tests at all.
        done = _run(shutil.which('go'), 'test', '-v', './...', cwd=module)
        assert done.returncode == 0, done.stdout + done.stderr
        return (
            done.stdout,
            trees,
            {name: json.loads((module / f'{name}.roundtrip').read_text(encoding='utf-8')) for name in trees},
        )

    def test_it_compiles_and_both_documents_are_decoded(self, decoded):
        # The Go assertions are the test; this asks that each of them ran.
        output, trees, written = decoded
        assert '--- PASS: TestEveryKind' in output, output
        assert '--- PASS: TestTheWorkedDocument' in output, output
        assert set(written) == set(trees)

    def test_what_it_decoded_is_what_it_was_given(self, decoded):
        """Encoding is the other half, and `omitzero` is what makes it exact.

        Under `omitempty` this failed on four keys -- `annotations`,
        `media_types`, `protocols` and `secured_by` -- each of which the tree
        emits as `[]` and Go wrote back as an absent key. Comparing whole
        documents rather than sampling fields is the point: a key no assertion
        names is exactly the key a binding drops.
        """
        _, trees, written = decoded
        for name, tree in trees.items():
            assert tree == written[name], name

    def test_it_is_gofmt_clean(self, tmp_path):
        # Not a style check on a file nobody commits: `gofmt -l` parses, so a
        # generator that emitted a syntax error would fail here by name, and the
        # column padding `_aligned` computes is measured against the tool that
        # decides it.
        module = _go_module(tmp_path)
        gofmt = shutil.which('gofmt')
        if gofmt is None:
            pytest.skip('no gofmt')
        done = _run(gofmt, '-l', 'tree.go', cwd=module)
        assert done.returncode == 0, done.stderr
        assert not done.stdout.strip(), f'gofmt would rewrite tree.go:\n{done.stdout}'


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

    def test_every_backend_can_spell_every_structural_kind(self):
        """The check the three tables could not make of each other.

        What each key holds is declared once, so the failure a backend can now
        have is the new one: a structural kind it has no spelling for. Asked of
        every key in the contract rather than of the ones a document happens to
        reach, because a backend that cannot render a kind fails generation
        outright and the corpus would only find it by luck.
        """
        schema = contract_schema()
        for name in ('typescript', 'python', 'golang'):
            _, module = backend(name)
            for record, keys in schema.structural.items():
                for key, structure in keys.items():
                    assert module._spelling(structure), f'{name} cannot spell {record}.{key}'
            for facets in schema.shape_facets.values():
                for facet in facets:
                    assert module._spelling(schema.facet_structure(facet)), f'{name} cannot spell {facet.name}'

    def test_a_structural_kind_no_backend_knows_fails_by_name(self):
        """A new `Holds` member is a change every backend has to answer."""
        _, module = backend('golang')
        with pytest.raises(LookupError, match='named ordered-map alias'):
            module._spelling(Structural(Holds.RECORD, 'Unheard', container=Container.MAP))


DOCUMENT = SOURCES.joinpath('every-kind.raml').read_text(encoding='utf-8')


class TestEveryKindLandsInTheContract:
    """One document declaring every kind, checked key by key against the file.

    The corpus form of this is `TestNothingArrivesUndeclared` in
    `tests/tck/test_properties.py`, which needs a checkout. This one always runs, so a facet added to a kind fails
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
        declared = declared_shape_members()
        assert not keys - declared, f'emitted but not in the contract: {sorted(keys - declared)}'

    def test_every_backend_declares_the_same_shape_fields(self):
        # With the test above, every backend declares every key that arrives.
        # The key *sets* come from one schema, but each backend decides which of
        # them it writes, and a backend that silently drops one is the failure
        # this whole file exists to catch.
        assert declared_shape_members() == python_shape_members()
        assert declared_shape_members() == go_shape_members()

    def test_the_document_reaches_the_facets_it_was_written_for(self, keys):
        # A vacuous pass is how a corpus-shaped test fails: an empty set of
        # observed keys satisfies the check above and proves nothing.
        assert {'minimum', 'multiple_of', 'pattern', 'items', 'properties', 'any_of', 'file_types'} <= keys

    def test_a_bound_arrives_as_an_exact_decimal_string(self, workspace):
        # The contract says so in a comment; this is what makes the comment
        # true. A fraction such as `1/100` is exact but is not what the author
        # wrote.
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


class TestTheGeneratedWalkReachesEveryShape:
    """The check a hand-written walk cannot make of itself.

    `CHILDREN` is the table that says where shapes sit, and the runtime half is
    generic over it. The failure it exists to prevent is the quiet one: a walk
    that descends everything it was told about, reaches most of the document,
    and never says what it skipped. So the question is asked of the *document*
    rather than of the table -- every `id` the projection emits anywhere must be
    a shape the walk arrives at.

    It found one on its first run. `raml-codegen`'s hand-written reachability
    never descended `entry_point.base_uri_parameters`, so a `$ref` naming a base
    URI parameter's type resolved to nothing -- and resolving to nothing is
    indistinguishable, at every call site, from a document that did not say it.
    """

    @pytest.fixture
    def document(self, workspace):
        root = workspace({'api.raml': DOCUMENT})
        return build_tree(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_every_addressed_shape_is_reached(self, document, tmp_path):
        walk = _vendored(tmp_path)
        assert _unreached(walk, document) == set()

    def test_it_reaches_the_sample_too(self, tmp_path):
        """The one worked document, which has an endpoint tree the other lacks."""
        walk = _vendored(tmp_path)
        document = json.loads((ROOT / CODEGEN_SAMPLE).read_text(encoding='utf-8'))
        assert _unreached(walk, document) == set()

    def test_a_link_and_a_marker_are_both_stops(self, document, tmp_path):
        """Neither is descended, which is what lets the walk keep no visited set."""
        tree = _vendored(tmp_path).Tree.of(document)
        assert list(tree.children({'$ref': 'fastraml://id#/x'})) == []
        assert list(tree.children({'type': 'recursive', 'name': 'Chain', 'id': 'fastraml://id#/y'})) == []


def _unreached(walk, document):
    """Addressed shapes the walk does not arrive at, by containment."""
    reached = {shape['id'] for shape in walk.Tree.of(document).shapes() if shape.get('id') is not None}
    return _addressed(document, walk.KINDS, set()) - reached


def _vendored(tmp_path):
    """Import the two halves the way a consumer vendors them: side by side."""
    import importlib.util

    package = tmp_path / 'vendored'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'tree.py').write_text(python(), encoding='utf-8')
    (package / 'walk.py').write_text(python_runtime(), encoding='utf-8')
    sys.path.insert(0, str(tmp_path))
    try:
        for name in ('vendored', 'vendored.tree', 'vendored.walk'):
            sys.modules.pop(name, None)
        return importlib.import_module('vendored.walk')
    finally:
        sys.path.remove(str(tmp_path))


def _addressed(value, kinds, found):
    """Every expanded shape's `id`, wherever it sits in the raw JSON.

    Asked of the JSON rather than through any reader, so the answer does not
    depend on the thing under test. `type in kinds` is what makes it *shapes*: a
    security scheme carries an `id` and a `type` too, and a recursion marker
    carries both and is a stop.
    """
    if isinstance(value, dict):
        if isinstance(value.get('id'), str) and value.get('type') in kinds:
            found.add(value['id'])
        for item in value.values():
            _addressed(item, kinds, found)
    elif isinstance(value, list):
        for item in value:
            _addressed(item, kinds, found)
    return found
