"""The generated corpora have to be valid RAML, in all four configurations.

This runs in the ordinary suite, at a scale small enough to be free, because a
benchmark corpus that only parses with `validate=False` measures the wrong
thing silently: an invalid example makes the `validate` configurations time an
exception.
"""

from __future__ import annotations

import json

import pytest

from bench import corpus
from fastraml import ParseOptions, parse_from_path
from tests.shapes import unshared_empties

CONFIGURATIONS = [
    pytest.param(ParseOptions(), id='parse'),
    pytest.param(ParseOptions(unwrap=True), id='unwrap'),
    pytest.param(ParseOptions(validate=True), id='validate'),
    pytest.param(ParseOptions(unwrap=True, validate=True), id='unwrap+validate'),
]

WRITERS = {
    'small': lambda root: corpus.write_small(root, type_count=12),
    'large': lambda root: corpus.write_large(root, type_count=24, library_count=4),
    'endpoints': lambda root: corpus.write_endpoints(root, resource_count=3),
    'extensions': lambda root: corpus.write_extensions(root, resource_count=11),
    'validate': lambda root: corpus.write_validate(root, type_count=3),
    'jsonschema': lambda root: corpus.write_jsonschema(root, schema_count=6, shared_count=2),
    'datatype-fragments': lambda root: corpus.write_datatype_fragments(root, fragment_count=3),
    'schema-export': lambda root: corpus.write_jsonschema(root, schema_count=6, shared_count=2),
    'schema-allof': lambda root: corpus.write_schema_allof(root, schema_count=6),
    'raml-schema': lambda root: corpus.write_validate(root, type_count=6),
    'projections': lambda root: corpus.write_projections(root, family_count=3),
    'enums': lambda root: corpus.write_enums(root, family_count=1),
    'unions': lambda root: corpus.write_unions(root, family_count=1),
    'facets': lambda root: corpus.write_facets(root, family_count=1),
    'inheritance': lambda root: corpus.write_inheritance(root, family_count=1),
    'diamonds': lambda root: corpus.write_diamonds(root, family_count=1),
    'sequence-merge': lambda root: corpus.write_sequence_merge(root, resource_count=len(corpus.MERGED_ENUM_SIZES)),
    'includes': lambda root: corpus.write_includes(root, resource_count=corpus._LEADING_TAB_EVERY + 1),
    'include-content': lambda root: corpus.write_include_content(root, resource_count=3),
    'inline-json': lambda root: corpus.write_inline_json(root, type_count=3),
    'template-scopes': lambda root: corpus.write_template_scopes(root, resource_count=3),
    'reference-namespaces': lambda root: corpus.write_reference_namespaces(root, resource_count=3),
    'annotation-targets': lambda root: corpus.write_annotation_targets(root, family_count=3),
    'doc-links': lambda root: corpus.write_doc_links(root, resource_count=3),
    'non-strict-examples': lambda root: corpus.write_non_strict_examples(root, family_count=3),
    'hover': lambda root: corpus.write_hover(root, family_count=3),
    'effective-types': lambda root: corpus.write_hover(root, family_count=3),
    'inlays': lambda root: corpus.write_hover(root, family_count=3),
    'source-structure': lambda root: corpus.write_hover(root, family_count=3),
    'service-session': lambda root: corpus.write_hover(root, family_count=3),
    'service-source-first': lambda root: corpus.write_hover(root, family_count=3),
    'service-navigation': lambda root: corpus.write_hover(root, family_count=3),
}


