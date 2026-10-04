"""External JSON Schema types — docs/10-validation.md § 7.

Three things are being pinned here, in the order docs/10 § 7 states them:
compilation and the shared registry, the restrictions on a JSON-schema-typed
declaration, and the projection to a RAML shape.

The two security-relevant rules have a test each, because both are silent when
broken: a relative `$ref` must resolve against the RAML file that held the
schema, and every `$ref` must go through `ResourceLoader` rather than the
network.
"""

from __future__ import annotations

import json
from fractions import Fraction
from itertools import permutations
from typing import ClassVar

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path
from fastraml.types.complex_ import RecursiveShape
from fastraml.types.jsonschema_ import projected
from fastraml.views.graph import build_graph
from fastraml.views.walk import DEFAULT_BASE
from tests.unit.conftest import CountingLoader

API = '#%RAML 1.0\ntitle: T\n'

PERSON = json.dumps(
    {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object',
        'required': ['name'],
        'properties': {'name': {'type': 'string'}, 'age': {'type': 'integer'}},
    }
)


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def parse(workspace, files: dict[str, str], **options):
    """Parse `api.raml` out of `files`; return the error, or `None`."""
    root = workspace(files)
    try:
        workspace.parse(root / 'api.raml', ParseOptions(validate=True, unwrap=True, **options))
    except RamlError as err:
        return err
    return None


def parsed(workspace, files: dict[str, str], **options):
    root = workspace(files)
    return workspace.parse(root / 'api.raml', ParseOptions(validate=True, unwrap=True, **options))


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


def indent(text: str, width: int = 4) -> str:
    pad = ' ' * width
    return ''.join(pad + line + '\n' for line in text.splitlines())


def declaration(name: str, schema: str, *facets: str) -> str:
    """`name:` declared by `schema`, with sibling facets under it.

    The block-scalar shorthand (`Person: |`) cannot carry siblings — anything
    indented under it is more schema — so the long form is what a test with an
    `example:` needs.
    """
    return f'  {name}:\n    type: |\n' + indent(schema, 6) + ''.join('    ' + line + '\n' for line in facets)


class TestCompilation:
    """Section 6.1. A schema is compiled where it is declared, not at first use."""

    def test_a_well_formed_schema_compiles(self, workspace):
        raml = parsed(workspace, {'api.raml': API + 'types:\n  Person: |\n' + indent(PERSON)})
        shape = raml.types_in(raml.location)['Person']
        assert shape.type == 'json'
        assert shape.shape.validator is not None

    def test_malformed_json_is_a_parse_error(self, workspace):
        error = parse(workspace, {'api.raml': API + 'types:\n  Person: |\n    {"type": "object"\n'})
        assert error is not None
        assert 'invalid JSON in schema' in messages(error)

    def test_a_schema_that_is_not_a_schema_is_rejected(self, workspace):
        # Caught by the meta-schema, not by the JSON decoder: this parses.
        error = parse(workspace, {'api.raml': API + 'types:\n  Person: |\n    {"type": "nonesuch"}\n'})
        assert error is not None
        assert 'invalid JSON schema' in messages(error)

    def test_the_declaration_is_rejected_without_validate(self, workspace):
        # Compilation happens at construction, so a broken schema is a syntax
        # error in the document rather than something only `validate=True` sees.
        root = workspace({'api.raml': API + 'types:\n  Person: |\n    {"type": "object"\n'})
        with pytest.raises(RamlError):
            workspace.parse(root / 'api.raml')

    def test_an_external_json_file_compiles_through_the_same_path(self, workspace):
        raml = parsed(workspace, {'api.raml': API + 'types:\n  Person: !include person.json\n', 'person.json': PERSON})
        shape = raml.types_in(raml.location)['Person']
        assert shape.type == 'json'

    def test_an_external_json_file_may_begin_with_whitespace(self, workspace):
        # JSON allows it; read as a type expression, `    {` was a syntax error.
        raml = parsed(
            workspace, {'api.raml': API + 'types:\n  Person: !include person.json\n', 'person.json': '\n    ' + PERSON}
        )
        assert raml.types_in(raml.location)['Person'].type == 'json'

    def test_a_draft_is_taken_from_schema_and_defaults_to_7(self, workspace):
        # `exclusiveMinimum: true` is draft-04's spelling and draft-07 forbids
        # it, so the declared draft is what decides whether this compiles.
        four = '{"$schema": "http://json-schema.org/draft-04/schema#", "minimum": 1, "exclusiveMinimum": true}'
        assert parse(workspace, {'api.raml': API + 'types:\n  N: |\n' + indent(four)}) is None
        seven = '{"minimum": 1, "exclusiveMinimum": true}'
        error = parse(workspace, {'api.raml': API + 'types:\n  N: |\n' + indent(seven)})
        assert error is not None
        assert 'invalid JSON schema' in messages(error)


class TestReferences:
    """A `$ref` is resolved when the schema is compiled, not when it is used."""

    def test_a_relative_ref_resolves_against_the_raml_file(self, workspace):
        # The whole point of registering under the RAML file's URI: `sub/`
        # holds neither the RAML file nor the reference's target.
        schema = json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'person.json'}}})
        assert (
            parse(
                workspace,
                {'api.raml': API + 'types:\n  Holder: |\n' + indent(schema), 'person.json': PERSON},
            )
            is None
        )

    def test_a_relative_ref_in_an_included_schema_resolves_against_that_file(self, workspace):
        holder = json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'person.json'}}})
        assert (
            parse(
                workspace,
                {
                    'api.raml': API + 'types:\n  Holder: !include sub/holder.json\n',
                    'sub/holder.json': holder,
                    'sub/person.json': PERSON,
                },
            )
            is None
        )

    @pytest.mark.parametrize(
        ('target', 'invalid'),
        [
            # Divided by at the first example: `ZeroDivisionError`.
            ({'type': 'number', 'multipleOf': 0}, {'keyword': 'exclusiveMinimum', 'path': 'multipleOf'}),
            # Crawled by `referencing`: `TypeError`.
            ({'properties': {'currency': 7}}, {'keyword': 'type', 'path': 'properties/currency'}),
            # Read as a bound by the `allOf` projection.
            ({'type': 'number', 'minimum': 'oops'}, {'keyword': 'type', 'path': 'minimum'}),
        ],
        ids=['multipleOf-zero', 'non-schema-property', 'non-numeric-bound'],
    )
    def test_a_referenced_document_is_checked_against_its_meta_schema(self, workspace, target, invalid):
        # docs/10 § 7: a `$ref` target is held to its draft as the entry is,
        # before anything crawls, projects or validates with it. Lenient, which
        # each of these escaped as a non-`RamlError`.
        root = workspace(
            {
                'api.raml': API + 'types:\n  T:\n    type: !include s.json\n    example: 1\n',
                's.json': json.dumps({'allOf': [{'$ref': 'z.json'}, {}]}),
                'z.json': json.dumps(target),
            }
        )
        _, error = workspace.lenient(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        assert error is not None
        assert [[(trace.message, trace.info) for trace in chain][-2:] for chain in error.chains()] == [
            [('unresolvable JSON schema reference', {'ref': 'z.json'}), ('invalid JSON schema', invalid)]
        ]

    @pytest.mark.parametrize(
        ('draft', 'root', 'target', 'good', 'bad'),
        [
            (
                'http://json-schema.org/draft-04/schema#',
                {'allOf': [{'$ref': 'z.json'}]},
                {'type': 'number', 'minimum': 0, 'exclusiveMinimum': True},
                1,
                0,
            ),
        ],
        ids=['draft4-boolean-exclusive-minimum'],
    )
    def test_a_referenced_document_naming_no_draft_is_read_in_the_referrers(  # noqa: PLR0913, PLR0917 - one case
        self, workspace, draft, root, target, good, bad
    ):
        # docs/10 § 7: valid only in the referrer's draft, not in draft 7.
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T:\n    type: !include s.json\n',
                's.json': json.dumps({'$schema': draft, **root}),
                'z.json': json.dumps(target),
            },
        )
        shape = raml.types_in(raml.location)['T'].shape.base
        assert shape.validate(good) is None
        assert shape.validate(bad) is not None

    def test_a_ref_to_a_missing_file_is_reported_at_compile_time(self, workspace):
        # Nothing validates against this type, so a library that resolved lazily
        # would never report it. go-raml compiles eagerly
        # and the TCK expects that.
        schema = json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'nowhere.json'}}})
        error = parse(workspace, {'api.raml': API + 'types:\n  Holder: |\n' + indent(schema)})
        assert error is not None
        assert 'unresolvable JSON schema reference' in messages(error)

    def test_a_ref_to_a_missing_pointer_is_reported(self, workspace):
        schema = json.dumps({'type': 'object', 'properties': {'p': {'$ref': '#/definitions/Nope'}}})
        error = parse(workspace, {'api.raml': API + 'types:\n  Holder: |\n' + indent(schema)})
        assert error is not None
        assert 'unresolvable JSON schema reference' in messages(error)

    def test_an_offline_parse_refuses_a_remote_ref(self, workspace):
        """`http(s)` includes are opt-in, and a `$ref` is an include.

        `referencing` will happily fetch through a default retriever, which
        would make an offline parse reach the network — silently, and only for
        documents that name a remote schema.
        """
        schema = json.dumps({'$ref': 'http://json-schema.org/draft-07/schema#'})
        error = parse(workspace, {'api.raml': API + 'types:\n  Holder: |\n' + indent(schema)})
        assert error is not None
        assert 'unresolvable JSON schema reference' in messages(error)
        # The loader's own diagnostic survives, rather than being flattened into
        # "could not resolve": which scheme was refused is the useful half.
        assert any('no loader for URI scheme' in trace.message for chain in error.chains() for trace in chain)

    def test_a_ref_shared_by_many_schemas_is_read_once(self, workspace):
        """Section 6.1: one registry per parse, not one per shape."""
        holders = ''.join(
            f'  H{index}: |\n' + indent(json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'person.json'}}}))
            for index in range(8)
        )
        root = workspace({'api.raml': API + 'types:\n' + holders, 'person.json': PERSON})
        loader = CountingLoader(root, workspace)
        parse_from_path(
            root / 'api.raml', ParseOptions(validate=True, unwrap=True, file_loader=loader, workspace_root=root)
        )
        target = next(uri for uri in loader.counts if uri.endswith('person.json'))
        assert loader.counts[target] == 1

    def test_a_recursive_ref_terminates(self, workspace):
        schema = json.dumps({'type': 'object', 'properties': {'next': {'$ref': '#'}}})
        assert parse(workspace, {'api.raml': API + 'types:\n  Node: |\n' + indent(schema)}) is None

    def test_a_ref_inside_a_default_is_data_and_not_resolved(self, workspace):
        # `$ref` is only a reference in schema position. Resolving one written
        # inside a `default` would reject a document the spec allows.
        schema = json.dumps({'type': 'object', 'default': {'$ref': 'nowhere.json'}})
        assert parse(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(schema)}) is None


