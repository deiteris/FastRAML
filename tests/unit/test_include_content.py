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
