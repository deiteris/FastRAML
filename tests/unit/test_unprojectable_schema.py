"""A JSON Schema with no RAML projection degrades where it stands (docs/10 § 7).

`JsonShape.as_shape()` is `None` for it and `projection_error()` keeps why.
Every view reads the type as an opaque JSON Schema and everything else in the
document normally; the failure surfaces once, as the `unprojectable-json-schema`
lint finding (docs/18 § 2). Only `to_raml`, whose whole output is the
projection, raises it (docs/16 § 8).
"""

from __future__ import annotations

import json
from itertools import permutations
from typing import ClassVar

import pytest

import fastraml.types.jsonschema_ as module
from fastraml import ParseOptions, RamlError, to_openapi, to_raml
from fastraml.service import outline, queries
from fastraml.service.workspace import Workspace
from fastraml.types.complex_ import ObjectShape, RecursiveShape
from fastraml.types.jsonschema_ import JsonShape, projected, schema_registry
from fastraml.uris import path_to_file_uri
from fastraml.views.graph import build_graph
from fastraml.views.jsonschema import to_json_schema
from fastraml.views.lint import configured_linter
from fastraml.views.render import render
from fastraml.views.samples import SampleError, sample
from fastraml.views.tree import build_tree

NO_EQUIVALENT = 'JSON schema construct has no RAML equivalent'
UNSATISFIABLE = {'construct': 'unsatisfiable allOf'}
TUPLE = {'construct': 'tuple-form items'}
#: The lint finding's `pointer` for a whole schema file or an inline schema.
WHOLE = {'pointer': ''}

API = """#%RAML 1.0
title: t
types:
  Broken: !include broken.json
  Again: !include broken.json
  Tuple: |
    {"type": "array", "items": [{"type": "string"}]}
  Sound:
    properties:
      id: integer
/sound:
  get:
    responses:
      200:
        body:
          application/json: Sound
/broken:
  get:
    responses:
      200:
        body:
          application/json: Broken
"""

FILES = {'api.raml': API, 'broken.json': json.dumps({'allOf': [{'type': 'string'}, {'type': 'integer'}]})}


@pytest.fixture
def raml(memory_workspace):
    root = memory_workspace(FILES)
    return memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))


def declared(raml, name):
    return raml.types_in(raml.location)[name]


class TestTheFailureIsKeptNotRaised:
    def test_as_shape_is_none_and_the_error_is_the_projection_diagnostic(self, raml):
        shape = declared(raml, 'Broken').shape
        assert shape.as_shape() is None
        failure = shape.projection_error()
        assert (failure.head.message, failure.head.info) == (NO_EQUIVALENT, UNSATISFIABLE)
        tuple_failure = declared(raml, 'Tuple').shape.projection_error()
        assert (tuple_failure.head.message, tuple_failure.head.info) == (NO_EQUIVALENT, TUPLE)

    def test_a_sound_schema_has_no_error(self, memory_workspace):
        root = memory_workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  T: |\n    {"type": "string"}\n'})
        shape = declared(memory_workspace.parse(root / 'api.raml'), 'T').shape
        assert shape.projection_error() is None
        assert shape.as_shape() is not None

    def test_projected_is_the_opaque_leaf(self, raml):
        base = declared(raml, 'Broken')
        assert projected(base) is base
        assert isinstance(base.shape, JsonShape)
        assert base.shape.raw == FILES['broken.json']

    def test_the_failure_is_computed_once(self, raml, monkeypatch):

        calls = []
        walk = module._project

        def counted(*args, **kwargs):
            calls.append(args[1])
            return walk(*args, **kwargs)

        monkeypatch.setattr(module, '_project', counted)
        shape = declared(raml, 'Broken').shape
        first = shape.projection_error()
        walked = len(calls)
        assert walked
        assert shape.as_shape() is None
        assert shape.projection_error() is first
        assert len(calls) == walked

    def test_a_failure_shared_by_canonical_uri_is_one_failure(self, raml, monkeypatch):
        """`Broken` and `Again` include one schema file: one projection, so one failure."""

        first = declared(raml, 'Broken').shape.projection_error()
        monkeypatch.setattr(module, '_project', pytest.fail)
        assert declared(raml, 'Again').shape.projection_error() is first
        again = declared(raml, 'Again').shape
        assert schema_registry(raml).failed(again.canonical_uri, module._draft_of(again.validator)) is first


