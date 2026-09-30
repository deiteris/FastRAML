"""Name provenance and namespace isolation (docs/04 § 4; docs/08 § 4)."""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError

API = '#%RAML 1.0\ntitle: T\n'
LIB = '#%RAML 1.0 Library\n'


def declaration(kind, owner):
    if kind == 'trait':
        return f'traits:\n  chosen:\n    description: {owner}\n'
    if kind == 'resource-type':
        return f'resourceTypes:\n  chosen:\n    get:\n      description: {owner}\n'
    if kind == 'scheme':
        scheme = 'Basic Authentication' if owner == 'caller' else 'Digest Authentication'
        return f'securitySchemes:\n  chosen:\n    type: {scheme}\n'
    if kind == 'annotation':
        return 'annotationTypes:\n  chosen: string\n'
    return 'types:\n  chosen: ' + ('integer' if owner == 'caller' else 'string') + '\n'


@pytest.mark.parametrize(
    ('kind', 'mapping'),
    [(kind, mapping) for kind in ('trait', 'resource-type', 'scheme') for mapping in (False, True)]
    + [('annotation', False), ('data-type', False)],
)
@pytest.mark.parametrize('present', ['caller', 'template', 'both', 'neither'])
@pytest.mark.parametrize('substituted', [False, True], ids=['static-name', 'caller-name'])
def test_a_name_resolves_only_in_its_own_namespace(memory_workspace, kind, present, substituted, mapping):
    name = '<<name>>' if substituted else 'chosen'
    reference = '{' + name + ': {}}' if mapping else name
    if kind == 'resource-type':
        wrapper = f'  wrapper:\n    type: {reference}\n'
        apply = '  type: {lib.wrapper: {name: chosen}}\n' if substituted else '  type: lib.wrapper\n'
    else:
        content = {
            'trait': f'is: [{reference}]\n',
            'scheme': f'securedBy: [{reference}]\n',
            'annotation': f'({name}): value\n',
            'data-type': f'body:\n  application/json:\n    type: {name}\n',
        }[kind]
        wrapper = '  wrapper:\n' + ''.join('    ' + line + '\n' for line in content.splitlines())
        apply = '  get:\n    is: [lib.wrapper: {name: chosen}]\n' if substituted else '  get:\n    is: [lib.wrapper]\n'
    table = 'resourceTypes' if kind == 'resource-type' else 'traits'
    api = API + 'uses:\n  lib: lib.raml\n'
    library = LIB
    if present in ('caller', 'both'):
        api += declaration(kind, 'caller')
    if present in ('template', 'both'):
        library += declaration(kind, 'template')
    # Append to an existing declaration map rather than repeating its key.
    library += wrapper if library.startswith(LIB + table + ':') else table + ':\n' + wrapper
    root = memory_workspace({'api.raml': api + '/items:\n' + apply, 'lib.raml': library})
    expected = 'caller' if substituted else 'template'
    if present not in (expected, 'both'):
        with pytest.raises(RamlError) as caught:
            memory_workspace.parse(root / 'api.raml')
        field = 'annotation' if kind == 'annotation' else 'missing'
        assert any(
            frame.message == 'reference not found' and frame.info.get(field) == 'chosen'
            for chain in caught.value.chains()
            for frame in chain
        )
        return
    raml = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
    operation = raml.endpoints['/items'].operations['get']
    owner = raml.entry_point if substituted else raml.entry_point.uses['lib'].link
    if kind == 'trait':
        assert operation.traits[-1].resolved is owner.traits['chosen']
    elif kind == 'resource-type':
        assert operation.description.value == expected
        assert operation.description.location == owner.location
    elif kind == 'scheme':
        assert operation.secured_by[0].definition is owner.security_schemes['chosen']
    elif kind == 'annotation':
        assert operation.annotations['chosen'].defined_by is owner.annotation_types['chosen']
    else:
        assert operation.request.bodies['application/json'].shape.inherits[0] is owner.types['chosen']


