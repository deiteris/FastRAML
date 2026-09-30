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
    'schema-export': lambda root: corpus.write_jsonschema(root, schema_count=6, shared_count=2),
    'raml-schema': lambda root: corpus.write_validate(root, type_count=6),
    'enums': lambda root: corpus.write_enums(root, family_count=1),
    'unions': lambda root: corpus.write_unions(root, family_count=1),
    'facets': lambda root: corpus.write_facets(root, family_count=1),
    'inheritance': lambda root: corpus.write_inheritance(root, family_count=1),
    'includes': lambda root: corpus.write_includes(root, resource_count=corpus._LEADING_TAB_EVERY + 1),
    'include-content': lambda root: corpus.write_include_content(root, resource_count=3),
    'inline-json': lambda root: corpus.write_inline_json(root, type_count=3),
    'template-scopes': lambda root: corpus.write_template_scopes(root, resource_count=3),
    'reference-namespaces': lambda root: corpus.write_reference_namespaces(root, resource_count=3),
}


class TestFeatureCorporaReachTheirCode:
    """A feature corpus that stops reaching its code measures nothing, silently.

    The general corpora never called the enum subset check or `uniqueItems`,
    so every delta reported for a change there was noise on unchanged code
    (docs/12 § 4). Each feature corpus pins, by counting calls, that it runs the
    code it was written for, at every size it was written to cover.
    """

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

        def counting_compose(raml, node, data, target):
            composed.append(target.rsplit('/', 1)[-1])
            return original_compose(raml, node, data, target)

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

        original_fold = inherit_module.fold

        def fold(parents):
            folded.add(parents[0].name)
            return original_fold(parents)

        monkeypatch.setattr(inherit_module, '_inherit_from_union', counting('from', inherit_module._inherit_from_union))
        monkeypatch.setattr(inherit_module, '_inherit_into_union', counting('into', inherit_module._inherit_into_union))
        # `_narrow` dispatches through the table, not the module attribute.
        monkeypatch.setitem(inherit_module._RULES, UnionShape, counting('both', inherit_module._narrow_union))
        monkeypatch.setattr(inherit_module, 'fold', fold)
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
