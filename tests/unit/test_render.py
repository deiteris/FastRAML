"""The effective view — docs/16-graph.md § 4.

What is worth pinning is what a reader would be misled by, not the layout:

- **every inherited property is present**, which is the whole reason the view
  exists;
- **each one is attributed to the right declaration** — the furthest ancestor
  that declares it, unless a subtype narrowed it, in which case the subtype;
- **a referenced type is named**, not flattened to `object`;
- **the output is valid YAML**, because docs/16 § 4 calls it RAML-shaped.

Layout is deliberately not asserted beyond that. A test that pins column
alignment fails on every wording change and protects nothing.
"""

from __future__ import annotations

import re

import pytest
import yaml

from fastraml import ParseOptions, parse_from_path
from fastraml.types.base import facets_of
from fastraml.types.jsonschema_ import projected
from fastraml.views.graph import build_graph
from fastraml.views.render import render

LIB = """#%RAML 1.0 Library
types:
  Entity:
    type: object
    properties:
      id:
        type: string
        maxLength: 36
  Audited:
    type: object
    properties:
      changedBy: string
  User:
    type: Entity
    properties:
      name: string
      address: Address
  Address:
    type: object
    properties:
      city: string
      country:
        type: string
        enum: [uk, us, de]
  Admin:
    type: [User, Audited]
    properties:
      level: integer
  Narrowed:
    type: Entity
    properties:
      id:
        type: string
        maxLength: 8
  UserList: User[]
  Node:
    type: object
    properties:
      child?: Node
  Priced:
    type: object
    properties:
      rate:
        type: number
        multipleOf: 1.1
  Either:
    type: string | integer
"""


@pytest.fixture
def shown(workspace):
    root = workspace({'lib.raml': LIB})
    raml = parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True))
    graph = build_graph(raml)

    def show(name: str, depth: int = 1) -> str:
        found = graph.find(name)
        assert found, f'{name} not found'
        shape = graph.shape_at(found[0])
        assert shape is not None, f'{name} is not a type'
        return '\n'.join(render(shape, depth=depth, root=graph.root))

    return show


def loaded(text: str) -> dict:
    return yaml.safe_load(text)


class TestItIsTheEffectiveType:
    def test_inherited_properties_are_present(self, shown):
        """The point of the whole thing: `Admin` declares one property and has five.

        `level` is its own; `name` and `address` come from `User`; `id` from
        `Entity` two links up; `changedBy` from the second parent.
        """
        properties = loaded(shown('Admin'))['Admin']['properties']
        assert set(properties) == {'level', 'name', 'address', 'id', 'changedBy'}

    def test_an_inherited_constraint_comes_with_it(self, shown):
        assert loaded(shown('Admin'))['Admin']['properties']['id']['maxLength'] == 36

    def test_the_parents_are_named(self, shown):
        assert loaded(shown('Admin'))['Admin']['inherits'] == ['User', 'Audited']

    def test_an_optional_property_keeps_its_question_mark(self, shown):
        assert 'child?' in loaded(shown('Node'))['Node']['properties']


class TestOrigins:
    """Where each property was really written — the question a merged type raises."""

    @staticmethod
    def notes(text: str) -> dict[str, str]:
        found = {}
        for line in text.splitlines():
            body, _, note = line.partition('  # ')
            key = body.strip().split(':')[0]
            if note:
                found[key] = note
        return found

    def test_a_grandparents_property_names_the_grandparent(self, shown):
        """Not the nearest parent. `Admin.id` is on `User` too after unwrap, and
        `Entity` is the answer a reader wants.
        """
        assert self.notes(shown('Admin'))['id'].startswith('Entity,')

    def test_an_own_property_names_no_ancestor(self, shown):
        assert not self.notes(shown('Admin'))['level'].startswith(('User', 'Entity', 'Audited'))

    def test_a_narrowed_property_is_attributed_to_the_narrower(self, shown):
        """`Narrowed` re-declares `id` with a tighter bound, so it owns it.

        This is the case the view exists to settle — is the limit 36 or 8? —
        and attributing it to `Entity` would answer it wrongly.
        """
        text = shown('Narrowed')
        assert loaded(text)['Narrowed']['properties']['id']['maxLength'] == 8
        assert not self.notes(text)['id'].startswith('Entity')

    def test_the_note_carries_a_path_and_a_line(self, shown):
        """A relative path, not a bare file name: two `common.raml` under
        different directories is exactly the project that needs this view.
        """
        assert re.fullmatch(r'Entity, lib\.raml:\d+', self.notes(shown('Admin'))['id'])


