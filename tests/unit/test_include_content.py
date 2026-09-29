"""An `!include` of a file without a RAML header is content: read as if
written where it is included (docs/03 § 4.2).

Each position below takes a mapping or a sequence; the table pairs the API
text, the included file, and what the parse holds once the content is read.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError

ROOT = '#%RAML 1.0\ntitle: T\n'
SCHEMES = 'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
OAUTH = 'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings:\n'


def _get(raml, uri='/x'):
    return raml.endpoints[uri].operations['get']


def _type(raml, name='T'):
    return raml.types_in(raml.location)[name]


POSITIONS = {
    'resource': (
        '/x: !include c.yaml\n',
        'displayName: X\nget:\n/y:\n  post:\n',
        lambda r: (r.endpoints['/x'].display_name.value, list(r.endpoints['/x'].operations), list(r.endpoints)),
        ('X', ['get'], ['/x', '/x/y']),
    ),
    'method': ('/x:\n  get: !include c.yaml\n', 'displayName: G\n', lambda r: _get(r).display_name.value, 'G'),
    'types': ('types: !include c.yaml\n', 'A: string\n', lambda r: list(r.types_in(r.location)), ['A']),
    'traits': (
        'traits: !include c.yaml\n/x:\n  get:\n    is: [t]\n',
        't:\n  description: d\n',
        lambda r: _get(r).description.value,
        'd',
    ),
    'resourceTypes': (
        'resourceTypes: !include c.yaml\n/x:\n  type: r\n',
        'r:\n  get:\n',
        lambda r: list(r.endpoints['/x'].operations),
        ['get'],
    ),
    'securitySchemes': (
        'securitySchemes: !include c.yaml\n',
        'basic:\n  type: Basic Authentication\n',
        lambda r: list(r.entry_point.security_schemes),
        ['basic'],
    ),
    'documentation': (
        'documentation: !include c.yaml\n',
        '- title: T\n  content: C\n',
        lambda r: [item.title.value for item in r.entry_point.documentation],
        ['T'],
    ),
    'responses': (
        '/x:\n  get:\n    responses: !include c.yaml\n',
        '200:\n  description: ok\n',
        lambda r: _get(r).responses['200'].description.value,
        'ok',
    ),
    'response': (
        '/x:\n  get:\n    responses:\n      200: !include c.yaml\n',
        'description: ok\n',
        lambda r: _get(r).responses['200'].description.value,
        'ok',
    ),
    'queryParameters': (
        '/x:\n  get:\n    queryParameters: !include c.yaml\n',
        'q: integer\n',
        lambda r: list(_get(r).request.query_parameters),
        ['q'],
    ),
    'protocols': ('/x:\n  get:\n    protocols: !include c.yaml\n', '[HTTPS]\n', lambda r: _get(r).protocols, ['HTTPS']),
    'root protocols': (
        'protocols: !include c.yaml\n',
        '[HTTPS]\n',
        lambda r: [p.value for p in r.entry_point.protocols],
        ['HTTPS'],
    ),
    'mediaType': (
        'mediaType: !include c.yaml\n',
        '[application/json]\n',
        lambda r: [m.value for m in r.entry_point.media_types],
        ['application/json'],
    ),
    'properties': (
        'types:\n  T:\n    properties: !include c.yaml\n',
        'a: string\n',
        lambda r: list(_type(r).shape.properties),
        ['a'],
    ),
    'enum': (
        'types:\n  T:\n    enum: !include c.yaml\n',
        '[a, b]\n',
        lambda r: [v.raw for v in _type(r).enum],
        ['a', 'b'],
    ),
    'facets': (
        'types:\n  T:\n    type: string\n    facets: !include c.yaml\n',
        'f: string\n',
        lambda r: list(_type(r).custom_facet_defs),
        ['f'],
    ),
    'allowedTargets': (
        'annotationTypes:\n  a:\n    allowedTargets: !include c.yaml\n',
        '[API]\n',
        lambda r: [str(t) for t in r.entry_point.annotation_types['a'].allowed_targets],
        ['API'],
    ),
    'xml': (
        'types:\n  T:\n    type: string\n    xml: !include c.yaml\n',
        'name: n\n',
        lambda r: _type(r).xml.name.value,
        'n',
    ),
    'fileTypes': (
        'types:\n  T:\n    type: file\n    fileTypes: !include c.yaml\n',
        '[image/png]\n',
        lambda r: [f.value for f in _type(r).shape.file_types],
        ['image/png'],
    ),
    'describedBy': (
        'securitySchemes:\n  s:\n    type: Pass Through\n    describedBy: !include c.yaml\n',
        'headers:\n  X: string\n',
        lambda r: list(r.entry_point.security_schemes['s'].described_by.headers),
        ['X'],
    ),
    'settings': (
        'securitySchemes:\n  s:\n    type: OAuth 2.0\n    settings: !include c.yaml\n',
        'accessTokenUri: https://t\nauthorizationGrants: [client_credentials]\n',
        lambda r: r.entry_point.security_schemes['s'].settings.lists['authorizationGrants'],
        ['client_credentials'],
    ),
    'a setting list': (
        OAUTH + '      accessTokenUri: https://t\n      authorizationGrants: !include c.yaml\n',
        '[client_credentials]\n',
        lambda r: r.entry_point.security_schemes['s'].settings.lists['authorizationGrants'],
        ['client_credentials'],
    ),
    'is': (
        'traits:\n  t:\n    description: d\n/x:\n  get:\n    is: !include c.yaml\n',
        '[t]\n',
        lambda r: _get(r).description.value,
        'd',
    ),
    'securedBy': (
        SCHEMES + '/x:\n  get:\n    securedBy: !include c.yaml\n',
        '[basic]\n',
        lambda r: [s.name for s in _get(r).secured_by],
        ['basic'],
    ),
}


@pytest.mark.parametrize('position', list(POSITIONS))
def test_a_position_reads_included_content_in_place(memory_workspace, position):
    api, content, read, expected = POSITIONS[position]
    root = memory_workspace({'api.raml': ROOT + api, 'c.yaml': content})
    assert read(memory_workspace.parse(root / 'api.raml')) == expected


def test_an_included_type_is_named_in_the_api_and_checked_in_its_file(memory_workspace):
    # Named in the declaring document, so `types_in` and every view reading it
    # see it there; indexed for unwrap and validation where it is written.
    root = memory_workspace(
        {
            'api.raml': ROOT + 'types: !include c.yaml\n',
            'c.yaml': 'A:\n  properties:\n    n: integer\n  example: {n: x}\n',
        }
    )
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
    frame = next(caught.value.chains())[0]
    assert (frame.message, frame.where().rsplit('/', 1)[-1]) == ('invalid example', 'c.yaml:4:12')


def test_a_subtype_in_an_included_types_map_resolves_deferred_property_names(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': ROOT + 'types: !include types.yaml\n',
            'types.yaml': (
                'Derived:\n  type: Base\n  properties:\n    items: Item[]\n'
                'Base:\n  properties:\n    id: string\n'
                'Item:\n  properties:\n    name: string\n'
            ),
        }
    )
    raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
    types = raml.entry_point.types
    assert list(types) == ['Derived', 'Base', 'Item']
    assert list(types['Derived'].shape.properties) == ['items', 'id']
    assert types['Derived'].shape.properties['items'].base.shape.items.type == 'object'
    assert types['Derived'].shape.properties['items'].base.location.endswith('/types.yaml')


def test_a_missing_deferred_property_name_in_an_included_types_map_reports_its_file(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': ROOT + 'types: !include types.yaml\n',
            'types.yaml': 'Base: object\nDerived:\n  type: Base\n  properties:\n    items: Missing[]\n',
        }
    )
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml')
    frame = next(caught.value.chains())[-1]
    assert (frame.message, frame.info, frame.where().rsplit('/', 1)[-1]) == (
        'reference not found',
        {'type': 'Missing', 'missing': 'Missing'},
        'types.yaml:5:12',
    )


def test_a_resource_is_keyed_where_it_is_written_and_decoded_in_its_file(memory_workspace):
    root = memory_workspace({'api.raml': ROOT + '/x: !include c.yaml\n', 'c.yaml': 'displayName: X\n/y:\n'})
    raml = memory_workspace.parse(root / 'api.raml')
    x, y = raml.endpoints['/x'], raml.endpoints['/x/y']
    files = [each.rsplit('/', 1)[-1] for each in (x.location, x.display_name.location, y.location)]
    assert files == ['api.raml', 'c.yaml', 'c.yaml']


def test_a_failure_in_included_content_is_reported_in_its_file(memory_workspace):
    root = memory_workspace({'api.raml': ROOT + '/x: !include c.yaml\n', 'c.yaml': 'get:\n  bogus: 1\n'})
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml')
    frame = next(caught.value.chains())[-1]
    assert (frame.message, frame.where().rsplit('/', 1)[-1]) == ('unknown field', 'c.yaml:2:3')


def test_a_typed_fragment_is_not_content(memory_workspace):
    # A ResourceType where a resource goes: its place is `resourceTypes:`.
    root = memory_workspace({'api.raml': ROOT + '/x: !include c.raml\n', 'c.raml': '#%RAML 1.0 ResourceType\nget:\n'})
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml')
    frame = next(caught.value.chains())[-1]
    assert (frame.message, frame.info['header']) == ('fragment is not allowed here', '#%RAML 1.0 ResourceType')


def test_a_text_file_is_judged_at_the_include(memory_workspace):
    root = memory_workspace({'api.raml': ROOT + '/x: !include c.md\n', 'c.md': 'get:\n'})
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml')
    frame = next(caught.value.chains())[-1]
    assert (frame.message, frame.where().rsplit('/', 1)[-1]) == ('resource must be a mapping', 'api.raml:3:5')


def test_an_empty_file_is_empty_content(memory_workspace):
    root = memory_workspace({'api.raml': ROOT + 'types: !include c.yaml\n', 'c.yaml': ''})
    raml = memory_workspace.parse(root / 'api.raml')
    assert raml.types_in(raml.location) == {}


#: Positions that take a typed fragment, given a file without a RAML header:
#: its content is the declaration, as if written in place (docs/03 § 4.2).
TYPED_POSITIONS = {
    'trait': (
        'traits:\n  t: !include c.yaml\n/x:\n  get:\n    is: [t]\n',
        'description: d\n',
        lambda r: _get(r).description.value,
        'd',
    ),
    'resource type': (
        'resourceTypes:\n  r: !include c.yaml\n/x:\n  type: r\n',
        'get:\n',
        lambda r: list(r.endpoints['/x'].operations),
        ['get'],
    ),
    'type': (
        # Linked, as a DataType fragment is: the facets are on the declaration
        # the file holds until unwrap flattens it.
        'types:\n  T: !include c.yaml\n',
        'type: string\nminLength: 2\n',
        lambda r: _type(r).link.shape.shape.min_length.value,
        2,
    ),
    'body': (
        '/x:\n  get:\n    body:\n      application/json: !include c.yaml\n',
        'type: string\n',
        lambda r: _get(r).request.bodies['application/json'].shape.type,
        'string',
    ),
    'security scheme': (
        'securitySchemes:\n  s: !include c.yaml\n/x:\n  get:\n    securedBy: [s]\n',
        'type: Basic Authentication\n',
        lambda r: _get(r).secured_by[0].definition.resolved().type,
        'Basic Authentication',
    ),
    'examples': (
        'types:\n  T:\n    type: string\n    examples: !include c.yaml\n',
        'one: x\ntwo: y\n',
        lambda r: [e.data.raw for e in _type(r).examples.entries().values()],
        ['x', 'y'],
    ),
    'documentation item': (
        'documentation:\n  - !include c.yaml\n',
        'title: X\ncontent: Y\n',
        lambda r: r.entry_point.documentation[0].title.value,
        'X',
    ),
}


@pytest.mark.parametrize('position', list(TYPED_POSITIONS))
def test_a_typed_position_reads_a_file_without_a_header_as_its_content(memory_workspace, position):
    api, content, read, expected = TYPED_POSITIONS[position]
    root = memory_workspace({'api.raml': ROOT + api, 'c.yaml': content})
    assert read(memory_workspace.parse(root / 'api.raml')) == expected


def test_included_content_resolves_names_where_it_is_included(memory_workspace):
    # Literal content has no namespace of its own: `B` is the API's.
    root = memory_workspace({'api.raml': ROOT + 'types:\n  B: integer\n  T: !include c.yaml\n', 'c.yaml': 'type: B\n'})
    raml = memory_workspace.parse(root / 'api.raml')
    assert _type(raml).link.shape.inherits == [raml.types_in(raml.location)['B']]


def test_a_uses_in_included_content_is_no_import(memory_workspace):
    # Only a typed fragment imports; content that writes `uses:` is a type
    # declaration with an unknown facet, and `l.X` names nothing.
    root = memory_workspace(
        {
            'api.raml': ROOT + 'types:\n  T: !include c.yaml\n',
            'c.yaml': 'uses:\n  l: lib.raml\ntype: l.X\n',
            'lib.raml': '#%RAML 1.0 Library\ntypes:\n  X: string\n',
        }
    )
    with pytest.raises(RamlError):
        memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))


@pytest.mark.parametrize('declared', ['traits:\n  t: !include t.yaml\n', 'traits: !include t.yaml\n'])
def test_a_grafted_template_body_is_located_in_its_file(memory_workspace, declared):
    # Its names resolve in the API, but a failure is reported where it is written.
    body = 'description: d\nbogus: 1\n' if 't: !include' in declared else 't:\n  bogus: 1\n'
    root = memory_workspace({'api.raml': ROOT + declared + '/x:\n  get:\n    is: [t]\n', 't.yaml': body})
    with pytest.raises(RamlError) as caught:
        memory_workspace.parse(root / 'api.raml')
    frame = next(caught.value.chains())[-1]
    assert (frame.message, frame.location.rsplit('/', 1)[-1]) == ('unknown field', 't.yaml')


def test_a_json_file_where_a_type_goes_is_still_a_schema(memory_workspace):
    root = memory_workspace({'api.raml': ROOT + 'types:\n  T: !include s.json\n', 's.json': '{"type": "object"}'})
    assert _type(memory_workspace.parse(root / 'api.raml')).type == 'json'


def test_deciding_content_from_fragment_reads_a_file_once(memory_workspace):
    from fastraml import parse_from_path, path_to_file_uri
    from tests.unit.conftest import CountingLoader

    root = memory_workspace(
        {
            'api.raml': ROOT + 'traits:\n  f: !include f.raml\n  c: !include c.yaml\n/x:\n  get:\n    is: [f, c]\n',
            'f.raml': '#%RAML 1.0 Trait\ndescription: f\n',
            'c.yaml': 'displayName: c\n',
        }
    )
    loader = CountingLoader(root, memory_workspace)
    parse_from_path(root / 'api.raml', ParseOptions(file_loader=loader))
    counts = loader.counts
    assert (counts[path_to_file_uri(root / 'f.raml')], counts[path_to_file_uri(root / 'c.yaml')]) == (1, 1)