class TestFeatureCorporaReachTheirCode:
    """A feature corpus that stops reaching its code measures nothing, silently.

    The general corpora never called the enum subset check or `uniqueItems`,
    so every delta reported for a change there was noise on unchanged code
    (docs/12 § 4). Each feature corpus pins, by counting calls, that it runs the
    code it was written for, at every size it was written to cover.
    """

    @pytest.mark.parametrize('count', [2, 64])
    def test_non_strict_examples_reaches_warnings_and_shared_site_deduplication(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.views.lint import Linter, Severity

        results = []
        original = Linter.report

        def report(linter, raml, **kwargs):
            result = original(linter, raml, **kwargs)
            findings = [finding for finding in result.findings if finding.rule == 'non-strict-example']
            assert len(findings) == count * 2
            assert {finding.info['example'] for finding in findings} == {'example', 'sample'}
            assert all(finding.severity is Severity.WARNING for finding in findings)
            assert len({finding.where for finding in findings}) == count * 2
            results.append(result)
            return result

        monkeypatch.setattr(Linter, 'report', report)
        entry = corpus.write_non_strict_examples(tmp_path, family_count=count)
        run_one('non-strict-examples', 'unwrap+lint', entry, repeat=1)
        assert len(results) == 2, 'timing and allocation both reach the warnings'

    @pytest.mark.parametrize('count', [2, 4])
    def test_lenient_recovery_reaches_local_and_stage_boundaries(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml import Stage, parse_lenient

        results = []

        def parse(entry, options):
            raml, error = parse_lenient(entry, options)
            assert error is not None
            assert len(list(error.chains())) == count * 4 + 2
            assert raml.stopped_at is None
            assert Stage.UNWRAPPED in raml.completed
            assert (Stage.VALIDATED in raml.completed) is options.validate
            assert len(raml.endpoints) == count
            for endpoint in raml.endpoints.values():
                operation = endpoint.operations['get']
                bad, included, missing, good = operation.secured_by
                assert bad.id in raml.broken
                assert bad.compiled_params is None
                assert included.id in raml.broken
                assert included.definition.link is not None
                assert missing.id in raml.broken
                assert missing.definition is None
                assert good.id not in raml.broken
                assert operation.id in raml.broken
                assert list(operation.request.query_parameters) == ['name', 'after']
                name = operation.request.query_parameters['name'].base
                assert name.validate('Alice') is None
                assert name.validate('A') is not None
            results.append(raml)
            return raml, error

        monkeypatch.setattr('fastraml.parse_lenient', parse)
        entry = corpus.write_lenient_recovery(tmp_path, family_count=count)
        run_one('lenient-recovery', 'unwrap+validate', entry, repeat=1)
        run_one('lenient-recovery', 'unwrap+lint', entry, repeat=1)
        assert len(results) == 4, 'timing and allocation both reach recovery and lint'

    @pytest.mark.parametrize('count', [2, 4])
    def test_source_structure_reaches_folding_and_selection_on_the_cached_tree(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.service import queries
        from fastraml.service import source as source_module

        folded = []
        selected = []
        compositions = 0
        original_folding = queries.folding_ranges_of
        original_selection = queries.selection_ranges_of
        original_compose = source_module.compose

        def folding(root):
            result = original_folding(root)
            assert result
            folded.append(len(result))
            return result

        def selection(root, line, column):
            result = original_selection(root, line, column)
            assert len(result) > 1, 'a position inside a nested structure spans at least root, pair and token'
            selected.append(len(result))
            return result

        def compose(text, **kwargs):
            nonlocal compositions
            compositions += 1
            return original_compose(text, **kwargs)

        monkeypatch.setattr(queries, 'folding_ranges_of', folding)
        monkeypatch.setattr(queries, 'selection_ranges_of', selection)
        monkeypatch.setattr(source_module, 'compose', compose)
        entry = corpus.write_hover(tmp_path, family_count=count)
        run_one('source-structure', 'unwrap', entry, repeat=1)
        assert len(folded) == 2 * 3, 'timing and allocation both reach folding'
        assert len(selected) == 2 * 12, 'timing and allocation both reach selection'
        assert compositions == 2, 'each measured pass composes the text once, shared by fifteen requests'

    @pytest.mark.parametrize('count', [2, 4])
    def test_inlays_reaches_inferred_types_data_types_and_inherited_facets(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.service import inlays
        from fastraml.service.hover import Hover

        labels = []
        passes = 0
        original = inlays.inlay_hints

        def source(*args, **kwargs):
            pytest.fail('model-backed inlays must not compose source or read its grammar')

        def hints(snapshot, uri, span):
            nonlocal passes
            passes += 1
            result = original(snapshot, uri, span)
            labels.extend(hint.label for hint in result)
            return result

        monkeypatch.setattr(Hover, '_node', source)
        monkeypatch.setattr(Hover, '_keys_at', source)
        monkeypatch.setattr(inlays, 'inlay_hints', hints)
        entry = corpus.write_hover(tmp_path, family_count=count)
        run_one('inlays', 'unwrap', entry, repeat=1)
        assert passes == 2, 'timing and allocation both reach model-backed hints'
        for label in ('[object]', '[string]', '[string; length: ≥2]'):
            assert labels.count(label) >= count

    @pytest.mark.parametrize('count', [2, 4])
    def test_effective_types_reaches_each_code_lens_rendering(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.service import lenses

        calls = set()
        original = lenses.effective_type

        def effective(snapshot, uri, line, column, *, name):
            text = original(snapshot, uri, line, column, name=name)
            assert text is not None
            assert text.startswith('#%RAML 1.0 DataType')
            if name.startswith('MetadataLeaf'):
                assert 'items: Metadata' in text
            elif name.startswith('Metadata'):
                assert 'anyOf:' in text
                assert 'minLength: 3' in text
                assert 'items:' in text
            calls.add(name)
            return text

        monkeypatch.setattr(lenses, 'effective_type', effective)
        entry = corpus.write_hover(tmp_path, family_count=count)
        run_one('effective-types', 'unwrap', entry, repeat=1)
        assert len(calls) == 6 * count

    @pytest.mark.parametrize('count', [2, 4])
    def test_hover_reaches_syntax_primitives_authorship_and_http_help(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.service import queries

        calls = set()
        texts = []
        original = queries.hover

        def hover(snapshot, uri, line, column):
            result = original(snapshot, uri, line, column)
            assert result is not None
            calls.add((uri, line, column))
            texts.append(result[0])
            return result

        monkeypatch.setattr(queries, 'hover', hover)
        entry = corpus.write_hover(tmp_path, family_count=count)
        run_one('hover', 'parse', entry, repeat=1)
        assert not texts
        run_one('hover', 'unwrap', entry, repeat=1)
        assert len(calls) == 14 * count
        for meaning in (
            'Specializes `Word',
            'minimum length',
            'Unicode characters',
            'object property',
            'HTTP method',
            '404 Not Found',
            'Explains how this name is used.',
            'Explains the nested note.',
        ):
            assert any(meaning in text for text in texts), meaning

    @pytest.mark.parametrize('count', [2, 4])
    def test_projections_reaches_narrowing_patterns_and_typed_enums(self, tmp_path, monkeypatch, count):
        import yaml

        import fastraml.views.jsonschema as schema_module
        import fastraml.views.openapi as openapi_module
        import fastraml.views.render as render_module
        from bench.__main__ import run_one

        documents, schemas, displays = [], [], []
        original_openapi = openapi_module.to_openapi
        original_schema = schema_module.to_json_schema
        original_render = render_module.render

        def openapi(raml):
            result = original_openapi(raml)
            documents.append(result[0].to_dict())
            return result

        def schema(base):
            result = original_schema(base)
            schemas.append((base.name, result[0]))
            return result

        def render(base):
            result = list(original_render(base))
            displays.append(yaml.safe_load('\n'.join(result)))
            return iter(result)

        monkeypatch.setattr(openapi_module, 'to_openapi', openapi)
        monkeypatch.setattr(schema_module, 'to_json_schema', schema)
        monkeypatch.setattr(render_module, 'render', render)
        entry = corpus.write_projections(tmp_path, family_count=count)
        run_one('projections', 'parse', entry, repeat=1)
        assert not documents
        assert not schemas
        assert not displays
        run_one('projections', 'unwrap', entry, repeat=1)
        assert documents
        assert len(schemas) == len(displays) == len(documents) * 5 * count
        for document in documents:
            assert len(document['paths']) == count
            for path in document['paths'].values():
                response = path['post']['responses']['200']['content']['application/json']['schema']
                assert response['maxLength'] == 3
                body = path['post']['requestBody']['content']['application/json']['schema']
                assert set(body['properties']) == {'name', 'age'}
                assert body['additionalProperties'] is False
                unchanged = path['post']['responses']['201']['content']['application/json']['schema']
                alias = path['post']['responses']['202']['content']['application/json']['schema']
                redeclared = path['post']['responses']['203']['content']['application/json']['schema']
                assert set(unchanged) == set(alias) == set(redeclared) == {'$ref'}
        for name, exported in schemas:
            if name.startswith('Patterned'):
                patterns = exported['definitions'][name]['patternProperties']
                assert len(patterns) == 3
                assert all(pattern.startswith('^(?!') for pattern in patterns)
            if name.startswith('Captured'):
                assert set(exported['definitions'][name]['patternProperties']) == {'(x)', r'(a)\1'}
        for display in displays:
            for name, shown in display.items():
                if name.startswith('Choice'):
                    assert shown['enum'] == [1, 2]

    @pytest.mark.parametrize('count', [2, 4])
    def test_datatype_fragments_projects_every_shared_root(self, tmp_path, monkeypatch, count):
        import fastraml.views.graph as graph_module
        import fastraml.views.tree as tree_module
        from bench.__main__ import run_one

        projected = []
        graphs = []
        positions = []
        original = tree_module.build_tree
        original_graph = graph_module.build_graph
        original_positions = tree_module.positions_of

        def counting_graph(raml):
            graph = original_graph(raml)
            graphs.append(graph)
            return graph

        def counting_positions(raml):
            found = original_positions(raml)
            positions.append(found)
            return found

        def counting(raml, **kwargs):
            tree = original(raml, **kwargs)
            projected.append(tree)
            assert kwargs['addresses'] is graphs[-1].addresses
            assert all(
                root['id'] in graphs[-1].nodes
                for file, declared in tree['types'].items()
                if file != 'api.raml'
                for root in declared.values()
            )
            return tree

        monkeypatch.setattr(tree_module, 'build_tree', counting)
        monkeypatch.setattr(graph_module, 'build_graph', counting_graph)
        monkeypatch.setattr(tree_module, 'positions_of', counting_positions)
        entry = corpus.write_datatype_fragments(tmp_path, fragment_count=count)
        run_one('datatype-fragments', 'parse', entry, repeat=1)
        assert not projected, 'parse must not time a projection'
        assert not graphs
        assert not positions
        run_one('datatype-fragments', 'unwrap', entry, repeat=1)
        assert projected
        assert len(projected) == len(graphs) == len(positions)
        for tree, placed in zip(projected, positions, strict=True):
            assert len(tree['types']) == 2 * count + 2
            assert len(tree['annotation_types']) == count + 1
            for index in range(count):
                fragment = tree['types'][f'models/{index}/user.raml']['user.raml']
                expected = [{'$ref': fragment['id']}]
                assert tree['types']['api.raml'][f'A{index}']['inherits'] == expected
                assert tree['types']['api.raml'][f'B{index}']['inherits'] == expected
                body = tree['endpoints'][f'/users{index}']['operations']['get']['responses']['200']['bodies'][
                    'application/json'
                ]
                assert body['inherits'] == expected
                collapsed = tree['types'][f'models/{index}/narrowed.raml']['narrowed.raml']
                assert collapsed['type'] == 'object'
                assert tree['types']['api.raml'][f'C{index}']['inherits'] == [{'$ref': collapsed['id']}]
                annotation = tree['annotation_types'][f'annotations/{index}/tag.raml']['tag.raml']
                assert tree['annotation_types']['api.raml'][f'tag{index}']['inherits'] == [{'$ref': annotation['id']}]
                assert set(placed[f'models/{index}/user.raml']) == {'user.raml'}
                assert set(placed[f'models/{index}/narrowed.raml']) == {'narrowed.raml'}
                assert set(placed[f'annotations/{index}/tag.raml']) == {'tag.raml'}

    @pytest.mark.parametrize('count', [6, 12])
    def test_schema_allof_projects_every_composite_without_narrowing_shared_refs(self, tmp_path, monkeypatch, count):
        import fastraml.types.schema_intersection as intersection_module
        import fastraml.types.schema_projection as schema_module
        from fastraml import build_graph

        calls = []
        original = schema_module.intersect

        def counting(context, contents, *args, **kwargs):
            if kwargs.get('strict', True):  # the `allOf` path; a plain body intersects with `strict=False`
                calls.append(contents['allOf'])
            return original(context, contents, *args, **kwargs)

        monkeypatch.setattr(schema_module, 'intersect', counting)
        references = []
        original_parts = intersection_module._parts

        def counting_parts(context, contents, *args):
            if isinstance(contents, dict) and contents.get('$ref', '').startswith('#/definitions/required'):
                references.append(contents['$ref'])
            yield from original_parts(context, contents, *args)

        monkeypatch.setattr(intersection_module, '_parts', counting_parts)
        uuid_calls = []
        original_string = intersection_module._string

        def counting_string(context, parts, base, *, strict):
            if any(contents.get('format') == 'uuid' for _, contents in parts):
                uuid_calls.append(base)
            return original_string(context, parts, base, strict=strict)

        monkeypatch.setattr(intersection_module, '_string', counting_string)
        raml = parse_from_path(corpus.write_schema_allof(tmp_path, schema_count=count), ParseOptions(unwrap=True))
        build_graph(raml)
        assert len(calls) == count
        assert len(references) == 17 * count
        assert len(uuid_calls) == count + 1
        assert sum(members[0] is True or members[0] in ({}, {'$ref': 'base.json'}) for members in calls) == count // 2
        declared = raml.types_in(raml.location)
        for index in range(count):
            declaration = declared[f'S{index}']
            projection = declaration.shape.as_shape()
            assert set(projection.shape.properties) == {'extra', 'amount', 'tags', 'code', 'limit', 'node', 'id'}
            assert [value.raw for value in projection.shape.properties['code'].base.enum] == [f'v{index}']
            amount = projection.shape.properties['amount'].base.shape
            assert amount.minimum.value == 5
            assert amount.multiple_of.value == 6
            assert projection.shape.properties['tags'].base.shape.items.shape.max_length.value == 4
            assert projection.shape.properties['limit'].base is declared['Limit'].shape.as_shape()
            node = declared['Node'].shape.as_shape()
            assert projection.shape.properties['node'].base is node
            assert node.shape.properties['next'].base.shape.head is node
            identifier = projection.shape.properties['id'].base
            assert identifier.shape.min_length.value == identifier.shape.max_length.value == 36
            assert identifier.validate('123e4567-e89b-12d3-a456-426614174000') is None
            assert identifier.validate('123e4567-e89b-12d3-a456-426614174000\n') is not None
        assert declared['Record'].shape.as_shape().shape.properties['code'].base.enum is None
        assert declared['Base'].shape.as_shape().type == 'any'

    @pytest.mark.parametrize('count', [2, 4])
    def test_doc_links_parses_each_text_once_and_reaches_every_outcome(self, tmp_path, monkeypatch, count):
        from bench.__main__ import run_one
        from fastraml.views.doclinks import DocLinks, Kind, Outcome

        parsed: dict[int, list[str]] = {}
        # Held, so no two resolvers share an `id` by one outliving the other.
        resolvers = []
        found = []
        original = DocLinks._parse

        def counting(self, text, scope):
            links = original(self, text, scope)
            resolvers.append(self)
            parsed.setdefault(id(self), []).append(text)
            found.extend(links)
            return links

        monkeypatch.setattr(DocLinks, '_parse', counting)
        entry = corpus.write_doc_links(tmp_path, resource_count=count)
        run_one('doc-links', 'parse', entry, repeat=1)
        assert not parsed, 'parse must not time the links'
        run_one('doc-links', 'unwrap', entry, repeat=1)
        # Per resolver, each text once: the inherited description and the
        # trait's are each one text however many entities carry them, and
        # prose with no `[` is not parsed. Four shared texts, four per item.
        assert parsed
        for texts in parsed.values():
            assert len(texts) == len(set(texts)) == 4 + 4 * count
        assert {link.target.kind for link in found if link.target is not None} == {
            Kind.TYPE,
            Kind.METHOD,
            Kind.ENDPOINT,
            Kind.DOCUMENTATION,
        }
        assert {(link.written, link.outcome, link.explicit) for link in found if link.target is None} == {
            ('`Nope`', Outcome.UNRESOLVED, True),
            ('optional', Outcome.UNRESOLVED, False),
        }

    @pytest.mark.parametrize('count', [2, 4])
    def test_annotation_targets_reaches_each_restriction_and_scalar_path(self, tmp_path, monkeypatch, count):
        from fastraml.domains import DomainLocation
        from fastraml.registry import Raml

        looked_up = []
        scope_for = Raml.scope_for

        def counting(raml, node):
            if node in raml.substitutions and node.value == 'Base':
                looked_up.append(node)
            return scope_for(raml, node)

        monkeypatch.setattr(Raml, 'scope_for', counting)

        raml = parse_from_path(
            corpus.write_annotation_targets(tmp_path, family_count=count), ParseOptions(unwrap=True, validate=True)
        )
        targets = [extension.target for extension in raml.domain_extensions]
        assert len(set(looked_up)) == count, 'the annotated type names must reach provenance lookup'
        assert targets.count(DomainLocation.TYPE_DECLARATION) == count * 8
        assert targets.count(DomainLocation.TRAIT) == count * 2
        assert targets.count(DomainLocation.RESOURCE_TYPE) == count
        assert targets.count(DomainLocation.REQUEST_BODY) == count
        assert targets.count(DomainLocation.RESPONSE_BODY) == count
        for index in range(count):
            assert raml.entry_point.annotation_types[f'data{index}'].allowed_targets == [
                DomainLocation.TYPE_DECLARATION
            ]
            assert raml.entry_point.annotation_types[f'combined{index}'].allowed_targets == [
                DomainLocation.TYPE_DECLARATION
            ]
            assert raml.entry_point.types[f'T{index}'].default.raw == f'v{index}'
            assert raml.entry_point.types[f'O{index}'].shape.discriminator_value.raw == f'v{index}'
            assert raml.endpoints[f'/r{index}'].operations['get'].annotations['dynamic'].value.raw == f'v{index}'

    @pytest.mark.parametrize('count', [2, 4])
    def test_reference_namespaces_binds_caller_names_and_preserves_static_names(self, tmp_path, count):
        raml = parse_from_path(
            corpus.write_reference_namespaces(tmp_path, resource_count=count), ParseOptions(unwrap=True, validate=True)
        )
        api = raml.entry_point
        library = api.uses['lib'].link
        assert len(raml.endpoints) == count
        for endpoint in raml.endpoints.values():
            operation = endpoint.operations['get']
            assert operation.description.value == 'caller'
            assert operation.traits[1].resolved is api.traits['chosen']
            assert operation.secured_by[0].definition is api.security_schemes['chosen']
            assert operation.request.query_string.alias is api.types['Model']
            assert operation.annotations['dynamic'].defined_by is api.annotation_types['dynamic']
            assert operation.annotations['fixed'].defined_by is library.annotation_types['fixed']

    @pytest.mark.parametrize('count', [2, 4])
    def test_template_scopes_binds_library_schemes_and_parameter_annotations(self, tmp_path, count):
        from fastraml.domains import DomainLocation

        raml = parse_from_path(
            corpus.write_template_scopes(tmp_path, resource_count=count), ParseOptions(unwrap=True, validate=True)
        )
        library = raml.entry_point.uses['lib'].link
        assert len(raml.endpoints) == count
        for endpoint in raml.endpoints.values():
            operation = endpoint.operations['get']
            assert operation.secured_by[0].definition is library.security_schemes['basic']
            extension = operation.request.query_parameters['id'].declaration.base.annotations['ref']
            assert extension.target is DomainLocation.TYPE_DECLARATION
            assert extension.defined_by is library.annotation_types['ref']
            assert operation.secured_by[0].location == (tmp_path / 'secured.yaml').as_uri()
            imported = endpoint.operations['post'].secured_by[0]
            assert imported.definition.location == (tmp_path / 'auth.raml').as_uri()
            assert imported.location == (tmp_path / 'imported.raml').as_uri()

    @pytest.mark.parametrize('count', [2, 4])
    def test_inline_json_decodes_every_string_once(self, tmp_path, monkeypatch, count):
        decoded: list[str] = []
        original = json.loads

        def counting(value, *args, **kwargs):
            if isinstance(value, str) and value.startswith('"'):
                decoded.append(value)
            return original(value, *args, **kwargs)

        entry = corpus.write_inline_json(tmp_path, type_count=count)
        monkeypatch.setattr(json, 'loads', counting)
        raml = parse_from_path(entry, ParseOptions(unwrap=True, validate=True))
        assert len(decoded) == count * 5
        assert set(decoded) == {json.dumps(f'{{"attr":{index}}}') for index in range(count)}
        for index in range(count):
            assert raml.types_in(raml.location)[f'T{index}'].example.data.raw == f'{{"attr":{index}}}'

    def test_enums_runs_the_subset_check_at_every_size(self, tmp_path, monkeypatch):
        import fastraml.types.inherit as inherit_module

        sizes: list[int] = []
        original = inherit_module._is_subset

        def counting(target, source):
            sizes.append(len(source))
            return original(target, source)

        monkeypatch.setattr(inherit_module, '_is_subset', counting)
        parse_from_path(corpus.write_enums(tmp_path, family_count=1), ParseOptions(unwrap=True))
        # Parent to child at every size, then child to grandchild at every
        # second value of it.
        expected = {*corpus.ENUM_SIZES, *((size + 1) // 2 for size in corpus.ENUM_SIZES)}
        assert set(sizes) >= expected

    def test_enums_runs_unique_items_at_every_length(self, tmp_path, monkeypatch):
        import fastraml.types.complex_ as complex_module

        lengths: list[int] = []
        original = complex_module.unique_items

        def counting(items):
            lengths.append(len(items))
            return original(items)

        monkeypatch.setattr(complex_module, 'unique_items', counting)
        parse_from_path(corpus.write_enums(tmp_path, family_count=1), ParseOptions(unwrap=True, validate=True))
        assert set(lengths) >= set(corpus.UNIQUE_LENGTHS)

    def test_includes_composes_every_example_and_takes_both_whitespace_paths(self, tmp_path, monkeypatch):
        import fastraml.parser.includes as includes_module

        composed: list[str] = []
        rewritten: list[bool] = []
        original_compose = includes_module._compose_include
        original_tabs = includes_module._json_tabs_as_spaces

        def counting_compose(raml, node, data, target, location):
            composed.append(target.rsplit('/', 1)[-1])
            return original_compose(raml, node, data, target, location)

        def counting_tabs(text):
            result = original_tabs(text)
            rewritten.append(result is not text)
            return result

        monkeypatch.setattr(includes_module, '_compose_include', counting_compose)
        monkeypatch.setattr(includes_module, '_json_tabs_as_spaces', counting_tabs)
        count = corpus._LEADING_TAB_EVERY + 1
        parse_from_path(corpus.write_includes(tmp_path, resource_count=count))
        assert sorted(composed) == sorted(f'e{index}.{kind}' for index in range(count) for kind in ('json', 'yaml'))
        # Every `.json` is checked; a leading tab is rewritten, inner ones are not.
        assert len(rewritten) == count
        assert set(rewritten) == {True, False}

    def test_includes_peeks_at_every_trait_fragment_once(self, tmp_path, monkeypatch):
        import fastraml.parser.includes as includes_module

        headers: list[bool | None] = []
        original = includes_module._has_raml_header

        def counting(raml, target):
            result = original(raml, target)
            headers.append(result)
            return result

        monkeypatch.setattr(includes_module, '_has_raml_header', counting)
        count = 3
        raml = parse_from_path(corpus.write_includes(tmp_path, resource_count=count))
        assert headers == [True] * count
        assert raml.endpoints['/r2'].operations['get'].description.value == 'trait 2'

    def test_include_content_reads_every_resource_and_the_types_as_content(self, tmp_path, monkeypatch):
        import fastraml.parser.includes as includes_module

        inlined: list[str] = []
        original = includes_module.inline_include

        def counting(raml, node, location):
            content, written = original(raml, node, location)
            if written != location:
                inlined.append(written.rsplit('/', 1)[-1])
            return content, written

        for module in ('source_ir', 'fragments'):
            monkeypatch.setattr(f'fastraml.parser.{module}.inline_include', counting)
        monkeypatch.setattr('fastraml.types.shape.inline_include', counting)
        raml = parse_from_path(corpus.write_include_content(tmp_path, resource_count=3))
        assert sorted(inlined) == ['r0.yaml', 'r1.yaml', 'r2.yaml', 'types.yaml']
        # Each trait file is content at a typed position, applied where it is named.
        descriptions = [raml.endpoints[f'/r{index}'].operations['get'].description.value for index in range(3)]
        assert descriptions == [f'trait {index}' for index in range(3)]
        assert sorted(raml.endpoints) == ['/r0', '/r0/{id}', '/r1', '/r1/{id}', '/r2', '/r2/{id}']

    def test_templates_applies_resource_types_and_every_transform_it_names(self, tmp_path, monkeypatch):
        import fastraml.parser.endpoint_build as build_module
        from fastraml.parser import templates

        applied: list[str] = []
        original_apply = build_module.apply_resource_type

        def counting_apply(raml, endpoint, ref, visited):
            applied.append(ref.name)
            return original_apply(raml, endpoint, ref, visited)

        transformed: dict[str, set[str]] = {}
        for action in ('!singularize', '!pluralize', '!uppercamelcase', '!lowerhyphencase'):
            original = templates.TEMPLATE_ACTIONS[action]

            def counting(value, action=action, original=original):
                transformed.setdefault(action, set()).add(value)
                return original(value)

            monkeypatch.setitem(templates.TEMPLATE_ACTIONS, action, counting)
        monkeypatch.setattr(build_module, 'apply_resource_type', counting_apply)
        count = 3
        parse_from_path(corpus.write_templates(tmp_path, resource_count=count), ParseOptions(unwrap=True))
        assert sorted(applied) == sorted(['collection', 'item'] * count)
        # Every resource's own name reaches both dictionary transforms.
        names = {f'w{index}widgets' for index in range(count)}
        assert transformed['!singularize'] >= names
        assert transformed['!pluralize'] >= names
        assert transformed.keys() == {'!singularize', '!pluralize', '!uppercamelcase', '!lowerhyphencase'}

    def test_sequence_merge_unions_a_trait_enum_at_every_size(self, tmp_path, monkeypatch):
        import fastraml.parser.structural_merge as merge_module

        merged: list[tuple[int, int, int]] = []
        original = merge_module._merge_sequences

        def counting(target, source, source_scope, overlay):
            result = original(target, source, source_scope, overlay)
            merged.append((len(target.content), len(source.content), len(result.content)))
            return result

        monkeypatch.setattr(merge_module, '_merge_sequences', counting)
        count = len(corpus.MERGED_ENUM_SIZES)
        parse_from_path(corpus.write_sequence_merge(tmp_path, resource_count=count))
        # The method's values, then the trait's other half.
        assert sorted(merged) == sorted((size, size, size + size // 2) for size in corpus.MERGED_ENUM_SIZES)

    def test_unions_narrows_every_width_nested_and_items(self, tmp_path, monkeypatch):
        import fastraml.types.unwrap as unwrap_module

        widths: list[int] = []
        facets: set[str] = set()
        nested = []
        original = unwrap_module._distribute

        def counting(walk, base, depth):
            shape = base.shape
            if shape is not None and getattr(shape, 'pending_facets', None):
                widths.append(len(shape.any_of or ()))
                facets.update(node.value for node in shape.pending_facets[::2])
                nested.append(depth)
            return original(walk, base, depth)

        monkeypatch.setattr(unwrap_module, '_distribute', counting)
        parse_from_path(corpus.write_unions(tmp_path, family_count=1), ParseOptions(unwrap=True))
        assert set(widths) >= set(corpus.UNION_WIDTHS)
        assert facets >= {'properties', 'items'}
        assert any(depth > 0 for depth in nested), 'a nested union must distribute in turn'

    def test_jsonschema_validates_every_example_through_its_references(self, tmp_path, monkeypatch):
        from fastraml.types.jsonschema_ import JsonShape

        validated: list[object] = []
        original = JsonShape.validate

        def counting(self, value, path):
            validated.append(value)
            return original(self, value, path)

        monkeypatch.setattr(JsonShape, 'validate', counting)
        count = 6
        entry = corpus.write_jsonschema(tmp_path, schema_count=count, shared_count=2)
        parse_from_path(entry, ParseOptions(unwrap=True, validate=True))
        assert len(validated) == count * corpus.EXAMPLES_PER_SCHEMA
        # Each example reaches both `$ref` targets, in another file.
        assert all({'detail', 'more'} <= value.keys() for value in validated)

    def test_schema_export_visits_every_schema(self, tmp_path, monkeypatch):
        import fastraml.views.raml as export_module
        from bench.__main__ import run_one

        visited: list[str] = []
        original = export_module.to_raml

        def counting(shape, **kwargs):
            visited.append(shape.document_uri)
            return original(shape, **kwargs)

        monkeypatch.setattr(export_module, 'to_raml', counting)
        entry = corpus.write_jsonschema(tmp_path, schema_count=6, shared_count=2)
        run_one('schema-export', 'parse', entry, repeat=1)
        assert visited == [], 'parse must not time an export'
        run_one('schema-export', 'unwrap', entry, repeat=1)
        assert len(set(visited)) == 6

    def test_raml_schema_export_visits_every_declared_type(self, tmp_path, monkeypatch):
        import fastraml.views.jsonschema as export_module
        from bench.__main__ import run_one

        visited: list[str] = []
        original = export_module.to_json_schema

        def counting(base, **kwargs):
            visited.append(base.name)
            return original(base, **kwargs)

        monkeypatch.setattr(export_module, 'to_json_schema', counting)
        entry = corpus.write_validate(tmp_path, type_count=6)
        run_one('raml-schema', 'parse', entry, repeat=1)
        assert visited == [], 'parse must not time a schema export'
        run_one('raml-schema', 'unwrap', entry, repeat=1)
        assert visited
        assert set(visited) == {f'V{index}' for index in range(6)}

    def test_facets_walks_every_parent_count(self, tmp_path, monkeypatch):
        import fastraml.types.validate as validate_module

        widths: list[int] = []
        original = validate_module._facet_declarations

        def counting(base, acc):
            widths.append(len(base.inherits))
            return original(base, acc)

        monkeypatch.setattr(validate_module, '_facet_declarations', counting)
        parse_from_path(corpus.write_facets(tmp_path, family_count=1), ParseOptions(unwrap=True, validate=True))
        assert set(widths) >= set(corpus.FACET_PARENTS)

    def test_diamonds_reaches_every_shared_level_by_both_routes(self, tmp_path, monkeypatch):
        """Recursion marking, `check` and the example walk each meet every shared level twice."""
        import fastraml.types.unwrap as unwrap_module
        import fastraml.types.validate as validate_module
        from fastraml.types.base import BaseShape

        calls: dict[str, list[BaseShape]] = {'finish': [], 'check': [], 'commons': []}

        def counting(name, original, position):
            def call(*args):
                calls[name].append(args[position])
                return original(*args)

            return call

        monkeypatch.setattr(unwrap_module, '_finish', counting('finish', unwrap_module._finish, 1))
        monkeypatch.setattr(
            validate_module, '_validate_commons', counting('commons', validate_module._validate_commons, 0)
        )
        monkeypatch.setattr(BaseShape, 'check', counting('check', BaseShape.check, 0))
        raml = parse_from_path(
            corpus.write_diamonds(tmp_path, family_count=1), ParseOptions(unwrap=True, validate=True)
        )
        types = raml.types_in(raml.location)
        for level in range(1, corpus.DIAMOND_DEPTH):
            # Level `level` is shared: both of the previous level's properties
            # lead into its one `properties` container.
            above = types[f'F0D{level - 1}'].shape.properties
            assert above['l'].base.shape.properties is above['r'].base.shape.properties
            for prop in types[f'F0D{level}'].shape.properties.values():
                for name, reached in calls.items():
                    assert sum(base is prop.base for base in reached) >= 2, (name, level)

    def test_inheritance_takes_every_union_path_and_folds_every_declaration_kind(self, tmp_path, monkeypatch):
        import fastraml.types.inherit as inherit_module
        from fastraml.types.complex_ import UnionShape

        paths: list[str] = []
        folded: set[str] = set()

        def counting(name, original):
            def call(*args):
                paths.append(name)
                return original(*args)

            return call

        original_fold = inherit_module._fold

        def fold(parents):
            folded.add(parents[0].name)
            return original_fold(parents)

        monkeypatch.setattr(inherit_module, '_inherit_from_union', counting('from', inherit_module._inherit_from_union))
        monkeypatch.setattr(inherit_module, '_inherit_into_union', counting('into', inherit_module._inherit_into_union))
        # `_narrow` dispatches through the table, not the module attribute.
        monkeypatch.setitem(inherit_module._RULES, UnionShape, counting('both', inherit_module._narrow_union))
        monkeypatch.setattr(inherit_module, '_fold', fold)
        raml = parse_from_path(corpus.write_inheritance(tmp_path, family_count=1), ParseOptions(unwrap=True))
        assert {'from', 'into', 'both'} <= set(paths)
        assert folded >= {'tag', '/^x-/', 'items'}, 'a property, a pattern property and items'
        types = raml.types_in(raml.location)
        for width in corpus.INHERITED_UNION_WIDTHS:
            counts = [len(types[f'F0W{width}{kind}'].shape.any_of) for kind in ('After', 'First', 'Pairs')]
            assert counts == [width, width, 2 * width]


@pytest.mark.parametrize('name', sorted(WRITERS))
@pytest.mark.parametrize('options', CONFIGURATIONS)
def test_corpus_parses_cleanly(tmp_path, name, options):
    entry = WRITERS[name](tmp_path)
    raml = parse_from_path(entry, options)
    assert raml.entry_point is not None
    # Each empty shape container is the shared one (docs/12 § 2): a site that
    # allocates one undoes the saving on exactly the corpora that measure it.
    assert not unshared_empties(raml)


def test_generation_is_deterministic(tmp_path):
    """A baseline describes an input, so the input has to be reproducible."""
    first = corpus.write_large(tmp_path / 'a', type_count=24, library_count=4)
    second = corpus.write_large(tmp_path / 'b', type_count=24, library_count=4)
    written = sorted(path.relative_to(first.parent) for path in first.parent.rglob('*') if path.is_file())
    assert written == sorted(path.relative_to(second.parent) for path in second.parent.rglob('*') if path.is_file())
    for name in written:
        assert (first.parent / name).read_bytes() == (second.parent / name).read_bytes()


def test_large_reaches_common_by_two_spellings(tmp_path):
    """The diamond `bench_large` exists to exercise (docs/12 § 1).

    Every library reaches one `common.raml` through a relative path spelt from
    its own directory. If the compose cache ever stops canonicalising, this
    corpus stops being a linearity check and starts being a quadratic one, so
    the property is pinned here rather than left to the benchmark to notice.
    """
    entry = corpus.write_large(tmp_path, type_count=24, library_count=4)
    raml = parse_from_path(entry)
    common = [uri for uri in raml.fragments if uri.endswith('/common.raml')]
    assert len(common) == 1


def test_general_corpora_exercise_the_deprecated_spellings(tmp_path):
    """The parse-side compatibility recording only runs on `schemas:` and `schema:`.

    The general corpora carry both spellings (docs/18 § 6), so the parse
    workloads measure a document that records them. If a writer ever drops a
    spelling, the recording goes unmeasured and this test says so.
    """
    corpora = (
        ('large', corpus.write_large(tmp_path / 'large', type_count=24, library_count=4)),
        ('endpoints', corpus.write_endpoints(tmp_path / 'endpoints', resource_count=3)),
    )
    for name, entry in corpora:
        raml = parse_from_path(entry)
        spelled = {use.name for uses in raml.syntax_aliases.values() for use in uses}
        assert spelled == {'schemas', 'schema'}, f'{name} corpus records both deprecated spellings'


class TestBaselinesMerge:
    """`baseline --bench small` must not delete the other four benches' rows.

    `write_baseline` is only ever handed what the driver just ran, so replacing
    the file wholesale silently discards every row the current invocation did
    not measure — and the loss is invisible until `compare` reports "new, no
    baseline" for something that had one.
    """

    @staticmethod
    def written(tmp_path, monkeypatch, results):
        from bench import __main__ as driver

        monkeypatch.setattr(driver, 'BASELINE_PATH', tmp_path / 'baselines.json')
        driver.write_baseline(results)
        return json.loads((tmp_path / 'baselines.json').read_text(encoding='utf-8'))

    @staticmethod
    def measurement(bench, config, seconds=1.0):
        from bench.harness import Measurement

        return Measurement(bench=bench, config=config, seconds=seconds, allocated_bytes=1, max_rss_bytes=2)

    def test_a_partial_run_keeps_the_rows_it_did_not_measure(self, tmp_path, monkeypatch):
        first = self.written(tmp_path, monkeypatch, [self.measurement('large', 'parse')])
        assert set(first['measurements']) == {'large/parse'}

        second = self.written(tmp_path, monkeypatch, [self.measurement('small', 'unwrap')])
        assert set(second['measurements']) == {'large/parse', 'small/unwrap'}

    def test_a_rerun_of_the_same_key_replaces_it(self, tmp_path, monkeypatch):
        self.written(tmp_path, monkeypatch, [self.measurement('large', 'parse', seconds=1.0)])
        again = self.written(tmp_path, monkeypatch, [self.measurement('large', 'parse', seconds=2.0)])
        assert again['measurements']['large/parse']['seconds'] == 2.0

    def test_a_fingerprint_change_discards_rather_than_merges(self, tmp_path, monkeypatch, capsys):
        """Rows from another interpreter are not comparable; keeping them would
        let `compare` mix two machines in one report.
        """
        from bench import __main__ as driver

        path = tmp_path / 'baselines.json'
        monkeypatch.setattr(driver, 'BASELINE_PATH', path)
        path.write_text(
            json.dumps({'fingerprint': 'some other machine', 'measurements': {'large/parse': {}}}),
            encoding='utf-8',
        )
        driver.write_baseline([self.measurement('small', 'parse')])
        assert set(json.loads(path.read_text(encoding='utf-8'))['measurements']) == {'small/parse'}
        assert 'discarded' in capsys.readouterr().out