class TestNamingRatherThanFlattening:
    def test_a_property_of_a_declared_type_is_named(self, shown):
        """`alias`, not `type` — reading `type` prints `object`, which is useless."""
        assert loaded(shown('User'))['User']['properties']['address'] == 'Address'

    def test_an_array_names_its_member_type(self, shown):
        """`User[]`, not `array`. The bare word says nothing, and the member is
        the fact a reader wants — whether a list holds a shared type or an
        inline copy of one. It is also how the declaration was written.
        """
        assert loaded(shown('UserList'))['UserList']['type'] == 'User[]'

    def test_the_member_line_gives_way_to_the_name(self, shown):
        """`items: User` under `type: User[]` is the same fact twice, as
        `inherits:` is. It returns when `--depth` opens the member, which is
        more than the name.
        """
        assert 'items' not in loaded(shown('UserList', depth=1))['UserList']
        assert set(loaded(shown('UserList', depth=2))['UserList']['items']['properties']) >= {'name', 'address'}

    def test_a_union_lists_its_members(self, shown):
        assert loaded(shown('Either'))['Either']['anyOf'] == ['string', 'integer']

    def test_a_recursive_property_names_the_type_it_closes_back_to(self, shown):
        """Its own `type` is `recursive`, which says nothing about which cycle."""
        text = shown('Node')
        assert loaded(text)['Node']['properties']['child?'] == 'Node'
        assert 'recursive' in text


class TestDepth:
    def test_one_level_names_a_nested_type_without_opening_it(self, shown):
        assert loaded(shown('User', depth=1))['User']['properties']['address'] == 'Address'

    def test_two_levels_open_it(self, shown):
        address = loaded(shown('User', depth=2))['User']['properties']['address']
        assert set(address['properties']) == {'city', 'country'}

    def test_depth_does_not_expand_a_scalar_into_a_block(self, shown):
        """`name:` followed by `type: string` is two lines saying what one said."""
        assert loaded(shown('User', depth=3))['User']['properties']['name'] == 'string'

    def test_a_cycle_terminates_however_deep_it_is_asked_to_go(self, shown):
        assert loaded(shown('Node', depth=10))['Node']['properties']['child?'] == 'Node'


class TestOutputContract:
    @pytest.mark.parametrize('name', ['Admin', 'User', 'UserList', 'Node', 'Either', 'Priced', 'Address'])
    def test_it_is_valid_yaml(self, shown, name):
        """§ 9 claims the output pastes back, so it has to parse."""
        assert loaded(shown(name, depth=2)) is not None

    def test_facets_use_raml_spelling_not_the_slot_name(self, shown):
        assert 'maxLength' in shown('Entity')
        assert 'max_length' not in shown('Entity')

    def test_a_decimal_facet_does_not_pass_through_float(self, shown):
        """docs/10 § 5 is about comparison, but `1.100000000000000088`
        reaching a reader would be this module's defect all the same.
        """
        assert 'multipleOf: 1.1' in shown('Priced')


API = """#%RAML 1.0
title: Store
securitySchemes:
  oauth:
    type: OAuth 2.0
    settings:
      authorizationUri: https://e.test/a
      accessTokenUri: https://e.test/t
      authorizationGrants: [authorization_code]
      scopes: [read, write]
    describedBy:
      headers:
        Authorization:
          type: string
          description: Bearer token.
      responses:
        401:
          description: Token missing or invalid.
  plain:
    type: Pass Through
traits:
  paged:
    queryParameters:
      offset?: integer
    headers:
      X-Trait: string
resourceTypes:
  collection:
    get:
      queryParameters:
        shared?:
          type: string
          description: from the resource type
        fromType?: string
types:
  Item:
    type: object
    properties:
      sku: string
      tag: string | nil
securedBy: [oauth, plain]
/items:
  description: Everything on sale.
  type: collection
  get:
    displayName: ListItems
    description: Page through the catalogue.
    protocols: [HTTPS]
    is: [paged]
    queryParameters:
      shared?:
        type: string
        description: from the method itself
    responses:
      200:
        description: One page of items.
        body:
          application/json: Item
/open:
  get:
    securedBy: [null]
    responses:
      204:
"""