class TestEveryViewDegradesLocally:
    """One unprojectable schema does not take the document's views down with it."""

    def test_the_tree_carries_the_schema_without_a_projection(self, raml):
        types = build_tree(raml)['types']['api.raml']
        assert types['Broken']['type'] == 'json'
        assert types['Broken']['json_schema'] == json.loads(FILES['broken.json'])
        assert 'projection' not in types['Broken']
        assert 'projection' not in types['Tuple']
        assert list(types['Sound']['properties']) == ['id']

    def test_the_graph_holds_the_schema_type_as_a_leaf(self, raml):
        graph = build_graph(raml)
        (broken,) = graph.find('Broken')
        (sound,) = graph.find('Sound')
        # `nodes.TypeNode` reads its facets through `projected`.
        assert graph.nodes[broken].attributes['type'] == 'json'
        assert not graph.out(broken, ('property',))
        assert {graph.label(edge.object) for edge in graph.out(sound, ('property',))} == {'id'}

    def test_render_shows_the_schema_type_and_the_sound_type(self, raml):
        assert '\n'.join(render(declared(raml, 'Broken'), depth=3)).startswith('Broken:')
        assert 'id: integer' in '\n'.join(render(declared(raml, 'Sound')))

    def test_samples_refuse_only_the_schema_type(self, raml):
        assert isinstance(sample(declared(raml, 'Sound'))['id'], int)
        with pytest.raises(SampleError):
            sample(declared(raml, 'Broken'))

    def test_openapi_reports_the_loss_and_exports_the_rest(self, raml):
        document, notes = to_openapi(raml)
        schemas = document.components.schemas
        assert schemas['Sound'].properties['id'].type == 'integer'
        assert not schemas['Broken'].type
        assert any(note.startswith('components.schemas.Broken: JSON Schema could not be projected') for note in notes)

    def test_the_json_schema_export_is_the_schema_itself(self, raml):
        schema, dropped = to_json_schema(declared(raml, 'Broken'))
        assert schema['allOf'] == json.loads(FILES['broken.json'])['allOf']
        assert dropped == []

    def test_to_raml_raises_the_projection_error(self, raml):
        """The projection is the whole of that export, so it has nothing to degrade to."""
        shape = declared(raml, 'Broken').shape
        with pytest.raises(RamlError) as caught:
            to_raml(shape)
        assert caught.value is shape.projection_error()