class TestInstanceValidation:
    """Section 5's `json` row: the compiled validator decides."""

    @pytest.mark.parametrize(
        ('value', 'valid'),
        [('00000000-0000-0000-0000-000000000000', True), ('65', True), ('abc', False)],
        ids=['uuid', 'numeric', 'neither'],
    )
    def test_one_of_uses_formats_in_referenced_schemas(self, workspace, value, valid):
        # docs/10 § 7: ignoring uuid format makes every string match that
        # branch, rejecting numeric strings as ambiguous and accepting junk.
        error = parse(
            workspace,
            {
                'api.raml': API + 'types:\n  Id:\n    type: !include id.json\n    example: !include example.json\n',
                'id.json': json.dumps({'oneOf': [{'$ref': 'uuid.json'}, {'$ref': 'numeric.json'}]}),
                'uuid.json': json.dumps({'type': 'string', 'format': 'uuid'}),
                'numeric.json': json.dumps({'type': 'string', 'pattern': '^[0-9]+$'}),
                'example.json': json.dumps(value),
            },
        )
        if valid:
            assert error is None
        else:
            assert error is not None
            assert any(
                trace.message == 'value does not match the JSON schema'
                and trace.info == {'path': '$', 'schema_path': 'oneOf'}
                for chain in error.chains()
                for trace in chain
            )

    @pytest.mark.parametrize(
        ('draft', 'format_name', 'valid', 'invalid'),
        [
            ('http://json-schema.org/draft-04/schema#', 'date-time', '2026-09-30T12:00:00Z', 'not-a-date'),
            ('http://json-schema.org/draft-07/schema#', 'uuid', '00000000-0000-0000-0000-000000000000', 'abc'),
            ('http://json-schema.org/draft-07/schema#', 'uri', 'https://example.com/path', 'not a uri'),
            ('https://json-schema.org/draft/2020-12/schema', 'duration', 'P1D', 'not-a-duration'),
        ],
        ids=['draft4-date-time', 'draft7-uuid', 'draft7-uri', 'draft2020-duration'],
    )
    def test_known_formats_validate_across_drafts(self, workspace, draft, format_name, valid, invalid):
        # docs/10 § 7: URI/date-time/duration exercise format-nongpl's
        # optional libraries as well as enabling the checker itself.
        schema = json.dumps({'$schema': draft, 'type': 'string', 'format': format_name})
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', schema)})
        shape = raml.types_in(raml.location)['T'].shape.base
        assert shape.validate(valid) is None
        error = shape.validate(invalid)
        assert error is not None
        assert any(
            trace.message == 'value does not match the JSON schema'
            and trace.info == {'path': '$', 'schema_path': 'format'}
            for chain in error.chains()
            for trace in chain
        )

    def test_unknown_formats_remain_annotations(self, workspace):
        # docs/10 § 7: enabling known formats does not invent constraints
        # for application-defined format names.
        schema = json.dumps({'type': 'string', 'format': 'application-specific-format'})
        body = 'types:\n' + declaration('T', schema, 'example: anything')
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_validating_through_a_ref_to_another_file_neither_crawls_nor_retrieves(self, workspace, monkeypatch):
        # docs/10 § 7: the validator's registry already holds every document
        # the schema reaches, so each validation does not rebuild one.
        from referencing import Registry

        from fastraml.types.jsonschema_ import SchemaRegistry

        holder = json.dumps({'type': 'object', 'properties': {'p': {'$ref': 'person.json'}}})
        files = {'api.raml': API + 'types:\n  Holder: !include holder.json\n', 'holder.json': holder}
        raml = parsed(workspace, {**files, 'person.json': PERSON})
        shape = raml.types_in(raml.location)['Holder'].shape
        calls: list[str] = []
        crawl, retrieve = Registry.crawl, SchemaRegistry._retrieve
        monkeypatch.setattr(Registry, 'crawl', lambda self: calls.append('crawl') or crawl(self))
        monkeypatch.setattr(SchemaRegistry, '_retrieve', lambda self, uri: calls.append(uri) or retrieve(self, uri))
        shape.validate({'p': {'name': 'n'}}, '')
        with pytest.raises(RamlError):
            shape.validate({'p': {'age': 1}}, '')
        assert calls == []

    def test_a_conforming_example_passes(self, workspace):
        body = 'types:\n' + declaration('Person', PERSON, 'example:', '  name: Ada', '  age: 36')
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_a_non_conforming_example_fails(self, workspace):
        body = 'types:\n' + declaration('Person', PERSON, 'example:', '  name: 12')
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'value does not match the JSON schema' in messages(error)

    def test_a_missing_required_property_fails(self, workspace):
        body = 'types:\n' + declaration('Person', PERSON, 'example:', '  age: 36')
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'value does not match the JSON schema' in messages(error)

    def test_an_example_written_as_a_json_string_is_decoded_first(self, workspace):
        # `example: |` holding a JSON document is the form the TCK uses. It
        # arrives as a scalar and is decoded by `make_data_node`, so the
        # validator sees a mapping rather than a string.
        body = 'types:\n' + declaration('Person', PERSON, 'example: |', '  {"name": "Ada"}')
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_a_pointer_include_validates_against_the_pointed_subschema(self, workspace):
        document = json.dumps({'definitions': {'Person': json.loads(PERSON)}})
        files = {
            'api.raml': API + 'types:\n  Person: !include schema.json#/definitions/Person\n',
            'schema.json': document,
        }
        assert parse(workspace, files) is None

    def test_a_pointer_included_subschema_bundles_the_pointers_it_uses(self, workspace):
        # docs/10 § 7: its `#/definitions/...` point into the file, which the
        # bundle of the subschema is not, so what they name is pulled in, and
        # a reference back to the subschema itself is the bundle's root.
        from jsonschema import Draft7Validator

        user = {
            'type': 'object',
            'required': ['home'],
            'properties': {'home': {'$ref': '#/definitions/Address'}, 'friend': {'$ref': '#/definitions/User'}},
        }
        document = json.dumps({'definitions': {'User': user, 'Address': {'type': 'string'}}})
        files = {'api.raml': API + 'types:\n  User: !include schema.json#/definitions/User\n', 'schema.json': document}
        raml = parsed(workspace, files)
        bundle = raml.types_in(raml.location)['User'].shape.as_schema()
        assert bundle['definitions'] == {'Address': {'type': 'string'}}
        assert bundle['properties']['friend'] == {'$ref': '#'}
        validator = Draft7Validator(bundle)
        assert validator.is_valid({'home': 'x', 'friend': {'home': 'y'}})
        assert not validator.is_valid({'home': 1})

    def test_a_referenced_document_claims_its_own_definition_aliases(self, workspace):
        from jsonschema import Draft7Validator

        files = {
            'api.raml': API + 'types:\n  Batch: !include batch.json\n',
            'batch.json': json.dumps(
                {
                    'definitions': {'idp': {'$ref': 'idp.json'}},
                    'type': 'array',
                    'items': {'$ref': '#/definitions/idp'},
                }
            ),
            # Put properties before definitions: aliases must be claimed before
            # the first reference to them, regardless of object member order.
            'idp.json': json.dumps(
                {
                    'type': 'object',
                    'properties': {'id': {'$ref': '#/definitions/uuid'}},
                    'definitions': {'uuid': {'$ref': 'uuid.json'}},
                }
            ),
            'uuid.json': json.dumps({'type': 'string', 'pattern': '^u$'}),
        }
        raml = parsed(workspace, files)
        bundle = raml.types_in(raml.location)['Batch'].shape.as_schema()
        assert list(bundle['definitions']) == ['idp']
        idp = bundle['definitions']['idp']
        assert idp['definitions'] == {'uuid': {'type': 'string', 'pattern': '^u$'}}
        assert idp['properties']['id'] == {'$ref': '#/definitions/idp/definitions/uuid'}
        validator = Draft7Validator(bundle)
        assert validator.is_valid([{'id': 'u'}])
        assert not validator.is_valid([{'id': 'wrong'}])

    def test_two_referenced_documents_can_alias_the_same_target(self, workspace):
        from jsonschema import Draft7Validator

        files = {
            'api.raml': API + 'types:\n  Batch: !include batch.json\n',
            'batch.json': json.dumps(
                {
                    'type': 'object',
                    'properties': {'first': {'$ref': 'first.json'}, 'second': {'$ref': 'second.json'}},
                }
            ),
            'first.json': json.dumps(
                {
                    'definitions': {'uuid': {'$ref': 'uuid.json'}},
                    'type': 'object',
                    'properties': {'id': {'$ref': '#/definitions/uuid'}},
                }
            ),
            'second.json': json.dumps(
                {
                    'definitions': {'uuid': {'$ref': 'uuid.json'}},
                    'type': 'object',
                    'properties': {'id': {'$ref': '#/definitions/uuid'}},
                }
            ),
            'uuid.json': json.dumps({'type': 'string', 'pattern': '^u$'}),
        }
        raml = parsed(workspace, files)
        bundle = raml.types_in(raml.location)['Batch'].shape.as_schema()
        assert list(bundle['definitions']) == ['first', 'second']
        assert bundle['definitions']['second']['definitions']['uuid'] == {
            '$ref': '#/definitions/first/definitions/uuid'
        }
        assert bundle['definitions']['second']['properties']['id'] == {'$ref': '#/definitions/second/definitions/uuid'}
        validator = Draft7Validator(bundle)
        assert validator.is_valid({'first': {'id': 'u'}, 'second': {'id': 'u'}})
        assert not validator.is_valid({'second': {'id': 'wrong'}})

    def test_a_pointer_that_names_nothing_is_an_error(self, workspace):
        document = json.dumps({'definitions': {'Person': json.loads(PERSON)}})
        files = {
            'api.raml': API + 'types:\n  Person: !include schema.json#/definitions/Missing\n',
            'schema.json': document,
        }
        error = parse(workspace, files)
        assert error is not None
        assert 'unresolvable JSON schema reference' in messages(error)