@pytest.fixture
def endpoint(workspace):
    from fastraml.views.graph import build_graph
    from fastraml.views.render import Sources, render_endpoint

    root = workspace({'api.raml': API})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    graph = build_graph(raml)
    sources = Sources.of(raml)

    def show(path: str, depth: int = 1) -> str:
        found = graph.endpoint_at(graph.find(path)[0])
        assert found is not None, f'{path} is not an endpoint'
        return '\n'.join(render_endpoint(found, depth=depth, root=graph.root, sources=sources))

    return show


class TestTheEndpointView:
    """The entity that needs this most: a resource accumulates a resource type,
    traits, inherited security and ancestor URI parameters, none of which is
    visible where it is written.
    """

    def test_the_applied_directives_are_named(self, endpoint):
        text = endpoint('/items')
        assert 'type: collection' in text
        assert 'is: [paged]' in text

    def test_everything_merged_in_is_present(self, endpoint):
        """Four sources, one list: the method, the trait, the resource type."""
        shown = loaded(endpoint('/items'))['/items']['get']
        assert set(shown['queryParameters']) == {'shared?', 'fromType?', 'offset?'}
        assert set(shown['headers']) == {'X-Trait'}

    def test_a_trait_contributed_parameter_names_the_trait(self, endpoint):
        assert 'paged,' in TestOrigins.notes(endpoint('/items'))['offset?']

    def test_a_resource_type_contributed_parameter_names_it(self, endpoint):
        assert 'collection,' in TestOrigins.notes(endpoint('/items'))['fromType?']

    def test_a_parameter_the_method_won_is_not_attributed_elsewhere(self, endpoint):
        """`shared?` is declared by the resource type *and* by the method, and
        the method wins. Naming the resource type here would answer the reader's
        actual question — which description applies — wrongly.

        An earlier version did exactly that, because it guessed a declaration's
        span as "until the next one" and the last declaration in a file has no
        next one.
        """
        text = endpoint('/items')
        assert loaded(text)['/items']['get']['queryParameters']['shared?']['description'] == 'from the method itself'
        assert 'collection' not in TestOrigins.notes(text)['shared?']

    def test_inherited_security_is_shown_where_it_applies(self, endpoint):
        """Inherited from the API root, and invisible at the resource itself."""
        assert set(loaded(endpoint('/items'))['/items']['securedBy']) == {'oauth', 'plain'}

    def test_securedby_null_round_trips_as_null(self, endpoint):
        """It *removes* inherited security (docs/09 § A3), and `null` is how
        RAML spells that. An explanatory `#` inside the flow sequence is not a
        comment, it is a syntax error — seven corpus fixtures caught that.
        """
        text = endpoint('/open')
        assert loaded(text)['/open']['get']['securedBy'] == [None]

    def test_a_response_body_names_its_type(self, endpoint):
        body = loaded(endpoint('/items'))['/items']['get']['responses'][200]['body']
        assert body == {'application/json': 'Item'}

    def test_depth_opens_the_body_type(self, endpoint):
        body = loaded(endpoint('/items', depth=2))['/items']['get']['responses'][200]['body']
        assert set(body['application/json']['properties']) == {'sku', 'tag'}

    @pytest.mark.parametrize('path', ['/items', '/open'])
    @pytest.mark.parametrize('depth', [1, 3])
    def test_it_is_valid_yaml(self, endpoint, path, depth):
        assert loaded(endpoint(path, depth=depth)) is not None


SCHEMA_API = """#%RAML 1.0
title: Schemas
types:
  errorScheme: !include err.json
  uuid: !include uuid.json
"""

ERR_JSON = """{
  "type": "object",
  "required": ["error"],
  "properties": {
    "error": {
      "type": "object",
      "properties": {
        "code": {"type": "integer"},
        "message": {"type": "string", "maxLength": 200},
        "domain": {"type": "string", "enum": ["auth", "billing"]}
      }
    },
    "trace": {"type": "array", "items": {"type": "string"}}
  }
}"""