class TestTheFailureIsALintFinding:
    def test_one_finding_per_failure_at_the_schema(self, raml):
        findings = [f for f in configured_linter({}).run(raml) if f.rule == 'unprojectable-json-schema']
        broken = declared(raml, 'Broken').shape.document_uri
        tuple_base = declared(raml, 'Tuple')
        assert sorted(((f.location, f.message, f.info) for f in findings), key=lambda found: found[2]['construct']) == [
            (tuple_base.location, NO_EQUIVALENT, {**TUPLE, **WHOLE}),
            (broken, NO_EQUIVALENT, {**UNSATISFIABLE, **WHOLE}),
        ]

    def test_an_inline_schema_is_reported_once_however_many_types_inherit_it(self, memory_workspace):
        document = (
            '#%RAML 1.0\ntitle: t\ntypes:\n'
            '  Tup: |\n    {"type": "array", "items": [{"type": "string"}]}\n'
            '  Child: Tup\n  Child2:\n    type: Tup\n'
        )
        root = memory_workspace({'api.raml': document})
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        findings = [f for f in configured_linter({}).run(raml) if f.rule == 'unprojectable-json-schema']
        assert [(f.message, f.info) for f in findings] == [(NO_EQUIVALENT, {**TUPLE, **WHOLE})]

    def test_two_failing_subschemas_of_one_file_are_told_apart(self, memory_workspace):
        unsatisfiable = {'allOf': [{'type': 'string'}, {'type': 'integer'}]}
        files = {
            'api.raml': (
                '#%RAML 1.0\ntitle: t\ntypes:\n'
                '  A: !include s.json#/definitions/A\n  B: !include s.json#/definitions/B\n'
            ),
            's.json': json.dumps({'definitions': {'A': unsatisfiable, 'B': unsatisfiable}}),
        }
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        findings = [f for f in configured_linter({}).run(raml) if f.rule == 'unprojectable-json-schema']
        assert sorted(f.info['pointer'] for f in findings) == ['/definitions/A', '/definitions/B']
        assert all(f.info['construct'] == 'unsatisfiable allOf' for f in findings)

    def test_two_false_schemas_in_two_files_are_two_findings(self, memory_workspace):
        """`false` is one Python object wherever it is written, so it identifies no schema."""
        files = {
            'api.raml': (
                '#%RAML 1.0\ntitle: t\ntypes:\n'
                '  X: !include a.json#/definitions/X\n  Y: !include b.json#/definitions/Y\n'
            ),
            'a.json': json.dumps({'definitions': {'X': False}}),
            'b.json': json.dumps({'definitions': {'Y': False}}),
        }
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        findings = [f for f in configured_linter({}).run(raml) if f.rule == 'unprojectable-json-schema']
        assert sorted((f.location.rpartition('/')[2], f.info['pointer'], f.info['construct']) for f in findings) == [
            ('a.json', '/definitions/X', 'false schema'),
            ('b.json', '/definitions/Y', 'false schema'),
        ]

    def test_the_language_service_answers_and_reports_it(self, memory_workspace):
        folder = path_to_file_uri(memory_workspace.root)
        workspace = Workspace([folder])
        for name, text in FILES.items():
            workspace.open(f'{folder}/{name}', text, 1)
        uri = f'{folder}/api.raml'
        snapshot = workspace.snapshot(uri)
        found = queries.diagnostics(snapshot)
        assert [(d.code, d.info) for d in found[uri] if d.code == 'unprojectable-json-schema'] == [
            ('unprojectable-json-schema', {**TUPLE, **WHOLE})
        ]
        assert [(d.code, d.info) for d in found[f'{folder}/broken.json']] == [
            ('unprojectable-json-schema', {**UNSATISFIABLE, **WHOLE})
        ]
        assert queries.tree(snapshot) is not None
        names = [symbol.name for section in outline.document_symbols(snapshot, uri) for symbol in section.children]
        assert {'Broken', 'Tuple', 'Sound'} <= set(names)