def test_a_static_annotation_name_does_not_follow_its_substituted_value(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': API + 'uses:\n  lib: lib.raml\nannotationTypes:\n  ann: string\n'
            '/items:\n  get:\n    is: [lib.wrapper: {value: supplied}]\n',
            'lib.raml': LIB + 'annotationTypes:\n  ann: string\ntraits:\n  wrapper:\n    (ann): <<value>>\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    annotation = raml.endpoints['/items'].operations['get'].annotations['ann']
    assert annotation.defined_by is raml.entry_point.uses['lib'].link.annotation_types['ann']
    assert annotation.value.raw == 'supplied'


@pytest.mark.parametrize('forwarded', [False, True], ids=['template-argument', 'forwarded-caller-argument'])
def test_nested_template_arguments_keep_their_own_namespace(memory_workspace, forwarded):
    argument = '<<arg>>' if forwarded else 'Chosen'
    application = 'lib.wrapper: {arg: Chosen}' if forwarded else 'lib.wrapper'
    root = memory_workspace(
        {
            'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  Chosen: integer\n'
            f'/items:\n  get:\n    is: [{application}]\n',
            'lib.raml': LIB + 'types:\n  Chosen: string\ntraits:\n  inner:\n'
            '    body:\n      application/json:\n        type: <<kind>>\n'
            f'  wrapper:\n    is: [inner: {{kind: {argument}}}]\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    shape = raml.endpoints['/items'].operations['get'].request.bodies['application/json'].shape
    owner = raml.entry_point if forwarded else raml.entry_point.uses['lib'].link
    assert shape.inherits[0] is owner.types['Chosen']


@pytest.mark.parametrize('kind', ['trait', 'resource-type'])
@pytest.mark.parametrize('forwarded', [False, True])
def test_a_substituted_callee_name_does_not_change_its_arguments_namespace(memory_workspace, kind, forwarded):
    argument = '<<arg>>' if forwarded else 'Chosen'
    parameter = ', arg: Chosen' if forwarded else ''
    table = 'traits' if kind == 'trait' else 'resourceTypes'
    body = 'body:\n  application/json:\n    type: <<kind>>\n'
    if kind == 'resource-type':
        body = 'get:\n' + ''.join('  ' + line + '\n' for line in body.splitlines())
        directive = f'type: {{<<callee>>: {{kind: {argument}}}}}'
        apply = f'  type: {{lib.wrapper: {{callee: inner{parameter}}}}}\n'
    else:
        directive = f'is: [<<callee>>: {{kind: {argument}}}]'
        apply = f'  get:\n    is: [lib.wrapper: {{callee: inner{parameter}}}]\n'
    root = memory_workspace(
        {
            'api.raml': API
            + 'uses:\n  lib: lib.raml\ntypes:\n  Chosen: integer\n'
            + table
            + ':\n  inner:\n'
            + ''.join('    ' + line + '\n' for line in body.splitlines())
            + '/items:\n'
            + apply,
            'lib.raml': LIB + 'types:\n  Chosen: string\n' + table + f':\n  wrapper:\n    {directive}\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    shape = raml.endpoints['/items'].operations['get'].request.bodies['application/json'].shape
    owner = raml.entry_point if forwarded else raml.entry_point.uses['lib'].link
    assert shape.inherits[0] is owner.types['Chosen']


def test_each_trait_entry_uses_its_own_name_provenance(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': API + 'uses:\n  lib: lib.raml\ntraits:\n  chosen:\n    description: caller\n'
            '/items:\n  get:\n    is: [lib.wrapper: {name: chosen}]\n',
            'lib.raml': LIB + 'traits:\n  static:\n    displayName: library\n  wrapper:\n    is: [<<name>>, static]\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    traits = raml.endpoints['/items'].operations['get'].traits
    assert traits[1].resolved is raml.entry_point.traits['chosen']
    assert traits[2].resolved is raml.entry_point.uses['lib'].link.traits['static']


def test_a_whole_substituted_directive_list_uses_its_callers_namespace(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': API + 'uses:\n  lib: lib.raml\ntraits:\n  chosen:\n    description: caller\n'
            '/items:\n  get:\n    is: [lib.wrapper: {names: [chosen]}]\n',
            'lib.raml': LIB + 'traits:\n  chosen:\n    description: library\n  wrapper:\n    is: <<names>>\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    assert raml.endpoints['/items'].operations['get'].traits[-1].resolved is raml.entry_point.traits['chosen']


def test_literal_content_reused_by_two_libraries_keeps_each_including_namespace(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': API + 'uses:\n  a: a.raml\n  b: b.raml\n'
            '/a:\n  get:\n    is: [a.wrapper]\n/b:\n  get:\n    is: [b.wrapper]\n',
            'a.raml': LIB + declaration('scheme', 'caller') + 'traits:\n  wrapper: !include shared.yaml\n',
            'b.raml': LIB + declaration('scheme', 'template') + 'traits:\n  wrapper: !include shared.yaml\n',
            'shared.yaml': 'securedBy: [chosen]\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    for name in ('a', 'b'):
        scheme = raml.endpoints['/' + name].operations['get'].secured_by[0]
        assert scheme.definition is raml.entry_point.uses[name].link.security_schemes['chosen']


def test_processor_supplied_parameters_keep_the_applying_endpoints_scope(memory_workspace):
    root = memory_workspace(
        {
            'api.raml': API
            + 'uses:\n  lib: lib.raml\ntypes:\n  Item: integer\n/item:\n  get:\n    is: [lib.wrapper]\n',
            'lib.raml': LIB + 'types:\n  Item: string\ntraits:\n  inner:\n'
            '    queryString: <<resourcePathName | !uppercamelcase>>\n  wrapper:\n    is: [inner]\n',
        }
    )
    raml = memory_workspace.parse(root / 'api.raml')
    shape = raml.endpoints['/item'].operations['get'].request.query_string
    assert shape.alias is raml.entry_point.types['Item']