UUID_JSON = '{"type": "string", "minLength": 36, "maxLength": 36}'


class TestJsonSchemaTypesExpand:
    """docs/16 § 4. `--depth` could never open a `JsonShape`.

    `_has_structure` tested for Object/Array/Union and fell through to `False`,
    so on a schema-heavy document — where that is *every* type — `show` printed
    a bare name at any depth and the flag did nothing.
    """

    @pytest.fixture
    def schema_shown(self, workspace):
        root = workspace({'api.raml': SCHEMA_API, 'err.json': ERR_JSON, 'uuid.json': UUID_JSON})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)

        def show(name: str, depth: int = 1) -> str:
            shape = graph.shape_at(graph.find(name)[0])
            assert shape is not None
            return '\n'.join(render(shape, depth=depth, root=graph.root))

        return show

    def test_the_schema_properties_are_shown(self, schema_shown):
        assert set(loaded(schema_shown('errorScheme'))['errorScheme']['properties']) == {'error', 'trace?'}

    def test_required_survives_the_projection(self, schema_shown):
        """`required: ["error"]` is a sibling list in JSON Schema and a flag per
        property in RAML. Losing it would silently make everything optional.
        """
        properties = loaded(schema_shown('errorScheme'))['errorScheme']['properties']
        assert 'error' in properties, 'required properties carry no `?`'
        assert 'trace?' in properties

    def test_depth_opens_a_nested_schema_object(self, schema_shown):
        inner = loaded(schema_shown('errorScheme', depth=2))['errorScheme']['properties']['error']
        assert set(inner['properties']) == {'code?', 'message?', 'domain?'}

    def test_facets_inside_the_schema_survive(self, schema_shown):
        inner = loaded(schema_shown('errorScheme', depth=2))['errorScheme']['properties']['error']
        assert inner['properties']['message?']['maxLength'] == 200
        assert inner['properties']['domain?']['enum'] == ['auth', 'billing']

    def test_a_scalar_schema_shows_its_bounds(self, schema_shown):
        shown = loaded(schema_shown('uuid'))['uuid']
        assert shown['minLength'] == 36
        assert shown['maxLength'] == 36

    def test_it_is_still_loadable_yaml(self, schema_shown):
        assert loaded(schema_shown('errorScheme', depth=3))

    def test_the_projection_survives_unwrap(self, workspace):
        """The bug underneath. `_narrow_json` carried `raw` and `validator` but
        not the compiled schema, so `as_shape()` returned None on every declared
        schema type once P9 had run — unreachable at the shape a consumer holds.
        """
        root = workspace({'api.raml': SCHEMA_API, 'err.json': ERR_JSON, 'uuid.json': UUID_JSON})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        declared = raml.types_in(raml.location)['errorScheme']
        assert declared.shape.as_shape() is not None


DEFS_API = """#%RAML 1.0
title: Definitions
types:
  Item: !include item.json
"""

#: `tag` names a definition *and* a property, which is the case that separates
#: an identity test from a "the name differs from the key" guess. `sku` is
#: inline, so it is the control: no definition, no name to print. `uuid` is the
#: one-line hop: named here, bodied in another file.
ITEM_JSON = """{
  "type": "object",
  "definitions": {
    "uuid": {"$ref": "shared.json"},
    "tag": {"type": "object", "properties": {"label": {"type": "string"}}},
    "maybe": {"oneOf": [{"type": "null"}, {"type": "integer"}]}
  },
  "properties": {
    "id": {"$ref": "#/definitions/uuid"},
    "tag": {"$ref": "#/definitions/tag"},
    "weight": {"$ref": "#/definitions/maybe"},
    "sku": {"type": "string", "minLength": 3}
  }
}"""

SHARED_JSON = '{"type": "string", "minLength": 36}'


def _typed(rendered):
    """The type of one rendered property, whichever form it took.

    A property with no facets renders in the short form, as the bare type name;
    one with facets renders as a block with a `type:` line. Which applies is a
    detail of the fixture, not of the rule under test.
    """
    return rendered if isinstance(rendered, str) else rendered['type']