class TestTheOutcomeDoesNotDependOnTheEntry:
    """docs/10 § 7: a cycle through a schema with a type head is productive from any entry."""

    FILES: ClassVar = {
        # `loop.json` is a reference to `holder.json`, whose `next` refers back:
        # the cycle passes through an object, so it has a head.
        'loop.json': json.dumps({'$ref': 'holder.json'}),
        'holder.json': json.dumps({'type': 'object', 'properties': {'next': {'$ref': 'loop.json'}}}),
    }
    INCLUDES: ClassVar = {'Loop': 'loop.json', 'Loop2': 'loop.json', 'Holder': 'holder.json'}

    @pytest.mark.parametrize('order', list(permutations(INCLUDES)), ids='-'.join)
    def test_every_type_projects_in_every_order(self, memory_workspace, order):
        types = ''.join(f'  {name}: !include {self.INCLUDES[name]}\n' for name in order)
        root = memory_workspace({'api.raml': f'#%RAML 1.0\ntitle: t\ntypes:\n{types}', **self.FILES})
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        for name in order:
            shape = declared(raml, name).shape
            assert shape.projection_error() is None, name
            assert isinstance(shape.as_shape().shape, ObjectShape), name
            assert set(shape.as_shape().shape.properties) == {'next'}, name

    CYCLES: ClassVar = {
        'allOf': (
            {'type': 'object', 'properties': {'next': {'allOf': [{'$ref': 'b.json'}, {'type': 'object'}]}}},
            {'$ref': 'a.json'},
            {'A': 'ObjectShape', 'A2': 'ObjectShape', 'B': 'ObjectShape'},
        ),
        'anyOf': (
            {'type': 'object', 'properties': {'next': {'anyOf': [{'$ref': 'b.json'}, {'type': 'null'}]}}},
            {'$ref': 'a.json'},
            {'A': 'ObjectShape', 'A2': 'ObjectShape', 'B': 'ObjectShape'},
        ),
        'items': (
            {'type': 'array', 'items': {'$ref': 'b.json'}},
            {'type': 'object', 'properties': {'children': {'$ref': 'a.json'}}},
            {'A': 'ArrayShape', 'A2': 'ArrayShape', 'B': 'ObjectShape'},
        ),
    }

    @pytest.mark.parametrize('order', list(permutations(('A', 'A2', 'B'))), ids='-'.join)
    @pytest.mark.parametrize('through', list(CYCLES))
    def test_a_cycle_through_each_applicator_projects_from_every_entry(self, memory_workspace, through, order):
        a, b, kinds = self.CYCLES[through]
        includes = {'A': 'a.json', 'A2': 'a.json', 'B': 'b.json'}
        types = ''.join(f'  {name}: !include {includes[name]}\n' for name in order)
        files = {'api.raml': f'#%RAML 1.0\ntitle: t\ntypes:\n{types}', 'a.json': json.dumps(a), 'b.json': json.dumps(b)}
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        found = {}
        for name in order:
            shape = declared(raml, name).shape
            assert shape.projection_error() is None, name
            found[name] = type(shape.as_shape().shape).__name__
        assert found == kinds

    def test_a_cycle_flattened_through_an_all_of_member_is_recursion(self, memory_workspace):
        """docs/12 § 3: re-entering an open subschema without a `$ref` is a back-edge, not a `RecursionError`."""
        schema = {'type': 'object', 'properties': {'n': {'allOf': [{'$ref': 'h.json'}, {'type': 'object'}]}}}
        root = memory_workspace(
            {'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  H: !include h.json\n', 'h.json': json.dumps(schema)}
        )
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        shape = declared(raml, 'H').shape
        assert shape.projection_error() is None
        inner = shape.as_shape().shape.properties['n'].base.shape
        assert isinstance(inner, ObjectShape)
        assert isinstance(inner.properties['n'].base.shape, RecursiveShape)

    @pytest.mark.parametrize('order', [('S', 'W'), ('W', 'S')], ids='-'.join)
    def test_a_document_naming_no_draft_is_read_in_each_entry_draft(self, memory_workspace, order):
        """`dependencies` is a draft 7 keyword and a 2020-12 annotation, so the
        same file fails in one reading and projects in the other, in either order.
        """
        files = {
            'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n'
            + ''.join(f'  {n}: !include {n.lower()}.json\n' for n in order),
            's.json': json.dumps({'allOf': [{'type': 'object'}], 'dependencies': {'a': ['b']}}),
            'w.json': json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$ref': 's.json'}),
        }
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        outcomes = {}
        for name in order:
            failure = declared(raml, name).shape.projection_error()
            outcomes[name] = None if failure is None else failure.head.info
        for name in order:
            # Final: reading the other type did not change this one's answer.
            failure = declared(raml, name).shape.projection_error()
            assert (None if failure is None else failure.head.info) == outcomes[name], name
        assert outcomes == {'S': {'construct': 'allOf keyword: dependencies'}, 'W': None}
        assert isinstance(declared(raml, 'W').shape.as_shape().shape, ObjectShape)

    @pytest.mark.parametrize('order', [('S', 'W'), ('W', 'S')], ids='-'.join)
    def test_ref_siblings_are_read_in_each_entry_draft(self, memory_workspace, order):
        """Draft 7 ignores a `$ref`'s siblings and 2020-12 applies them, so the
        `if` beside it is nothing to S and an unprojectable conditional to W.
        """
        s = {'$ref': '#/definitions/X', 'if': {'type': 'object'}, 'definitions': {'X': {'type': 'object'}}}
        files = {
            'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n'
            + ''.join(f'  {n}: !include {n.lower()}.json\n' for n in order),
            's.json': json.dumps(s),
            'w.json': json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$ref': 's.json'}),
        }
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))

        def outcome(name):
            failure = declared(raml, name).shape.projection_error()
            return None if failure is None else failure.head.info

        first = {name: outcome(name) for name in order}
        assert first == {'S': None, 'W': {'construct': 'allOf keyword: if'}}
        assert {name: outcome(name) for name in order} == first
        assert isinstance(declared(raml, 'S').shape.as_shape().shape, ObjectShape)

    def test_a_reference_only_cycle_fails_from_every_entry(self, memory_workspace):
        files = {
            'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  A: !include a.json\n  B: !include b.json\n',
            'a.json': json.dumps({'$ref': 'b.json'}),
            'b.json': json.dumps({'$ref': 'a.json'}),
        }
        root = memory_workspace(files)
        raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        for name in ('B', 'A'):
            failure = declared(raml, name).shape.projection_error()
            assert (failure.head.message, failure.head.info) == (NO_EQUIVALENT, {'construct': 'reference-only cycle'})