class TestMultipleOfIsExact:
    """docs/10 § 7: `multipleOf` never divides floats."""

    DRAFT7 = 'http://json-schema.org/draft-07/schema#'
    DRAFT2020 = 'https://json-schema.org/draft/2020-12/schema'

    @staticmethod
    def schema_paths(error) -> list[object]:
        return [
            trace.info
            for chain in error.chains()
            for trace in chain
            if trace.message == 'value does not match the JSON schema'
        ]

    def declared(self, workspace, divisor: str):
        schema = f'{{"$schema": "{self.DRAFT7}", "type": "number", "multipleOf": {divisor}}}'
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', schema)})
        return raml.types_in(raml.location)['T'].shape.base

    @pytest.mark.parametrize(
        ('divisor', 'good', 'bad'),
        [('0.1', 0.7, 0.75), ('1.1', 2.2, 2.3)],
        ids=['multipleOf-0.1', 'multipleOf-1.1'],
    )
    def test_a_decimal_multiple_is_accepted_and_a_non_multiple_rejected(self, workspace, divisor, good, bad):
        shape = self.declared(workspace, divisor)
        assert shape.validate(good) is None
        assert self.schema_paths(shape.validate(bad)) == [{'path': '$', 'schema_path': 'multipleOf'}]

    def test_a_non_number_is_left_to_type(self, workspace):
        shape = self.declared(workspace, '0.1')
        assert self.schema_paths(shape.validate(True)) == [{'path': '$', 'schema_path': 'type'}]

    @pytest.mark.parametrize('root_draft', [None, DRAFT7], ids=['default-to-2020', 'draft7-to-2020'])
    def test_a_reference_into_another_draft_stays_exact(self, workspace, root_draft):
        # A `$ref` into a document declaring another draft is validated by
        # that draft's class, which must be the exact one too.
        root = {'$ref': 'z.json'} if root_draft is None else {'$schema': root_draft, '$ref': 'z.json'}
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T:\n    type: !include s.json\n',
                's.json': json.dumps(root),
                'z.json': json.dumps({'$schema': self.DRAFT2020, 'type': 'number', 'multipleOf': 0.1}),
            },
        )
        shape = raml.types_in(raml.location)['T'].shape.base
        assert shape.validate(0.7) is None
        # `jsonschema` leaves `$ref` out of the schema path.
        assert self.schema_paths(shape.validate(0.75)) == [{'path': '$', 'schema_path': 'multipleOf'}]


class TestRestrictions:
    """Section 6.2: a JSON-schema type does not participate in the type system."""

    def test_a_sibling_facet_is_rejected(self, workspace):
        body = 'types:\n' + declaration('Person', PERSON, 'minLength: 3')
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'cannot define facets on a JSON schema type' in messages(error)

    def test_the_wrapper_facets_the_spec_allows_are_accepted(self, workspace):
        body = 'types:\n' + declaration('Person', PERSON, 'displayName: A person', 'description: has a name')
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_inheriting_from_a_different_schema_is_rejected(self, workspace):
        other = json.dumps({'type': 'object', 'properties': {'x': {'type': 'string'}}})
        body = 'types:\n  A: |\n' + indent(PERSON) + '  B: |\n' + indent(other) + '  C:\n    type: [A, B]\n'
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'cannot inherit from a different JSON schema' in messages(error)

    @pytest.mark.parametrize(
        'expression', ['Person[]', 'Person?', 'Person | string'], ids=['array', 'optional', 'union']
    )
    def test_a_schema_type_may_appear_in_a_type_expression(self, workspace, expression):
        """docs/01 § 4.5. The spec refuses all three; fastRAML builds them.

        Nothing here asks the schema for more than `validate(value)`, which it
        answers. A union is a list of types to validate against, and the union
        itself is only an entry point.
        """
        body = 'types:\n  Person: |\n' + indent(PERSON) + f'  Board:\n    properties:\n      members: {expression}\n'
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_the_expression_still_validates_through_the_schema(self, workspace):
        """Permitting it is only right if it works. `Person` requires `name`."""
        body = 'types:\n  Person: |\n' + indent(PERSON) + '  Board:\n    properties:\n      members: Person[]\n'
        raml = parsed(workspace, {'api.raml': API + body})
        board = raml.types_in(raml.location)['Board'].shape.base
        assert board.validate({'members': [{'name': 'Bob'}]}) is None
        assert board.validate({'members': [{'nope': 1}]}) is not None

    def test_a_bare_reference_to_a_schema_type_is_allowed(self, workspace):
        """The spec's own examples use one: a name is not a type expression."""
        body = 'types:\n  Person: |\n' + indent(PERSON) + '  Board:\n    properties:\n      chair: Person\n'
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_inheriting_from_a_schema_type_is_still_refused(self, workspace):
        """The boundary of docs/01 § 4.5. Inheritance asks for a RAML facet to be merged
        into a compiled schema, and there is no such operation.
        """
        body = 'types:\n  Person: |\n' + indent(PERSON) + '  Boss:\n    type: Person\n    minLength: 3\n'
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None


SCALAR_SCHEMA = json.dumps({'type': 'string', 'minLength': 4})


class TestParameterDeclarations:
    """A schema type *is* a type, including in a parameter.

    The spec forbids one outright in a query parameter, query string, URI
    parameter or header. fastRAML permits it (`docs/01` § 4.5) because a
    `JsonShape` is asked for nothing here but `validate(value)`, which it does.
    """

    RESOURCE: ClassVar[dict[str, str]] = {
        'headers': '/r:\n  get:\n    headers:\n      H: {ref}\n',
        'queryParameters': '/r:\n  get:\n    queryParameters:\n      q: {ref}\n',
        'queryString': '/r:\n  get:\n    queryString: {ref}\n',
        'uriParameters': '/r/{{id}}:\n  uriParameters:\n    id: {ref}\n  get:\n',
        'responseHeaders': '/r:\n  get:\n    responses:\n      200:\n        headers:\n          H: {ref}\n',
        'baseUriParameters': 'baseUriParameters:\n  h: {ref}\n',
    }

    @pytest.mark.parametrize('facet', sorted(RESOURCE))
    def test_a_named_schema_type_is_accepted(self, workspace, facet):
        body = 'types:\n  Code: |\n' + indent(SCALAR_SCHEMA) + self.RESOURCE[facet].format(ref='Code')
        assert parse(workspace, {'api.raml': API + 'baseUri: http://x/{h}\n' + body}) is None

    def test_an_inline_schema_is_accepted_too(self, workspace):
        body = '/r:\n  get:\n    headers:\n      H:\n        type: |\n' + indent(SCALAR_SCHEMA, 10)
        assert parse(workspace, {'api.raml': API + body}) is None

    def test_the_schema_still_validates_the_value(self, workspace):
        """The point of permitting it. A parameter that parses but never
        validates would be worse than refusing it.
        """
        body = 'types:\n  Code: |\n' + indent(SCALAR_SCHEMA) + self.RESOURCE['queryParameters'].format(ref='Code')
        raml = parsed(workspace, {'api.raml': API + body})
        operation = next(iter(next(iter(raml.endpoints.values())).operations.values()))
        code = operation.request.query_parameters['q'].base
        assert code.validate('long enough') is None
        assert code.validate('abc') is not None

    def test_an_ordinary_parameter_is_untouched(self, workspace):
        body = '/r:\n  get:\n    headers:\n      H: string\n'
        assert parse(workspace, {'api.raml': API + body}) is None


def projection_of(shape):
    """`shape.as_shape()`, raising its `projection_error()` where it has none.

    The projection's policy is which schemas fail and why; `as_shape` keeps the
    failure rather than raising it (docs/10 § 7), and these tests read it here.
    """
    view = shape.as_shape()
    if view is None:
        raise shape.projection_error()
    return view


def project(workspace, schema: dict, **files: str):
    """The projection of a type declared by `schema`, or its failure raised."""
    raml = parsed(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(json.dumps(schema)), **files})
    return projection_of(raml.types_in(raml.location)['T'].shape)