class TestSchemaDefinitionsKeepTheirName:
    """docs/16 § 4. A `definitions` entry rendered as its structural word.

    `#/definitions/uuid` printed `string` and `#/definitions/contact` printed
    `object` — true, and useless: neither says whether a field reuses a shared
    schema or inlines a copy of it, which is the question this view exists to
    answer.

    The name was already on the shape. What was missing is that `BaseShape.name`
    holds a *property key* too, so reading it unguarded renames every declared
    property after itself; `definition_ids` is the identity test that separates
    the two.
    """

    @pytest.fixture
    def defs_rendered(self, workspace):
        """The rendered text, for the notes. `defs_shown` loads it as YAML,
        which drops the comments the note column is made of.
        """
        root = workspace({'api.raml': DEFS_API, 'item.json': ITEM_JSON, 'shared.json': SHARED_JSON})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)

        def show(name: str, depth: int = 1) -> str:
            shape = graph.shape_at(graph.find(name)[0])
            assert shape is not None
            return '\n'.join(render(shape, depth=depth, root=graph.root))

        return show

    @pytest.fixture
    def defs_shown(self, workspace):
        root = workspace({'api.raml': DEFS_API, 'item.json': ITEM_JSON, 'shared.json': SHARED_JSON})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)

        def show(name: str, depth: int = 1) -> dict:
            shape = graph.shape_at(graph.find(name)[0])
            assert shape is not None
            return loaded('\n'.join(render(shape, depth=depth, root=graph.root)))[name]['properties']

        return show

    def test_a_property_reads_as_its_definition(self, defs_shown):
        assert _typed(defs_shown('Item')['id?']) == 'uuid'

    def test_an_inline_property_keeps_its_structural_word(self, defs_shown):
        """The control, and the regression guard. `sku` names no definition, so
        there is nothing to print but `string` — and the naive fix, reading
        `base.name` unguarded, renders `sku` here.
        """
        assert _typed(defs_shown('Item')['sku?']) == 'string'

    def test_the_name_wins_where_it_equals_the_property_key(self, defs_shown):
        """`tag: {"$ref": "#/definitions/tag"}`. Suppressing a name that matches
        its key would lose exactly the shared-schema case this view is for, so
        the test is identity against the projection's table, not a comparison.
        """
        assert _typed(defs_shown('Item')['tag?']) == 'tag'

    def test_the_name_holds_when_the_type_is_opened(self, defs_shown):
        opened = defs_shown('Item', depth=2)['tag?']
        assert _typed(opened) == 'tag'
        assert set(opened['properties']) == {'label?'}

    def test_a_named_union_reads_as_its_name(self, defs_shown):
        """Above the member join on purpose: `maybe` is what the schema calls
        it, and `nil | integer` is one `--depth` away.
        """
        assert _typed(defs_shown('Item')['weight?']) == 'maybe'

    def test_the_union_members_are_one_depth_away(self, defs_shown):
        assert defs_shown('Item', depth=2)['weight?']['anyOf'] == ['nil', 'integer']

    def test_a_whole_schema_type_names_its_file(self, defs_rendered):
        """`type: object`, not `type: item.json`: the filename is the note."""
        assert re.search(r'type: object\s+# item\.json', defs_rendered('Item'))

    def test_a_definition_names_the_file_its_body_is_in(self, defs_rendered):
        """`uuid` is named in `item.json` and bodied in `shared.json`, and
        `shared.json` is the file a reader wants — `item.json` holds one line of
        `$ref`. `tag` is bodied inline, so it names `item.json`.
        """
        assert re.search(r'type: uuid\s+# shared\.json', defs_rendered('Item'))
        assert re.search(r'type: tag\s+# item\.json', defs_rendered('Item', depth=2))

    def test_a_definition_is_distinguishable_from_a_raml_type(self, defs_rendered):
        """`type: tag` on its own reads as a RAML type called `tag`, and sends a
        reader looking for a `types:` entry that is not there.
        """
        assert re.search(r'tag\?: tag\s+# item\.json', defs_rendered('Item'))


