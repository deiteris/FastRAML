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
from fractions import Fraction
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
    def test_a_schema_type_may_appear_in_a_type_expression(self, workspace, expression):
        """Deviation D11. The spec refuses all three; fastRAML builds them.

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
        """The boundary of D11. Inheritance asks for a RAML facet to be merged
        into a compiled schema, and there is no such operation.
        """
        body = 'types:\n  Person: |\n' + indent(PERSON) + '  Boss:\n    type: Person\n    minLength: 3\n'
        error = parse(workspace, {'api.raml': API + body})
        assert error is not None


SCALAR_SCHEMA = json.dumps({'type': 'string', 'minLength': 4})


class TestParameterDeclarations:
    """Deviation D11: a schema type *is* a type, including in a parameter.

    The spec forbids one outright in a query parameter, query string, URI
    parameter or header. fastRAML permits it — `docs/01` § 4 D11 — because a
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


def project(workspace, schema: dict, **files: str):
    """`as_shape()` of a type declared by `schema`."""
    raml = parsed(workspace, {'api.raml': API + 'types:\n  T: |\n' + indent(json.dumps(schema)), **files})
    return raml.types_in(raml.location)['T'].shape.as_shape()


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
        # from its decimal text, so it is exactly 11/10 (docs/10 § 5.3).
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

    def test_all_of_merges_sequentially(self, workspace):
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


class TestTwoInlineSchemasStayApart:
    """A projection is shared on the subschema's canonical URI (docs/16 § 3.2b).

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
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        declared = raml.types_in(raml.location)
        assert sorted(projected(declared['A']).shape.properties) == ['alpha']
        assert sorted(projected(declared['B']).shape.properties) == ['beta']


class TestARecursiveSchemaSharedByTwoTypes:
    """Cycle detection and projection sharing meet here (docs/16 section 3.2b).

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
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
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
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert f'{DEFAULT_BASE}/node.json#/properties/value' in graph.nodes
        for name in ('A', 'B'):
            assert f'{DEFAULT_BASE}#/declarations/types/{name}/property/value' in graph.nodes