class TestProjection:
    """Section 6.3, one test per row of its table."""

    def test_an_object_carries_its_properties_and_bounds(self, workspace):
        shape = project(
            workspace,
            {
                'type': 'object',
                'required': ['a'],
                'properties': {'a': {'type': 'string'}, 'b': {'type': 'integer'}},
                'minProperties': 1,
                'maxProperties': 4,
                'additionalProperties': False,
            },
        )
        assert shape.type == 'object'
        assert list(shape.shape.properties) == ['a', 'b']
        assert shape.shape.properties['a'].required is True
        assert shape.shape.properties['b'].required is False
        assert shape.shape.min_properties.value == 1
        assert shape.shape.max_properties.value == 4
        assert shape.shape.additional_properties.value is False

    def test_pattern_properties_become_slash_delimited_keys(self, workspace):
        shape = project(workspace, {'type': 'object', 'patternProperties': {'^x': {'type': 'number'}}})
        assert list(shape.shape.pattern_properties) == ['/^x/']

    def test_an_array_carries_items_and_bounds(self, workspace):
        shape = project(
            workspace, {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 3, 'uniqueItems': True}
        )
        assert shape.type == 'array'
        assert shape.shape.items.type == 'string'
        assert (shape.shape.min_items.value, shape.shape.max_items.value) == (1, 3)
        assert shape.shape.unique_items.value is True

    @pytest.mark.parametrize(
        ('declared', 'expected'),
        [('string', 'string'), ('integer', 'integer'), ('number', 'number'), ('boolean', 'boolean'), ('null', 'nil')],
    )
    def test_each_scalar_maps_to_its_kind(self, workspace, declared, expected):
        assert project(workspace, {'type': declared}).type == expected

    def test_scalar_constraints_carry_over(self, workspace):
        text = project(workspace, {'type': 'string', 'minLength': 2, 'maxLength': 8, 'pattern': '^a'})
        assert (text.shape.min_length.value, text.shape.max_length.value) == (2, 8)
        assert text.shape.pattern.value.pattern == '^a'

    def test_a_numeric_bound_never_passes_through_float(self, workspace):
        # `1.1` decoded by `json` is a binary approximation. The bound is built
        # from its decimal text, so it is exactly 11/10 (docs/10 § 5).
        number = project(workspace, {'type': 'number', 'multipleOf': 1.1})
        assert number.shape.multiple_of.value == Fraction(11, 10)

    def test_a_type_list_becomes_a_union_of_bare_members(self, workspace):
        shape = project(workspace, {'type': ['string', 'null']})
        assert shape.type == 'union'
        assert [member.type for member in shape.shape.any_of] == ['string', 'nil']

    @pytest.mark.parametrize('keyword', ['anyOf', 'oneOf'])
    def test_any_of_and_one_of_both_become_a_union(self, workspace, keyword):
        # `oneOf` is "exactly one" and RAML's union is "at least one"; the
        # difference is a documented loss, not an oversight.
        shape = project(workspace, {keyword: [{'type': 'string'}, {'type': 'integer'}]})
        assert shape.type == 'union'
        assert [member.type for member in shape.shape.any_of] == ['string', 'integer']

    def test_all_of_intersects_object_properties(self, workspace):
        shape = project(
            workspace,
            {
                'allOf': [
                    {'type': 'object', 'properties': {'a': {'type': 'string'}}},
                    {'type': 'object', 'properties': {'b': {'type': 'integer'}}},
                ]
            },
        )
        assert shape.type == 'object'
        assert sorted(shape.shape.properties) == ['a', 'b']

    def test_a_named_ref_target_is_registered_in_the_defs(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n  T: |\n'
                + indent(
                    json.dumps(
                        {
                            'type': 'object',
                            'properties': {'u': {'$ref': '#/definitions/User'}},
                            'definitions': {'User': {'type': 'object', 'properties': {'n': {'type': 'string'}}}},
                        }
                    )
                )
            },
        )
        json_shape = raml.types_in(raml.location)['T'].shape
        shape = json_shape.as_shape()
        assert list(json_shape.as_shape_defs()) == ['User']
        assert shape.shape.properties['u'].base.name == 'User'

    def test_as_shape_defs_is_none_before_as_shape_runs(self, workspace):
        raml = parsed(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(PERSON)})
        assert raml.types_in(raml.location)['T'].shape.as_shape_defs() is None

    def test_a_cyclic_ref_becomes_a_recursive_shape(self, workspace):
        shape = project(workspace, {'type': 'object', 'properties': {'next': {'$ref': '#'}}})
        assert shape.shape.properties['next'].base.type == 'recursive'
        assert shape.shape.properties['next'].base.shape.head is shape

    def test_the_result_is_cached(self, workspace):
        raml = parsed(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(PERSON)})
        json_shape = raml.types_in(raml.location)['T'].shape
        assert json_shape.as_shape() is json_shape.as_shape()

    def test_a_view_shape_is_marked_unwrapped_and_unregistered(self, workspace):
        """They must never be fed back into the parser's own passes.

        The model looks right until P9 tries to flatten it, which is exactly the
        kind of failure that shows up far from its cause.
        """
        raml = parsed(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(PERSON)})
        shape = raml.types_in(raml.location)['T'].shape.as_shape()
        assert shape._unwrapped
        assert shape not in raml.shapes
        assert all(shape not in declared for declared in raml.fragment_typedefs.values())

    @pytest.mark.parametrize(
        ('schema', 'construct'),
        [
            ({'if': {'type': 'string'}, 'then': {'maxLength': 1}}, 'if/then/else'),
            ({'type': 'object', 'additionalProperties': {'type': 'string'}}, 'schema-form additionalProperties'),
            ({'type': 'array', 'items': [{'type': 'string'}]}, 'tuple-form items'),
            ({'type': 'object', 'properties': {'p': False}}, 'false schema'),
        ],
        ids=['if-then-else', 'additionalProperties', 'tuple-items', 'false-schema'],
    )
    def test_the_four_constructs_with_no_raml_equivalent_are_errors(self, workspace, schema, construct):
        with pytest.raises(RamlError) as caught:
            project(workspace, schema)
        assert 'JSON schema construct has no RAML equivalent' in messages(caught.value)
        assert construct in str(caught.value)


class TestAllOfProjection:
    """Neutral members and cached reference targets in docs/10 § 7."""

    @pytest.mark.parametrize(
        'neutral', [{}, True, {'description': 'No root constraint'}], ids=['empty', 'true', 'metadata']
    )
    @pytest.mark.parametrize('neutral_first', [True, False], ids=['neutral-first', 'neutral-last'])
    def test_unconstrained_members_do_not_determine_the_result_kind(self, workspace, neutral, neutral_first):
        concrete = {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['X']}}}
        members = [neutral, concrete] if neutral_first else [concrete, neutral]
        shape = project(workspace, {'allOf': members})
        assert shape.type == 'object'
        assert shape.inherits == []
        assert list(shape.shape.properties) == ['code']
        assert shape.validate({'code': 'X'}) is None
        assert shape.validate({'code': 'Y'}) is not None
        assert shape.validate('X') is not None

    def test_a_definitions_only_reference_is_neutral_in_the_tree(self, workspace):
        from fastraml import build_tree

        files = {
            'api.raml': API + 'types:\n  Derived: !include derived.json\n  Base: !include base.json\n',
            'base.json': json.dumps(
                {
                    '$schema': 'http://json-schema.org/draft-04/schema#',
                    'definitions': {'error': {'type': 'object', 'properties': {'other': {'type': 'integer'}}}},
                }
            ),
            'derived.json': json.dumps(
                {
                    '$schema': 'http://json-schema.org/draft-04/schema#',
                    'allOf': [
                        {'$ref': 'base.json'},
                        {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['X']}}},
                    ],
                }
            ),
        }
        raml = parsed(workspace, files)
        build_tree(raml)
        declared = raml.types_in(raml.location)
        derived = declared['Derived'].shape.as_shape()
        base = declared['Base'].shape.as_shape()
        assert derived.type == 'object'
        assert list(derived.shape.properties) == ['code']
        assert base.type == 'any'
        assert derived.inherits == []
        assert derived is not base
        assert derived.location.endswith('/derived.json#')
        assert base.location.endswith('/base.json#')
        for value in ({}, {'code': 'X'}, {'code': 'Y'}, 'X', 1):
            assert (derived.validate(value) is None) == (declared['Derived'].validate(value) is None)

    @pytest.mark.parametrize('neutral_first', [True, False], ids=['enum-first', 'enum-last'])
    def test_a_typeless_enum_still_constrains_the_result(self, workspace, neutral_first):
        enum, concrete = {'enum': ['X']}, {'type': 'string'}
        shape = project(workspace, {'allOf': [enum, concrete] if neutral_first else [concrete, enum]})
        assert shape.type == 'string'
        assert shape.validate('X') is None
        assert shape.validate('Y') is not None

    @pytest.mark.parametrize('base_first', [True, False], ids=['base-first', 'derived-first'])
    def test_all_of_does_not_narrow_a_shared_reference_target(self, workspace, base_first):
        declarations = ['  Base: !include base.json\n', '  Derived: !include derived.json\n']
        if not base_first:
            declarations.reverse()
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n' + ''.join(declarations),
                'base.json': json.dumps({'type': 'object', 'properties': {'code': {'type': 'string'}}}),
                'derived.json': json.dumps(
                    {
                        'allOf': [
                            {'$ref': 'base.json'},
                            {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['X']}}},
                        ]
                    }
                ),
            },
        )
        declared = raml.types_in(raml.location)
        projected_shapes = {name: declaration.shape.as_shape() for name, declaration in declared.items()}
        base, derived = projected_shapes['Base'], projected_shapes['Derived']
        assert base is not derived
        assert base.id != derived.id
        assert base.name == 'base'
        assert derived.name == 'derived'
        assert base.location.endswith('/base.json#')
        assert derived.location.endswith('/derived.json#')
        assert base.shape.properties['code'].base.enum is None
        assert base.validate({'code': 'Y'}) is None
        assert derived.validate({'code': 'Y'}) is not None
        assert derived.validate({'code': 'X'}) is None

    def test_later_members_do_not_mutate_properties_borrowed_from_a_reference(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  Derived: !include derived.json\n  Base: !include base.json\n',
                'base.json': json.dumps({'type': 'object', 'properties': {'code': {'type': 'string'}}}),
                'derived.json': json.dumps(
                    {
                        'allOf': [
                            {'type': 'object', 'properties': {'extra': {'type': 'boolean'}}},
                            {'$ref': 'base.json'},
                            {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['X']}}},
                        ]
                    }
                ),
            },
        )
        declared = raml.types_in(raml.location)
        derived = declared['Derived'].shape.as_shape()
        base = declared['Base'].shape.as_shape()
        assert list(derived.shape.properties) == ['extra', 'code']
        assert derived.validate({'code': 'Y'}) is not None
        assert base.shape.properties['code'].base.enum is None
        assert base.validate({'code': 'Y'}) is None