class TestOneFactOnce:
    def test_a_sole_named_parent_is_not_repeated_as_inherits(self, shown):
        """`type:` already prints the sole parent's name, so `inherits: [User]`
        beneath it says the same thing twice.
        """
        assert 'inherits' not in loaded(shown('UserList'))['UserList']

    def test_two_parents_still_get_the_line(self, shown):
        """`type:` cannot show both, which is what the line is for."""
        assert loaded(shown('Admin'))['Admin']['inherits'] == ['User', 'Audited']


QUOTING = """#%RAML 1.0 Library
types:
  Colon:
    type: string
    description: 'Indication of the BOT type. Example: Overeenkomst'
  Hash:
    type: string
    description: 'issue #42 is tracked here'
  Plain:
    type: string
    maxLength: 36
    pattern: '[0-9a-f]{8}-[0-9a-f]{4}'
    description: a plain sentence
  Comma:
    type: string
    enum: ['Amsterdam, NL', London]
  Looks:
    type: object
    properties:
      yes: string
      no: string
      null: string
      on: string
  Values:
    type: string
    enum: [yes, 'null', '1.0']
"""


class TestScalarsAreQuotedWhenPlainWouldNotParse:
    """Emitted by PyYAML, not by a rule written here — docs/16 § 4.

    A first attempt owned the rule: a denylist of `': '`, `' #'` and a few
    leading characters. It was right about punctuation and silently wrong about
    every string that merely *reads* as another type, on both sides of the
    colon. These pin the cases that denylist got wrong.

    The `: ` case itself was caught by the corpus graph check in
    `tests/tck/test_properties.py`, not by any fixture here.
    """

    @pytest.fixture
    def quoted(self, workspace):
        root = workspace({'lib.raml': QUOTING})
        graph = build_graph(parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True)))

        def show(name: str) -> dict:
            shape = graph.shape_at(graph.find(name)[0])
            assert shape is not None
            return loaded('\n'.join(render(shape, root=graph.root)))[name]

        return show

    def test_a_colon_in_a_description_stays_loadable(self, quoted):
        assert quoted('Colon')['description'].endswith('Overeenkomst')

    def test_a_hash_in_a_description_is_not_read_as_a_comment(self, quoted):
        assert quoted('Hash')['description'] == 'issue #42 is tracked here'

    def test_an_ordinary_description_is_left_unquoted(self, quoted):
        """Quoting everything would be safe and unreadable."""
        assert quoted('Plain')['description'] == 'a plain sentence'

    def test_an_enum_member_with_a_comma_survives(self, quoted):
        """`enum: [a, b]` is a flow sequence, so a comma inside a member splits
        one value into two and the reader cannot tell.
        """
        assert quoted('Comma')['enum'] == ['Amsterdam, NL', 'London']

    def test_a_property_named_like_a_bool_stays_a_string_key(self, quoted):
        """A key is a value too. `yes:` loads back as `True`, so a property
        called `yes` silently became a boolean key — the same defect as the
        description one, on the other side of the colon.
        """
        assert set(quoted('Looks')['properties']) == {'yes', 'no', 'null', 'on'}

    def test_an_enum_member_that_reads_as_another_type_stays_a_string(self, quoted):
        assert quoted('Values')['enum'] == ['yes', 'null', '1.0']

    def test_a_pattern_renders_as_the_regex_and_not_its_repr(self, quoted):
        """The facet holds a *compiled* pattern, and `str()` of one is
        `re.compile('...')` — Python's repr where the author's regex belongs.
        """
        assert quoted('Plain')['pattern'] == '[0-9a-f]{8}-[0-9a-f]{4}'

    def test_a_numeric_facet_is_still_a_number(self, quoted):
        """The counterweight. Routing every value through the string path made
        `maxLength: 36` the *string* "36" — PyYAML quoting it to preserve what
        it was handed, correctly and uselessly.
        """
        assert quoted('Plain')['maxLength'] == 36


