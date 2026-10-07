"""Custom-facet binding is a resolution fact, independent of optional checking."""

import pytest

from fastraml import ParseOptions, parse_from_string
from fastraml.service import queries
from fastraml.service.workspace import Snapshot
from tests.unit.test_service_queries import _where

DOCUMENT = """#%RAML 1.0
title: Binding
types:
  Base:
    type: string
    facets:
      payload:
        type: object
        description: Metadata for a consumer.
        properties:
          label:
            type: string
            description: A reader-facing label.
  Mid: Base
  Child:
    type: Mid
    payload: {label: Text}
"""


@pytest.mark.parametrize('unwrap', [False, True])
@pytest.mark.parametrize('validate', [False, True])
def test_bindings_and_definition_survive_all_parser_option_combinations(tmp_path, unwrap, validate):
    raml = parse_from_string(
        DOCUMENT,
        file_name='api.raml',
        base_dir=tmp_path,
        options=ParseOptions(unwrap=unwrap, validate=validate, retain_text=True),
    )
    uri = raml.location
    snapshot = Snapshot(uri, raml, None, frozenset(raml.source_texts))
    result = queries.hover(snapshot, uri, *_where(DOCUMENT, 'payload: {'))
    assert result is not None
    assert 'Metadata for a consumer.' in result[0]
    sites = queries.definition(snapshot, uri, *_where(DOCUMENT, 'payload: {'))
    assert [(site.uri, site.span.line) for site in sites] == [(uri, _where(DOCUMENT, 'payload:\n')[0])]
    nested = queries.hover(snapshot, uri, *_where(DOCUMENT, 'label: Text'))
    assert nested is not None
    assert 'A reader-facing label.' in nested[0]
    assert len(raml.custom_facet_refs) == 1


def test_validation_does_not_mutate_the_binding_index(tmp_path):
    from fastraml.types.validate import validate_shapes

    raml = parse_from_string(DOCUMENT, file_name='api.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True))
    before = {value: tuple(declarations) for value, declarations in raml.custom_facet_refs.items()}
    validate_shapes(raml)
    assert {value: tuple(declarations) for value, declarations in raml.custom_facet_refs.items()} == before


def test_resolution_does_not_run_custom_facet_value_checks(tmp_path):
    document = DOCUMENT.replace('payload: {label: Text}', 'payload: {label: 42}\n    unknown: true')
    raml = parse_from_string(
        document, file_name='api.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True, validate=False)
    )
    assert len(raml.custom_facet_refs) == 1
    (value,) = raml.custom_facet_refs
    assert value.raw == {'label': 42}


def test_included_value_definition_stays_in_the_including_file(tmp_path):
    from tests.unit.conftest import write_files

    write_files(tmp_path, {'value.yaml': 'label: Included\n'})
    document = DOCUMENT.replace('payload: {label: Text}', 'payload: !include value.yaml')
    raml = parse_from_string(
        document, file_name='api.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True, retain_text=True)
    )
    uri = raml.location
    snapshot = Snapshot(uri, raml, None, frozenset(raml.source_texts))
    sites = queries.definition(snapshot, uri, *_where(document, 'payload: !include'))
    assert [(site.uri, site.span.line) for site in sites] == [(uri, _where(document, 'payload:\n')[0])]