def test_a_shared_projection_wins_over_a_cached_failure(raml):
    """`as_shape` reads the registry's projection before the shape's own failure.

    One subschema is one projection (docs/10 § 7): once any walk has shared
    one under the URI, a failure cached earlier no longer answers for it.
    """
    shape = declared(raml, 'Broken').shape
    assert shape.projection_error() is not None
    sound = declared(raml, 'Sound')
    schema_registry(raml).share(shape.canonical_uri, module._draft_of(shape.validator), sound, {})
    assert shape.as_shape() is sound
    assert shape.projection_error() is None


class TestOneFileReadInTwoDrafts:
    """docs/10 § 7: one file read in two drafts is two projected shapes at one location.

    `P`'s `k` is a `$ref` with a sibling: a plain string to the draft 7 type
    `S`, a string of at most 3 characters to the 2020-12 schema `W` that refers
    to `s.json`. So `s.json#/definitions/P` is two different shapes.
    """

    S: ClassVar = {
        'type': 'object',
        'properties': {'p': {'$ref': '#/definitions/P'}},
        'definitions': {
            'P': {'type': 'object', 'properties': {'k': {'$ref': '#/definitions/Str', 'maxLength': 3}}},
            'Str': {'type': 'string'},
        },
    }
    W: ClassVar = {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$ref': 's.json'}
    BODY = '    responses:\n      200:\n        body:\n          application/json: {0}\n'

    @pytest.fixture(params=[('S', 'W'), ('W', 'S')], ids='-'.join)
    def raml(self, memory_workspace, request):
        declarations = ''.join(f'  {name}: !include {name.lower()}.json\n' for name in request.param)
        resources = ''.join(f'/{name.lower()}:\n  get:\n' + self.BODY.format(name) for name in request.param)
        files = {
            'api.raml': f'#%RAML 1.0\ntitle: t\ntypes:\n{declarations}{resources}',
            's.json': json.dumps(self.S),
            'w.json': json.dumps(self.W),
        }
        root = memory_workspace(files)
        return memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, retain_text=True))

    @staticmethod
    def readings(raml):
        """`P` as `S` reads it and as `W` reads it."""
        return [declared(raml, name).shape.as_shape().shape.properties['p'].base for name in ('S', 'W')]

    def test_p_is_two_shapes_at_one_location(self, raml):
        s, w = self.readings(raml)
        assert s is not w
        assert s.location == w.location
        assert s.shape.properties['k'].base.shape.max_length is None
        assert w.shape.properties['k'].base.shape.max_length.value == 3

    def test_the_graph_and_the_tree_give_each_reading_its_own_address(self, raml):
        """I11: one address per entity, and every address the tree emits is the graph's."""
        graph = build_graph(raml)
        iris = {node.entity.id: iri for iri, node in graph.nodes.items() if getattr(node, 'kind', '') == 'Type'}
        s, w = self.readings(raml)
        assert iris[s.id] != iris[w.id]
        found = []

        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get('id'), str):
                    found.append(value['id'])
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(build_tree(raml))
        assert {iris[s.id], iris[w.id]} <= set(found)
        assert set(found) <= graph.nodes.keys()

    def test_the_occurrence_index_builds(self, raml):
        from fastraml.views.occurrences import build_occurrences

        assert build_occurrences(raml) is not None

    def test_openapi_gives_each_reading_its_own_component(self, raml):
        """A component joins shapes by schema URI within one reading only, so the
        2020-12 `P` keeps its `maxLength` whichever type was exported first.
        """
        document, notes = to_openapi(raml)
        assert notes == []
        schemas = document.components.schemas
        lengths = {}
        for name in ('S', 'W'):
            component = schemas[name]
            p = component.properties['p']
            body = schemas[p.ref.rpartition('/')[2]] if p.ref else p
            k = body.properties['k']
            lengths[name] = (schemas[k.ref.rpartition('/')[2]] if k.ref else k).max_length
        assert lengths == {'S': None, 'W': 3}