class TestProseIsPartOfTheView:
    """`description` and `displayName` were on the model and never rendered.

    They are half of why a reader opens an endpoint: a resource's description
    says what it is for, and a response's says what the status code *means* —
    which a bare `404:` cannot. On a trait-heavy document the description is
    often the only part of a response that differs between two operations
    sharing one body.
    """

    def test_a_resource_description_is_shown(self, endpoint):
        assert loaded(endpoint('/items'))['/items']['description'] == 'Everything on sale.'

    def test_an_operation_carries_both_prose_facets(self, endpoint):
        get = loaded(endpoint('/items'))['/items']['get']
        assert get['displayName'] == 'ListItems'
        assert get['description'] == 'Page through the catalogue.'

    def test_a_response_description_is_shown(self, endpoint):
        """The one this was asked for: a status code with no gloss is a number."""
        assert loaded(endpoint('/items'))['/items']['get']['responses'][200]['description'] == 'One page of items.'

    def test_protocols_narrowing_the_api_is_shown(self, endpoint):
        """Nothing else in the view answers "is this method HTTPS-only?"."""
        assert loaded(endpoint('/items'))['/items']['get']['protocols'] == ['HTTPS']

    def test_a_response_without_one_gets_no_empty_key(self, endpoint):
        assert loaded(endpoint('/open'))['/open']['get']['responses'][204] is None


class TestAUnionNamesItsMembers:
    """`type: union` at the depth limit tells a reader nothing.

    Naming is not expansion, so it is not gated on `--depth`. JSON Schema makes
    this the common case rather than a corner: every nullable field is a `oneOf`
    of the type and `null`, so a schema-typed document is full of them.
    """

    def test_a_declared_union_names_its_members(self, shown):
        assert loaded(shown('Either'))['Either']['type'] == 'string | integer'

    def test_a_union_property_names_them_at_depth_one(self, endpoint):
        body = loaded(endpoint('/items', depth=2))['/items']['get']['responses'][200]['body']
        assert body['application/json']['properties']['tag'] == 'string | nil'

    def test_a_declared_name_still_beats_the_members(self, shown):
        """`_type_name` prefers the alias, so naming the members is the fallback
        for an anonymous one rather than a replacement for the declared name.
        """
        assert loaded(shown('UserList'))['UserList']['type'] == 'User[]'


class TestSecuritySchemesContribute:
    """docs/16 § 4. `describedBy` reached the view not at all.

    A scheme's headers, query parameters and responses are what a caller using
    it must send and expect -- the `Authorization` header above all -- and none
    of it was rendered.
    """

    def test_a_scheme_that_describes_nothing_keeps_the_flat_list(self, endpoint):
        """A block per name with nothing in it is worse than a list."""
        assert loaded(endpoint('/open'))['/open']['get']['securedBy'] == [None]

    def test_a_describing_scheme_gets_a_block(self, endpoint):
        secured = loaded(endpoint('/items'))['/items']['securedBy']
        assert isinstance(secured, dict)
        assert secured['oauth']['headers']['Authorization']['description'] == 'Bearer token.'

    def test_its_responses_are_shown(self, endpoint):
        secured = loaded(endpoint('/items'))['/items']['securedBy']
        assert secured['oauth']['responses'][401]['description'] == 'Token missing or invalid.'

    def test_a_scheme_without_a_describedby_is_still_named(self, endpoint):
        """Every alternative has to appear, or the reader cannot tell that
        calling it with a Pass Through scheme is an option at all.
        """
        assert 'plain' in loaded(endpoint('/items'))['/items']['securedBy']

    def test_the_contributions_are_not_merged_into_the_operation(self, endpoint):
        """The crux. `securedBy: [a, b, c]` means *any* of them, so hoisting
        every scheme's headers into `headers:` would say "send all three".
        """
        get = loaded(endpoint('/items'))['/items']['get']
        assert 'Authorization' not in (get.get('headers') or {})

    def test_a_response_the_operation_also_declares_is_not_overwritten(self, endpoint):
        """The scheme's 401 and the operation's 200 live in different blocks, so
        no precedence rule is needed -- and the spec defines none.
        """
        shown = loaded(endpoint('/items'))['/items']
        assert set(shown['get']['responses']) == {200}
        assert set(shown['securedBy']['oauth']['responses']) == {401}

    def test_it_is_still_loadable_yaml(self, endpoint):
        assert loaded(endpoint('/items', depth=3)) is not None