class TestAllOfIntersection:
    """Conjunction is commutative, including nested declarations (docs/10 § 7)."""

    @staticmethod
    def assert_matches_schema(workspace, members, values, **siblings):
        for ordered in permutations(members):
            schema = {'allOf': list(ordered), **siblings}
            raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
            declared = raml.types_in(raml.location)['T']
            projected_shape = declared.shape.as_shape()
            for value in values:
                assert (projected_shape.validate(value) is None) == (declared.validate(value) is None), (ordered, value)

    def test_numeric_bounds_take_the_strongest_restrictions_in_every_order(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'minimum': 0, 'maximum': 20}, {'minimum': 5}, {'maximum': 10}],
            [-1, 0, 4, 5, 7, 10, 11, 20],
        )

    def test_sibling_constraints_participate_in_the_intersection(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'minimum': 0}, {'minimum': 5}],
            [4, 5, 6, 7, 8],
            maximum=7,
            enum=[4, 5, 7, 8],
        )

    def test_numeric_multiples_are_intersected_not_replaced(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'multipleOf': 2}, {'multipleOf': 3}],
            [0, 2, 3, 6, 12, 15],
        )
        shape = project(workspace, {'allOf': [{'multipleOf': 1.1}, {'multipleOf': 0.3}]})
        assert shape.shape.multiple_of.value == Fraction(33, 10)
        assert shape.validate(3.3) is None
        assert shape.validate(1.1) is not None

    def test_number_and_integer_intersect_as_integer_with_rounded_bounds(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'minimum': 1.2}, {'type': 'integer', 'maximum': 3.8}],
            [1, 1.5, 2, 3, 3.5, 4],
        )

    @pytest.mark.parametrize(
        'draft', ['http://json-schema.org/draft-04/schema#', 'http://json-schema.org/draft-07/schema#']
    )
    def test_exclusive_integer_bounds_use_the_declared_draft(self, workspace, draft):
        exclusive = (
            {'minimum': 1, 'exclusiveMinimum': True, 'maximum': 4, 'exclusiveMaximum': True}
            if 'draft-04' in draft
            else {'exclusiveMinimum': 1, 'exclusiveMaximum': 4}
        )
        self.assert_matches_schema(
            workspace, [{'type': 'integer'}, exclusive], [0, 1, 2, 3, 4, 5], **{'$schema': draft}
        )

    def test_a_stronger_inclusive_bound_can_subsume_an_exclusive_number_bound(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'exclusiveMinimum': 0}, {'minimum': 5}],
            [0, 1, 4, 5, 6],
        )

    def test_constraints_for_other_instance_kinds_do_not_impose_a_type(self, workspace):
        self.assert_matches_schema(workspace, [{'type': 'string'}, {'minimum': 5}], ['', 'abc', 0, 6, None])

    def test_typeless_enum_values_select_their_actual_kinds_before_conditional_constraints(self, workspace):
        self.assert_matches_schema(workspace, [{'enum': [3, 'abc']}, {'minLength': 4}], [3, 4, 'abc', 'abcd'])

    def test_type_lists_intersect_and_keep_constraints_on_each_survivor(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': ['string', 'number', 'null'], 'minLength': 2}, {'type': ['string', 'null'], 'maxLength': 3}],
            [None, '', 'a', 'ab', 'abc', 'abcd', 1, True],
        )
        shape = project(workspace, {'allOf': [{'type': ['null', 'string', 'number']}, {'type': ['string', 'null']}]})
        assert [member.type for member in shape.shape.any_of] == ['nil', 'string']

    def test_enums_intersect_and_filter_values_that_fail_other_constraints(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'enum': [0, 2, 4, 6]}, {'enum': [2, 4, 6, 8]}, {'minimum': 3, 'multipleOf': 2}],
            [0, 2, 3, 4, 6, 8],
        )

    def test_json_enum_equality_keeps_booleans_and_numeric_strings_separate(self, workspace):
        shape = project(workspace, {'allOf': [{'enum': [1, True, '1', {'x': 1}]}, {'enum': [1.0, {'x': 1.0}]}]})
        assert [node.raw for node in shape.enum] == [1, {'x': 1}]
        assert shape.validate('1') is not None
        assert shape.validate(True) is not None
        assert shape.validate({'x': '1'}) is not None
        assert shape.validate({'x': 1.0}) is None

    def test_projected_integer_enum_uses_json_equality_without_changing_raml(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n'
                + declaration('T', json.dumps({'allOf': [{'type': 'integer'}, {'enum': [1]}]}))
            },
        )
        assert raml.types_in(raml.location)['T'].validate('1') is not None
        shape = raml.types_in(raml.location)['T'].shape.as_shape()
        assert shape.validate(1) is None
        assert shape.validate(1.0) is None
        assert shape.validate('1') is not None

    def test_const_participates_in_an_enum_intersection(self, workspace):
        self.assert_matches_schema(workspace, [{'type': 'string', 'enum': ['a', 'b']}, {'const': 'b'}], ['a', 'b', 'c'])

    def test_draft_four_const_is_an_unknown_keyword(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'string'}, {'const': 'b'}],
            ['a', 'b', 'c'],
            **{'$schema': 'http://json-schema.org/draft-04/schema#'},
        )

    def test_required_names_and_shared_property_constraints_are_combined(self, workspace):
        self.assert_matches_schema(
            workspace,
            [
                {'type': 'object', 'properties': {'x': {'type': 'number', 'minimum': 0}}, 'required': ['x']},
                {'properties': {'x': {'minimum': 5, 'maximum': 10}}, 'required': ['y']},
            ],
            [{}, {'x': 6}, {'y': True}, {'x': 4, 'y': True}, {'x': 6, 'y': True}, {'x': 11, 'y': True}],
        )

    def test_closed_members_do_not_admit_properties_declared_only_elsewhere(self, workspace):
        self.assert_matches_schema(
            workspace,
            [
                {'type': 'object', 'properties': {'x': {'type': 'string'}}, 'additionalProperties': False},
                {'properties': {'y': {'type': 'number'}}},
            ],
            [{}, {'x': 'ok'}, {'y': 1}, {'x': 'ok', 'y': 1}, {'z': True}],
        )

    def test_two_closed_members_allow_only_their_common_property_names(self, workspace):
        self.assert_matches_schema(
            workspace,
            [
                {'type': 'object', 'properties': {'x': {}, 'y': {}}, 'additionalProperties': False},
                {'properties': {'y': {}, 'z': {}}, 'additionalProperties': False},
            ],
            [{}, {'x': 1}, {'y': 1}, {'z': 1}, {'x': 1, 'y': 1}],
        )

    def test_array_items_lengths_and_uniqueness_are_intersected(self, workspace):
        self.assert_matches_schema(
            workspace,
            [
                {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1, 'maxItems': 5},
                {'items': {'maxLength': 2}, 'minItems': 2, 'maxItems': 3, 'uniqueItems': True},
            ],
            [[], ['a'], ['a', 'b'], ['a', 'a'], ['a', 'bbb'], ['a', 'b', 'c'], ['a', 'b', 'c', 'd']],
        )

    def test_single_referenced_property_preserves_type_list_constraints(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n'
                + declaration(
                    'T', json.dumps({'allOf': [{'type': 'object', 'properties': {'p': {'$ref': 'value.json'}}}, {}]})
                ),
                'value.json': json.dumps({'type': ['string', 'null'], 'minLength': 3}),
            },
        )
        declared = raml.types_in(raml.location)['T']
        projected_shape = declared.shape.as_shape()
        for value in (None, 'a', 'abc', 1):
            instance = {'p': value}
            assert (projected_shape.validate(instance) is None) == (declared.validate(instance) is None), instance

    @pytest.mark.parametrize(
        'draft', ['http://json-schema.org/draft-07/schema#', 'https://json-schema.org/draft/2020-12/schema']
    )
    def test_single_referenced_property_applies_ref_siblings_under_the_entry_draft(self, workspace, draft):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n'
                + declaration(
                    'T',
                    json.dumps(
                        {
                            '$schema': draft,
                            'allOf': [
                                {'type': 'object', 'properties': {'p': {'$ref': 'value.json'}}},
                                {},
                            ],
                        }
                    ),
                ),
                'value.json': json.dumps({'$ref': 'leaf.json', 'minimum': 5}),
                'leaf.json': json.dumps({'type': 'number'}),
            },
        )
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        for value in (1, 5, 11):
            instance = {'p': value}
            assert (shape.validate(instance) is None) == (declared.validate(instance) is None), (draft, instance)

    def test_mixed_draft_reference_siblings_are_not_projected_with_the_wrong_rules(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n'
                + declaration(
                    'T',
                    json.dumps(
                        {
                            'allOf': [
                                {'type': 'object', 'properties': {'p': {'$ref': 'value.json'}}},
                                {},
                            ]
                        }
                    ),
                ),
                'value.json': json.dumps(
                    {
                        '$schema': 'https://json-schema.org/draft/2020-12/schema',
                        '$ref': 'leaf.json',
                        'minimum': 5,
                    }
                ),
                'leaf.json': json.dumps({'type': 'number'}),
            },
        )
        declared = raml.types_in(raml.location)['T']
        assert declared.validate({'p': 1}) is None
        with pytest.raises(RamlError) as caught:
            projection_of(declared.shape)
        assert any(
            trace.info == {'construct': 'allOf mixed-draft $ref siblings'}
            for chain in caught.value.chains()
            for trace in chain
        )

    def test_false_referenced_items_admit_only_an_empty_array(self, workspace):
        raml = parsed(
            workspace,
            {
                'api.raml': API
                + 'types:\n'
                + declaration('T', json.dumps({'allOf': [{'type': 'array', 'items': {'$ref': 'false.json'}}, {}]})),
                'false.json': 'false',
            },
        )
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        for value in ([], [1], ['a']):
            assert (shape.validate(value) is None) == (declared.validate(value) is None)

    def test_integral_json_counts_are_integer_projection_facets(self, workspace):
        shape = project(workspace, {'allOf': [{'type': 'string', 'minLength': 1.0}, {'maxLength': 3.0}]})
        assert type(shape.shape.min_length.value) is int
        assert type(shape.shape.max_length.value) is int
        assert (shape.shape.min_length.value, shape.shape.max_length.value) == (1, 3)

    @pytest.mark.parametrize('bound', [float('inf'), float('nan')], ids=['Infinity', 'NaN'])
    def test_a_non_finite_bound_reports_a_projection_diagnostic(self, workspace, bound):
        # Python's `json` reads `Infinity` and `NaN`, and the meta-schema
        # takes either as a number; neither is an exact RAML bound.
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include schema.json\n',
                'schema.json': json.dumps({'allOf': [{'$ref': 'bound.json'}, {}]}),
                'bound.json': json.dumps({'type': 'number', 'minimum': bound}),
            },
        )
        with pytest.raises(RamlError) as caught:
            projection_of(raml.types_in(raml.location)['T'].shape)
        assert [[(trace.message, trace.info) for trace in chain][-1] for chain in caught.value.chains()] == [
            ('JSON schema construct has no RAML equivalent', {'construct': 'allOf invalid numeric facet'})
        ]

    def test_filtered_object_enum_does_not_use_raml_integer_string_equality(self, workspace):
        schema = {
            'allOf': [
                {'enum': [{'x': '1'}]},
                {'type': 'object', 'properties': {'x': {'type': 'integer'}}},
            ]
        }
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        declared = raml.types_in(raml.location)['T']
        assert declared.validate({'x': 1}) is not None
        assert declared.validate({'x': '1'}) is not None
        with pytest.raises(RamlError) as caught:
            projection_of(declared.shape)
        assert any(
            trace.info == {'construct': 'unsatisfiable allOf'} for chain in caught.value.chains() for trace in chain
        )

    @pytest.mark.parametrize('member', [{'x': '1'}, {'x': 1}], ids=['numeric-string', 'number'])
    def test_nested_enum_uses_json_equality(self, workspace, member):
        schema = {'allOf': [{'type': 'object'}, {'enum': [member]}]}
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        shape = raml.types_in(raml.location)['T'].shape.as_shape()
        assert shape.validate(member) is None
        other = {'x': 1} if isinstance(member['x'], str) else {'x': '1'}
        assert shape.validate(other) is not None

    def test_nullable_recursive_all_of_projects_without_recursing_forever(self, workspace):
        schema = {'allOf': [{'type': ['object', 'null'], 'properties': {'next': {'$ref': '#'}}}, {}]}
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        for value in (None, {}, {'next': None}, {'next': {}}, {'next': 1}):
            assert (shape.validate(value) is None) == (declared.validate(value) is None)

    def test_object_count_bounds_are_intersected(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'object', 'minProperties': 1, 'maxProperties': 4}, {'minProperties': 2, 'maxProperties': 3}],
            [{}, {'a': 1}, {'a': 1, 'b': 2}, {'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'b': 2, 'c': 3, 'd': 4}],
        )

    def test_nested_all_of_is_flattened_without_losing_sibling_bounds(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'number', 'allOf': [{'minimum': 0}, {'minimum': 5}]}, {'maximum': 10}],
            [0, 4, 5, 10, 11],
        )

    def test_references_in_intersected_properties_keep_their_own_directories(self, workspace):
        shape = project(
            workspace,
            {'allOf': [{'$ref': 'left/base.json'}, {'$ref': 'right/base.json'}]},
            **{
                'left/base.json': json.dumps({'type': 'object', 'properties': {'x': {'$ref': 'value.json'}}}),
                'left/value.json': json.dumps({'type': 'number', 'minimum': 5}),
                'right/base.json': json.dumps({'properties': {'x': {'$ref': 'value.json'}}}),
                'right/value.json': json.dumps({'maximum': 10}),
            },
        )
        assert shape.validate({'x': 5}) is None
        assert shape.validate({'x': 10}) is None
        assert shape.validate({'x': 4}) is not None
        assert shape.validate({'x': 11}) is not None

    @pytest.mark.parametrize(
        'draft', ['http://json-schema.org/draft-07/schema#', 'https://json-schema.org/draft/2020-12/schema']
    )
    def test_reference_siblings_follow_the_declared_draft(self, workspace, draft):
        shape = project(
            workspace,
            {'$schema': draft, 'allOf': [{'$ref': 'number.json', 'minimum': 5}, {'maximum': 10}]},
            **{'number.json': json.dumps({'type': 'number', 'minimum': 0})},
        )
        assert (shape.validate(4) is None) == ('draft-07' in draft)
        assert shape.validate(5) is None
        assert shape.validate(11) is not None

    def test_nested_ids_rebase_references_before_intersecting(self, workspace):
        shape = project(
            workspace,
            {'allOf': [{'$id': 'sub/base.json', 'allOf': [{'$ref': 'value.json'}]}, {'maximum': 10}]},
            **{'sub/value.json': json.dumps({'type': 'number', 'minimum': 5})},
        )
        assert shape.validate(4) is not None
        assert shape.validate(5) is None
        assert shape.validate(11) is not None

    @pytest.mark.parametrize(
        'schema',
        [
            {'type': 'string', 'minLength': 2},
            {'type': 'number', 'minimum': 5, 'multipleOf': 2},
            {'type': 'integer', 'minimum': 1.2, 'maximum': 3.8},
            {'type': ['string', 'null'], 'minLength': 3},
            {'type': 'object', 'properties': {'x': {'type': 'string'}}, 'required': ['y']},
            {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 2},
        ],
        ids=['string', 'number', 'integer', 'nullable', 'object', 'array'],
    )
    @pytest.mark.parametrize('value_first', [True, False])
    def test_unchanged_reference_children_keep_the_cached_projection_identity(self, workspace, schema, value_first):
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  Root: !include root.json\n  Value: !include value.json\n',
                'root.json': json.dumps(
                    {
                        'allOf': [
                            {'type': 'object', 'properties': {'value': {'$ref': 'value.json'}}},
                            {'required': ['value']},
                        ]
                    }
                ),
                'value.json': json.dumps(schema),
            },
        )
        declared = raml.types_in(raml.location)
        if value_first:
            declared['Value'].shape.as_shape()
        root = declared['Root'].shape.as_shape()
        assert root.shape.properties['value'].base is declared['Value'].shape.as_shape()
        assert root.shape.properties['value'].base.location.endswith('/value.json#')
        for value in (None, '', 'abc', 0, 1, 2, 3, 4, 6, {}, {'x': 'a', 'y': True}, [], ['a'], ['a', 'b', 'c']):
            assert (root.validate({'value': value}) is None) == (declared['Root'].validate({'value': value}) is None)

    def test_recursive_child_references_point_to_the_completed_intersection(self, workspace):
        shape = project(
            workspace,
            {
                'allOf': [
                    {'type': 'object', 'properties': {'next': {'$ref': '#'}}},
                    {'properties': {'code': {'type': 'string'}}, 'required': ['code']},
                ]
            },
        )
        assert shape.shape.properties['next'].base.shape.head is shape
        assert shape.validate({'code': 'x', 'next': {'code': 'y'}}) is None
        assert shape.validate({'code': 'x', 'next': {}}) is not None

    def test_recursive_inline_children_keep_their_own_projection_head(self, workspace):
        shape = project(
            workspace,
            {
                'allOf': [
                    {
                        'type': 'object',
                        'properties': {
                            'node': {
                                'type': 'object',
                                'properties': {'next': {'$ref': '#/allOf/0/properties/node'}},
                            }
                        },
                    },
                    {},
                ]
            },
        )
        node = shape.shape.properties['node'].base
        assert node.shape.properties['next'].base.shape.head is node

    def test_impossible_items_leave_only_the_empty_array(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'array', 'items': {'type': 'string'}}, {'items': {'type': 'number'}}],
            [[], ['x'], [1]],
        )

    def test_closed_objects_can_forbid_an_impossible_optional_property(self, workspace):
        self.assert_matches_schema(
            workspace,
            [
                {'type': 'object', 'properties': {'x': {'type': 'string'}}, 'additionalProperties': False},
                {'properties': {'x': {'type': 'number'}}},
            ],
            [{}, {'x': 'x'}, {'x': 1}],
        )

    def test_identical_patterns_and_string_lengths_are_preserved(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'string', 'pattern': '^a', 'minLength': 1}, {'pattern': '^a', 'minLength': 2, 'maxLength': 3}],
            ['', 'a', 'ab', 'abc', 'abcd', 'ba'],
        )

    def test_unknown_keywords_and_formats_remain_annotations(self, workspace):
        shape = project(workspace, {'allOf': [{'type': 'string'}, {'x-note': 'extension', 'format': 'custom-format'}]})
        assert shape.validate('anything') is None

    def test_keywords_from_newer_drafts_remain_annotations_in_draft_seven(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'object'}, {'dependentRequired': {'x': ['y']}, 'unevaluatedProperties': False}],
            [{}, {'x': 1}, {'x': 1, 'y': 2}],
        )

    def test_unsupported_conditional_keywords_for_other_kinds_are_irrelevant(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'string'}, {'dependentRequired': {'x': ['y']}}],
            ['', 'abc', {}],
            **{'$schema': 'https://json-schema.org/draft/2020-12/schema'},
        )

    def test_contains_modifiers_without_contains_are_annotations(self, workspace):
        self.assert_matches_schema(
            workspace,
            [{'type': 'array'}, {'minContains': 3, 'additionalItems': False}],
            [[], [1], [1, 2, 3]],
            **{'$schema': 'https://json-schema.org/draft/2019-09/schema'},
        )

    @pytest.mark.parametrize(
        'members',
        [
            [{'type': 'string'}, {'type': 'number'}],
            [{'minimum': 5}, {'maximum': 4}],
            [{'minLength': 5}, {'maxLength': 4}],
            [{'minItems': 2}, {'maxItems': 1}],
            [{'enum': [True]}, {'enum': [1]}],
            [{'enum': ['1']}, {'enum': [1]}],
            [{'type': 'number', 'multipleOf': 3, 'minimum': 1}, {'maximum': 2}],
            [{'type': 'object', 'additionalProperties': False}, {'required': ['x']}],
            [False, {'type': 'string'}],
            [{'type': 'object', 'required': ['x', 'y']}, {'maxProperties': 1}],
            [{'type': 'object', 'properties': {'x': {}}, 'additionalProperties': False}, {'minProperties': 2}],
            [{'type': 'integer', 'minimum': 1, 'multipleOf': 1.5}, {'maximum': 2}],
        ],
        ids=[
            'kinds',
            'numbers',
            'strings',
            'arrays',
            'boolean-enum',
            'string-enum',
            'multiples',
            'required',
            'false',
            'property-count',
            'closed-count',
            'integer-multiples',
        ],
    )
    def test_unsatisfiable_intersections_are_projection_diagnostics_in_every_order(self, workspace, members):
        for ordered in permutations(members):
            with pytest.raises(RamlError) as caught:
                project(workspace, {'allOf': list(ordered)})
            assert any(
                trace.message == 'JSON schema construct has no RAML equivalent'
                and trace.info == {'construct': 'unsatisfiable allOf'}
                for chain in caught.value.chains()
                for trace in chain
            )

    @pytest.mark.parametrize(
        ('members', 'construct'),
        [
            ([{'type': 'string', 'pattern': '^a'}, {'pattern': 'z$'}], 'allOf with multiple patterns'),
            ([{'type': 'number'}, {'exclusiveMinimum': 0}], 'allOf exclusive number bound'),
            (
                [{'type': 'object'}, {'patternProperties': {'^x': {'type': 'string'}}}],
                'allOf keyword: patternProperties',
            ),
            ([{'type': 'string'}, {'anyOf': [{'type': 'string'}, {'type': 'number'}]}], 'allOf keyword: anyOf'),
            (
                [
                    {'type': 'object', 'properties': {'x': {'type': 'string'}}},
                    {'properties': {'x': {'type': 'number'}}},
                ],
                'allOf with an impossible optional property',
            ),
            ([{'type': 'string'}, {'format': 'date-time'}], 'allOf keyword: format'),
            ([{'format': 'date-time'}, {}], 'allOf keyword: format'),
            ([{'properties': {'x': {}}}, {'minLength': 1}], 'allOf constraints on multiple inferred types'),
            ([{'type': 'object'}, {'additionalProperties': {'type': 'string'}}], 'schema-form additionalProperties'),
            ([{'type': 'array'}, {'items': [{'type': 'string'}]}], 'tuple-form items'),
        ],
    )
    def test_unrepresentable_conjunctions_are_not_silently_weakened(self, workspace, members, construct):
        for ordered in permutations(members):
            with pytest.raises(RamlError) as caught:
                project(workspace, {'allOf': list(ordered)})
            assert any(
                trace.message == 'JSON schema construct has no RAML equivalent'
                and trace.info == {'construct': construct}
                for chain in caught.value.chains()
                for trace in chain
            )

    @pytest.mark.parametrize(
        ('draft', 'member', 'keyword'),
        [
            ('2020-12', {'prefixItems': [{'type': 'string'}]}, 'prefixItems'),
            ('2020-12', {'$dynamicRef': '#/$defs/value'}, '$dynamicRef'),
            ('2019-09', {'$recursiveRef': '#/$defs/value'}, '$recursiveRef'),
            ('2020-12', {'dependentRequired': {'x': ['y']}}, 'dependentRequired'),
        ],
    )
    def test_active_newer_keywords_are_reported_instead_of_ignored(self, workspace, draft, member, keyword):
        with pytest.raises(RamlError) as caught:
            project(
                workspace,
                {
                    '$schema': f'https://json-schema.org/draft/{draft}/schema',
                    '$defs': {'value': {'type': 'number'}},
                    'allOf': [member],
                },
            )
        assert any(
            trace.message == 'JSON schema construct has no RAML equivalent'
            and trace.info == {'construct': f'allOf keyword: {keyword}'}
            for chain in caught.value.chains()
            for trace in chain
        )


