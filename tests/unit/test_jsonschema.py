"""External JSON Schema types — docs/10-validation.md section 6.

Three things are being pinned here, in the order the document states them:
compilation and the shared registry (section 6.1), the restrictions on a
JSON-schema-typed declaration (section 6.2), and the projection to a RAML shape
(section 6.3).

The two security-relevant rules have a test each, because both are silent when
broken: a relative `$ref` must resolve against the RAML file that held the
schema, and every `$ref` must go through `ResourceLoader` rather than the
network.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path
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


def parse(workspace, files: dict[str, str], **options):
    """Parse `api.raml` out of `files`; return the error, or `None`."""
    root = workspace(files)
    try:
        parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True, **options))
    except RamlError as err:
        return err
    return None


def parsed(workspace, files: dict[str, str], **options):
    root = workspace(files)
    return parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True, **options))


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
            parse_from_path(root / 'api.raml')

    def test_an_external_json_file_compiles_through_the_same_path(self, workspace):
        raml = parsed(workspace, {'api.raml': API + 'types:\n  Person: !include person.json\n', 'person.json': PERSON})
        shape = raml.types_in(raml.location)['Person']
        assert shape.type == 'json'

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

    def test_a_ref_to_a_missing_file_is_reported_at_compile_time(self, workspace):
        # Nothing validates against this type, so a library that resolved lazily
        # would never report it. The reference implementation compiles eagerly
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
        loader = CountingLoader(root)
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

    def test_a_pointer_that_names_nothing_is_an_error(self, workspace):
        document = json.dumps({'definitions': {'Person': json.loads(PERSON)}})
        files = {
            'api.raml': API + 'types:\n  Person: !include schema.json#/definitions/Missing\n',
            'schema.json': document,
        }
        error = parse(workspace, files)
        assert error is not None
        assert 'unresolvable JSON schema reference' in messages(error)


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
    def test_a_schema_type_cannot_appear_in_a_type_expression(self, workspace, expression):
        body = 'types:\n  Person: |\n' + indent(PERSON) + f'  Board:\n    properties:\n      members: {expression}\n'
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'a JSON schema type cannot be used in a type expression' in messages(error)

    def test_a_bare_reference_to_a_schema_type_is_allowed(self, workspace):
        """The spec's own examples use one: a name is not a type expression."""
        body = 'types:\n  Person: |\n' + indent(PERSON) + '  Board:\n    properties:\n      chair: Person\n'
        assert parse(workspace, {'api.raml': API + body}) is None


class TestParameterDeclarations:
    """Section 6.2's other half: four places a schema may not be used at all.

    Checked after P7 rather than at the decoders, because a parameter may name a
    JSON-schema type instead of declaring one inline.
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
    def test_a_named_schema_type_is_refused(self, workspace, facet):
        body = 'types:\n  Person: |\n' + indent(PERSON) + self.RESOURCE[facet].format(ref='Person')
        error = parse(workspace, {'api.raml': API + 'baseUri: http://x/{h}\n' + body})
        assert error is not None
        assert 'a JSON schema type is not allowed here' in messages(error)

    def test_an_inline_schema_is_refused_too(self, workspace):
        body = '/r:\n  get:\n    headers:\n      H:\n        type: |\n' + indent(PERSON, 10)
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None
        assert 'a JSON schema type is not allowed here' in messages(error)

    def test_an_ordinary_parameter_is_untouched(self, workspace):
        body = '/r:\n  get:\n    headers:\n      H: string\n'
        assert parse(workspace, {'api.raml': API + body}) is None