EXTENDED = """#%RAML 1.0
title: T
annotationTypes:
  deprecated: string
  tier:
    type: string
    enum: [gold, silver]
  limits:
    type: object
    properties:
      perMinute: integer
types:
  Money:
    type: object
    facets:
      currencyCode: string
      rounding?: integer
    properties:
      amount: number
  Price:
    type: Money
    currencyCode: EUR
    (deprecated): use Amount instead
    (tier): gold
    (limits):
      perMinute: 60
    properties:
      vat?: number
"""


class TestExtensionsAreShown:
    """`facets:`, the values supplied for them, and applied annotations.

    All three were absent, and none can be recovered by looking at the
    declaration: a custom facet's *value* is supplied by a subtype far from
    where the facet was declared, and an annotation is RAML's main extension
    point — one real document applies 187 of them.
    """

    @pytest.fixture
    def extended(self, workspace):
        root = workspace({'api.raml': EXTENDED})
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

        def show(name: str) -> dict:
            text = '\n'.join(render(graph.shape_at(graph.find(name)[0]), root=graph.root))
            return loaded(text)[name]

        return show

    def test_a_declared_facet_block_is_shown(self, extended):
        assert extended('Money')['facets'] == {'currencyCode': 'string', 'rounding?': 'integer'}

    def test_a_supplied_facet_value_is_shown_on_the_subtype(self, extended):
        """The question the view exists for: `Money` says what must be supplied,
        and only `Price` says what was.
        """
        assert extended('Price')['currencyCode'] == 'EUR'

    def test_an_annotation_round_trips_in_its_applied_form(self, extended):
        assert extended('Price')['(deprecated)'] == 'use Amount instead'
        assert extended('Price')['(tier)'] == 'gold'

    def test_a_structured_annotation_keeps_its_shape(self, extended):
        assert extended('Price')['(limits)'] == {'perMinute': 60}

    def test_a_type_with_none_gains_no_empty_keys(self, extended):
        assert 'facets' not in extended('Money') or extended('Money')['facets']
        assert not [key for key in extended('Money') if key.startswith('(')]


class TestOneFacetVocabularyForEveryEmitter:
    """`facets_of` is the only enumeration of a kind's
    constraints, so a facet added to a kind reaches every view without any
    emitter being edited.

    Asserted as agreement rather than by inspecting the helper: the failure this
    guards against is one emitter growing its own list and drifting, which no
    test of either emitter alone can see.
    """

    FACETED = """#%RAML 1.0
title: T
types:
  Bounded:
    type: string
    minLength: 2
    maxLength: 8
    pattern: ^a
  Counted:
    type: integer
    minimum: 1
    maximum: 9
    multipleOf: 2
    format: int32
  Listed:
    type: string[]
    minItems: 1
    maxItems: 3
    uniqueItems: true
  Held:
    properties:
      a: string
    minProperties: 1
    maxProperties: 2
    additionalProperties: false
"""

    @pytest.fixture
    def views(self, workspace):
        root = workspace({'api.raml': self.FACETED})
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

        def of(name: str) -> tuple[set[str], set[str], set[str]]:
            iri = graph.find(name)[0]
            shape = graph.shape_at(iri)
            declared = {facet for facet, _ in facets_of(projected(shape).shape)}
            rendered = {
                match.group(1) for line in render(shape, root=graph.root) if (match := re.match(r'\s+(\w+):', line))
            }
            return declared, rendered, set(graph.nodes[iri].attributes)

        return of

    @pytest.mark.parametrize('name', ['Bounded', 'Counted', 'Listed', 'Held'])
    def test_every_facet_the_kind_holds_reaches_both_views(self, views, name):
        declared, rendered, attributes = views(name)
        assert declared, f'{name} declares no facet, so the law is not being exercised'
        assert declared <= rendered, f'{name}: render is missing {sorted(declared - rendered)}'
        assert declared <= attributes, f'{name}: the graph is missing {sorted(declared - attributes)}'

    def test_the_two_views_spell_a_facet_identically(self, views):
        """`multipleOf`, not `multiple_of` in one and `multipleof` in the other."""
        _, rendered, attributes = views('Counted')
        assert {'minimum', 'maximum', 'multipleOf', 'format'} <= rendered & attributes