class TestUuidProjection:
    """Canonical UUID spelling is a bounded string pattern (docs/10 § 7)."""

    UUID = '123e4567-e89b-12d3-a456-426614174000'

    @pytest.mark.parametrize(
        'schema',
        [
            {'type': 'string', 'format': 'uuid'},
            {'format': 'uuid'},
            {'allOf': [{'type': 'string'}, {'format': 'uuid'}]},
            {'allOf': [{'format': 'uuid'}, {'type': 'string'}]},
            {'allOf': [{'type': 'string', 'format': 'uuid'}, {'format': 'uuid'}, {'format': 'custom-format'}]},
            {'type': 'string', 'format': 'uuid', 'allOf': [{}]},
        ],
        ids=['ordinary', 'format-only', 'format-last', 'format-first', 'repeated', 'sibling'],
    )
    def test_uuid_is_an_anchored_ascii_pattern_in_ordinary_and_conjoined_schemas(self, workspace, schema):
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        assert shape.type == 'string'
        assert shape.shape.min_length.value == shape.shape.max_length.value == 36
        assert shape.shape.pattern is not None
        for value in (
            self.UUID,
            self.UUID.upper(),
            '00000000-0000-0000-0000-000000000000',
            'ffffffff-ffff-ffff-ffff-ffffffffffff',
            '',
            'not-a-uuid',
            self.UUID[:-1],
            self.UUID.replace('e', 'g'),
            self.UUID.replace('-', ''),
            self.UUID.replace('-', '_'),
            'prefix' + self.UUID,
            self.UUID + '\n',
            self.UUID + '\r\n',
            '{' + self.UUID + '}',
            'urn:uuid:' + self.UUID,
        ):
            assert (shape.validate(value) is None) == (declared.validate(value) is None), value
        build_graph(raml)

    @pytest.mark.parametrize(
        'value',
        [
            UUID + '-',
            UUID + 'uuid:',
            '1_3e4567-e89b-12d3-a456-426614174000',
            '\u066123e4567-e89b-12d3-a456-426614174000',
        ],
    )
    def test_projection_excludes_noncanonical_spellings_accepted_by_the_compiled_checker(self, workspace, value):
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n' + declaration('T', json.dumps({'type': 'string', 'format': 'uuid'})),
            },
        )
        declared = raml.types_in(raml.location)['T']
        assert declared.validate(value) is None
        assert declared.shape.as_shape().validate(value) is not None

    @pytest.mark.parametrize('conjunction', [False, True])
    def test_uuid_is_applied_only_to_string_alternatives_of_a_nullable_type(self, workspace, conjunction):
        schema = {'type': ['null', 'string'], 'format': 'uuid'}
        if conjunction:
            schema = {'allOf': [schema, {'minLength': 36, 'maxLength': 40}]}
        shape = project(workspace, schema)
        assert [member.type for member in shape.shape.any_of] == ['nil', 'string']
        assert shape.validate(None) is None
        assert shape.validate(self.UUID) is None
        assert shape.validate('not-a-uuid') is not None
        assert shape.validate(1) is not None

    @pytest.mark.parametrize('conjunction', [False, True])
    def test_uuid_format_is_irrelevant_to_a_selected_non_string_kind(self, workspace, conjunction):
        schema = {'type': 'integer', 'format': 'uuid', 'minimum': 1}
        if conjunction:
            schema = {'allOf': [schema, {'maximum': 3}]}
        shape = project(workspace, schema)
        assert shape.type == 'integer'
        assert shape.validate(2) is None
        assert shape.validate(0) is not None

    def test_bounds_and_enums_are_narrowed_without_losing_common_metadata(self, workspace):
        shape = project(
            workspace,
            {
                'title': 'Identifier',
                'description': 'Canonical UUID',
                'default': self.UUID,
                'allOf': [
                    {'type': 'string', 'format': 'uuid', 'enum': [self.UUID, 'bad', self.UUID + '-']},
                    {'minLength': 20, 'maxLength': 40},
                ],
            },
        )
        assert shape.display_name.value == 'Identifier'
        assert shape.description.value == 'Canonical UUID'
        assert shape.default.raw == self.UUID
        assert [node.raw for node in shape.enum] == [self.UUID]
        assert shape.shape.min_length.value == shape.shape.max_length.value == 36
        assert shape.validate(self.UUID) is None
        assert shape.validate(self.UUID.upper()) is not None

    @pytest.mark.parametrize('bound', [{'minLength': 37}, {'maxLength': 35}])
    def test_length_bounds_incompatible_with_uuid_are_projection_contradictions(self, workspace, bound):
        for members in permutations([{'type': 'string', 'format': 'uuid'}, bound]):
            with pytest.raises(RamlError) as caught:
                project(workspace, {'allOf': list(members)})
            assert any(
                trace.message == 'JSON schema construct has no RAML equivalent'
                and trace.info == {'construct': 'unsatisfiable allOf'}
                for chain in caught.value.chains()
                for trace in chain
            )

    def test_an_impossible_uuid_string_branch_does_not_eliminate_null(self, workspace):
        shape = project(workspace, {'allOf': [{'type': ['string', 'null'], 'format': 'uuid'}, {'maxLength': 35}]})
        assert shape.validate(None) is None
        assert shape.validate(self.UUID) is not None

    @pytest.mark.parametrize('conjunction', [False, True])
    def test_an_additional_distinct_pattern_is_not_silently_dropped(self, workspace, conjunction):
        schema = {'type': 'string', 'format': 'uuid', 'pattern': '^123'}
        if conjunction:
            schema = {'allOf': [{'type': 'string', 'format': 'uuid'}, {'pattern': '^123'}]}
        with pytest.raises(RamlError) as caught:
            project(workspace, schema)
        assert any(
            trace.message == 'JSON schema construct has no RAML equivalent'
            and trace.info == {'construct': 'allOf with multiple patterns'}
            for chain in caught.value.chains()
            for trace in chain
        )

    def test_an_identical_uuid_pattern_can_be_repeated(self, workspace):
        ordinary = project(workspace, {'type': 'string', 'format': 'uuid'})
        shape = project(
            workspace,
            {
                'allOf': [
                    {'type': 'string', 'format': 'uuid'},
                    {'pattern': ordinary.shape.pattern.value.pattern},
                ]
            },
        )
        assert shape.validate(self.UUID) is None
        assert shape.validate(self.UUID + '\n') is not None

    @pytest.mark.parametrize('keyword', ['oneOf', 'anyOf'])
    def test_uuid_format_beside_a_disjunction_is_not_discarded(self, workspace, keyword):
        with pytest.raises(RamlError) as caught:
            project(workspace, {'format': 'uuid', keyword: [{'type': 'string'}, {'type': 'null'}]})
        assert any(
            trace.message == 'JSON schema construct has no RAML equivalent'
            and trace.info == {'construct': f'{keyword} with format'}
            for chain in caught.value.chains()
            for trace in chain
        )

    @pytest.mark.parametrize('keyword', ['oneOf', 'anyOf'])
    def test_uuid_formats_inside_disjunction_members_are_projected(self, workspace, keyword):
        shape = project(workspace, {keyword: [{'type': 'string', 'format': 'uuid'}, {'type': 'null'}]})
        assert shape.validate(self.UUID) is None
        assert shape.validate(None) is None
        assert shape.validate('bad') is not None

    @pytest.mark.parametrize('value_first', [True, False])
    def test_referenced_uuid_children_keep_their_canonical_identity_and_restrictions(self, workspace, value_first):
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include root.json\n  Value: !include value.json\n',
                'root.json': json.dumps(
                    {
                        'allOf': [
                            {'type': 'object', 'properties': {'id': {'$ref': 'value.json'}}},
                            {'required': ['id']},
                        ]
                    }
                ),
                'value.json': json.dumps({'type': 'string', 'format': 'uuid'}),
            },
        )
        declared = raml.types_in(raml.location)
        if value_first:
            declared['Value'].shape.as_shape()
        shape = declared['T'].shape.as_shape()
        assert shape.shape.properties['id'].base is declared['Value'].shape.as_shape()
        assert shape.validate({'id': self.UUID}) is None
        assert shape.validate({'id': 'bad'}) is not None
        assert shape.validate({}) is not None


class TestAllOfReferenceGraphs:
    """Conjunctions preserve scoped graph identity and recursion (docs/10 § 7)."""

    def test_a_draft_four_pointer_entry_keeps_its_compiled_draft(self, workspace):
        schema = {
            '$schema': 'http://json-schema.org/draft-04/schema#',
            'definitions': {
                'Value': {'type': 'integer', 'minimum': 0},
                'T': {'allOf': [{'$ref': '#/definitions/Value', 'minimum': 5}, {'maximum': 10}]},
            },
        }
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include schema.json#/definitions/T\n',
                'schema.json': json.dumps(schema),
            },
        )
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        for value in (-1, 0, 4, 5, 10, 11):
            assert (shape.validate(value) is None) == (declared.validate(value) is None)
        assert shape.validate(4) is None

    @pytest.mark.parametrize('value_first', [True, False])
    def test_a_cached_child_cannot_hide_an_unrepresentable_nested_restriction(self, workspace, value_first):
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include root.json\n  Value: !include value.json\n',
                'root.json': json.dumps(
                    {
                        'allOf': [
                            {'type': 'object', 'properties': {'value': {'$ref': 'value.json'}}},
                            {},
                        ]
                    }
                ),
                'value.json': json.dumps(
                    {'type': 'object', 'properties': {'code': {'type': 'string', 'format': 'uri'}}}
                ),
            },
        )
        declared = raml.types_in(raml.location)
        if value_first:
            declared['Value'].shape.as_shape()
        with pytest.raises(RamlError) as caught:
            projection_of(declared['T'].shape)
        assert any(
            trace.message == 'JSON schema construct has no RAML equivalent'
            and trace.info == {'construct': 'allOf keyword: format'}
            for chain in caught.value.chains()
            for trace in chain
        )

    @pytest.mark.parametrize('site', ['member', 'property', 'items'])
    def test_external_recursive_children_do_not_inherit_the_outer_intersection(self, workspace, site):
        node = {'type': 'object', 'properties': {'next': {'$ref': '#'}}}
        if site == 'member':
            schema = {'allOf': [{'$ref': 'node.json'}, {'required': ['code']}]}
            values = [{'code': 'x'}, {'code': 'x', 'next': {}}, {'code': 'x', 'next': 1}, {}]
        elif site == 'property':
            schema = {'allOf': [{'type': 'object', 'properties': {'node': {'$ref': 'node.json'}}}, {}]}
            values = [{}, {'node': {}}, {'node': {'next': {}}}, {'node': {'next': 1}}]
        else:
            schema = {'allOf': [{'type': 'array', 'items': {'$ref': 'node.json'}}, {}]}
            values = [[], [{}], [{'next': {}}], [{'next': 1}]]
        raml = parsed(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include root.json\n  Node: !include node.json\n',
                'root.json': json.dumps(schema),
                'node.json': json.dumps(node),
            },
        )
        declared = raml.types_in(raml.location)
        shape = declared['T'].shape.as_shape()
        target = declared['Node'].shape.as_shape()
        assert target.shape.properties['next'].base.shape.head is target
        child = (
            shape.shape.properties['next'].base
            if site == 'member'
            else shape.shape.properties['node'].base
            if site == 'property'
            else shape.shape.items
        )
        assert child is target
        for value in values:
            assert (shape.validate(value) is None) == (declared['T'].validate(value) is None)
        build_graph(raml)

    def test_an_intersected_inline_child_keeps_references_to_its_original_declaration(self, workspace):
        schema = {
            'allOf': [
                {
                    'type': 'object',
                    'properties': {
                        'node': {
                            'type': 'object',
                            'properties': {'next': {'$ref': '#/allOf/0/properties/node'}},
                        }
                    },
                },
                {'properties': {'node': {'required': ['code']}}},
            ]
        }
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        composite = shape.shape.properties['node'].base
        original = composite.shape.properties['next'].base
        assert original is not composite
        assert original.shape.properties['next'].base.shape.head is original
        for value in ({'node': {}}, {'node': {'code': 1}}, {'node': {'code': 1, 'next': {}}}):
            assert (shape.validate(value) is None) == (declared.validate(value) is None)

    def test_recursive_intersections_of_several_children_reuse_the_composite_head(self, workspace):
        files = {
            'api.raml': API + 'types:\n  T: !include root.json\n',
            'root.json': json.dumps({'allOf': [{'$ref': 'left.json'}, {'$ref': 'right.json'}]}),
        }
        for name in ('left', 'right'):
            files[f'{name}.json'] = json.dumps(
                {
                    'type': 'object',
                    'properties': {name: {'type': 'string'}, 'next': {'$ref': '#'}},
                    'required': [name],
                }
            )
        raml = parsed(workspace, files)
        declared = raml.types_in(raml.location)['T']
        shape = declared.shape.as_shape()
        assert shape.shape.properties['next'].base.shape.head is shape
        for value in (
            {'left': 'a', 'right': 'b'},
            {'left': 'a', 'right': 'b', 'next': {'left': 'c', 'right': 'd'}},
            {'left': 'a', 'right': 'b', 'next': {'left': 'c'}},
        ):
            assert (shape.validate(value) is None) == (declared.validate(value) is None)
        build_graph(raml)

    @pytest.mark.parametrize('levels', [8, 16, 24])
    def test_shared_conjunctions_are_walked_linearly_not_expanded_per_path(self, workspace, monkeypatch, levels):
        import fastraml.types.schema_intersection as module

        definitions = {'n0': {'type': 'number', 'minimum': 5}}
        for index in range(1, levels + 1):
            definitions[f'n{index}'] = {'allOf': [{'$ref': f'#/definitions/n{index - 1}'}] * 2}
        schema = {'definitions': definitions, 'allOf': [{'$ref': f'#/definitions/n{levels}'}, {'maximum': 10}]}
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))})
        calls = []
        original = module._parts

        def counting(context, contents, *args):
            calls.append(contents)
            yield from original(context, contents, *args)

        monkeypatch.setattr(module, '_parts', counting)
        shape = raml.types_in(raml.location)['T'].shape.as_shape()
        assert shape.validate(5) is None
        assert shape.validate(4) is not None
        assert shape.validate(11) is not None
        assert len(calls) <= 4 * levels + 5

    def test_a_reference_only_conjunction_cycle_is_a_projection_diagnostic(self, workspace):
        schema = {
            'definitions': {'loop': {'allOf': [{'$ref': '#/definitions/loop'}]}},
            'allOf': [{'$ref': '#/definitions/loop'}],
        }
        with pytest.raises(RamlError) as caught:
            project(workspace, schema)
        assert any(
            trace.message == 'JSON schema construct has no RAML equivalent'
            and trace.info == {'construct': 'recursive allOf member'}
            for chain in caught.value.chains()
            for trace in chain
        )

    def test_a_cached_reference_graph_still_obeys_the_projection_depth_limit(self, workspace):
        definitions = {'n0': {'type': 'number'}}
        for index in range(1, 15):
            definitions[f'n{index}'] = {'allOf': [{'$ref': f'#/definitions/n{index - 1}'}]}
        schema = {'definitions': definitions, 'allOf': [{'$ref': '#/definitions/n14'}]}
        raml = parsed(workspace, {'api.raml': API + 'types:\n' + declaration('T', json.dumps(schema))}, max_depth=20)
        with pytest.raises(RamlError) as caught:
            projection_of(raml.types_in(raml.location)['T'].shape)
        assert any(
            trace.message == 'JSON schema nesting too deep' and trace.info == {'limit': 20}
            for chain in caught.value.chains()
            for trace in chain
        )


class TestTwoInlineSchemasStayApart:
    """A projection is shared on the subschema's canonical URI (docs/16 § 2).

    An inline schema has none: it compiles under the RAML file's own URI, which
    every other inline schema in that file shares. Keying on it made the second
    inline schema in a file answer with the first one's projection.
    """

    INLINE: ClassVar = """#%RAML 1.0
title: T
types:
  A:
    type: |
      {"type": "object", "properties": {"alpha": {"type": "string"}}}
  B:
    type: |
      {"type": "object", "properties": {"beta": {"type": "integer"}}}
"""

    def test_each_inline_schema_keeps_its_own_properties(self, workspace):
        root = workspace({'api.raml': self.INLINE})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        declared = raml.types_in(raml.location)
        assert sorted(projected(declared['A']).shape.properties) == ['alpha']
        assert sorted(projected(declared['B']).shape.properties) == ['beta']


class TestARecursiveSchemaSharedByTwoTypes:
    """Cycle detection and projection sharing meet here (docs/16 § 2).

    A cycle is closed on the identity of the schema node being re-entered, which
    is per walk; a projection is shared on the subschema's canonical URI, which
    is per parse. The second type must get the first type's cached projection
    *with* its recursion marker intact, and must not walk into the cycle again.
    """

    RECURSIVE: ClassVar = """#%RAML 1.0
title: T
types:
  A: !include node.json
  B: !include node.json
"""
    NODE: ClassVar = '{"type": "object", "properties": {"value": {"type": "string"}, "child": {"$ref": "#"}}}'

    @pytest.fixture
    def declared(self, workspace):
        root = workspace({'api.raml': self.RECURSIVE, 'node.json': self.NODE})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        return raml.types_in(raml.location)

    def test_both_types_project_the_whole_schema(self, declared):
        for name in ('A', 'B'):
            assert sorted(projected(declared[name]).shape.properties) == ['child', 'value']

    def test_the_cycle_is_marked_rather_than_expanded(self, declared):
        """Not `None` and not an infinite walk: the back-edge is a marker."""
        for name in ('A', 'B'):
            child = projected(declared[name]).shape.properties['child'].base
            assert isinstance(child.shape, RecursiveShape)

    def test_the_two_types_share_one_projection(self, declared):
        """One schema document, one projection — the point of sharing it."""
        assert projected(declared['A']) is projected(declared['B'])

    def test_the_schema_is_one_node_and_the_uses_are_two(self, workspace):
        """The split the addressing exists to make. A property is a *use* and
        stays with the declaration that wrote it; the subschema it ranges to is
        one thing, at the schema's own URI.
        """
        root = workspace({'api.raml': self.RECURSIVE, 'node.json': self.NODE})
        graph = build_graph(workspace.parse(root / 'api.raml', ParseOptions(unwrap=True)))
        assert f'{DEFAULT_BASE}/node.json#/properties/value' in graph.nodes
        for name in ('A', 'B'):
            assert f'{DEFAULT_BASE}#/declarations/types/{name}/property/value' in graph.nodes
