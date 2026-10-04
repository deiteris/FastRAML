"""P8 — binding an `(annotation)` application to the type it names.

See docs/09-security-and-annotations.md § B3 and § B4. The two halves are
separate on purpose: this pass records *where* an annotation was applied and
*what* it was declared as. Comparing the two against `allowedTargets` is P10's,
and lives with the rest of validation.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError
from fastraml.domains import DomainLocation

API = '#%RAML 1.0\ntitle: T\n'
LIB = '#%RAML 1.0 Library\n'
#: Declared as `any` throughout: P8 binds the name, and nothing here is about
#: the value. Instance validation against the declaration is P10's.
DECLARE = 'annotationTypes:\n  ann: any\n'


def parse(workspace, files: dict[str, str], entry: str = 'api.raml', **options):
    root = workspace(files)
    return workspace.parse(root / entry, ParseOptions(**options) if options else None)


def extensions(raml) -> dict[str, object]:
    return {extension.name: extension for extension in raml.domain_extensions}


class TestBinding:
    def test_an_unqualified_name_binds_to_a_local_declaration(self, workspace):
        raml = parse(workspace, {'api.raml': API + DECLARE + '(ann): 1\n'})
        bound = extensions(raml)['ann']
        assert bound.defined_by is raml.annotation_types_in(raml.location)['ann']

    def test_a_qualified_name_binds_through_uses(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API + 'uses:\n  l: lib.raml\n(l.ann): 1\n',
                'lib.raml': LIB + DECLARE,
            },
        )
        assert extensions(raml)['l.ann'].defined_by is not None

    def test_a_dotted_library_name_is_split_on_the_last_dot(self, workspace):
        # `a.b.ann` is `ann` of library `a.b`, not `b.ann` of library `a`.
        raml = parse(
            workspace,
            {
                'api.raml': API + 'uses:\n  a.b: lib.raml\n(a.b.ann): 1\n',
                'lib.raml': LIB + DECLARE,
            },
        )
        assert extensions(raml)['a.b.ann'].defined_by is not None

    def test_the_lookup_falls_back_to_data_types(self, workspace):
        # docs/04 § 3: an annotation type may extend a data type, so a name
        # found only under `types:` still binds.
        raml = parse(workspace, {'api.raml': API + 'types:\n  ann: string\n(ann): x\n'})
        assert extensions(raml)['ann'].defined_by is raml.types_in(raml.location)['ann']

    def test_an_undeclared_name_is_an_error(self, workspace):
        # Spec § Annotations: "All annotations used in an API specification MUST
        # be declared in its annotationTypes node."
        with pytest.raises(RamlError) as caught:
            parse(workspace, {'api.raml': API + '(nope): 1\n'})
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.info == {'annotation': 'nope'}

    def test_every_undeclared_name_is_reported(self, workspace):
        # CLAUDE.md: accumulate, do not fail fast. Three typos are three
        # diagnostics, not one plus a rerun.
        with pytest.raises(RamlError) as caught:
            parse(workspace, {'api.raml': API + '(one): 1\n(two): 2\n(three): 3\n'})
        named = {trace[-1].info['annotation'] for trace in caught.value.chains()}
        assert named == {'one', 'two', 'three'}

    def test_a_name_declared_in_another_library_does_not_leak(self, workspace):
        # Namespace chaining is not permitted: the library must be imported
        # where the annotation is written.
        with pytest.raises(RamlError):
            parse(
                workspace,
                {
                    'api.raml': API + 'uses:\n  l: lib.raml\n(other): 1\n',
                    'lib.raml': LIB + 'annotationTypes:\n  other: any\n',
                },
            )


class TestTargets:
    """Which site an application records, for P10's `allowedTargets` check."""

    @pytest.mark.parametrize('field', ['queryParameters', 'headers'])
    @pytest.mark.parametrize('template', ['trait', 'resource-type'])
    @pytest.mark.parametrize('library', [False, True])
    def test_a_template_parameter_targets_type_declaration(self, workspace, field, template, library):
        declaration = 'annotationTypes:\n  ann:\n    type: string\n    allowedTargets: TypeDeclaration\n'
        parameter = f'{field}:\n  id?:\n    type: string\n    (ann): some.value\n'
        if template == 'trait':
            definition = 'traits:\n  filtered:\n' + ''.join('    ' + line + '\n' for line in parameter.splitlines())
            application = '  get:\n    is: [PREFIXfiltered]\n'
        else:
            definition = 'resourceTypes:\n  filtered:\n    get:\n' + ''.join(
                '      ' + line + '\n' for line in parameter.splitlines()
            )
            application = '  type: PREFIXfiltered\n'
        files = {'api.raml': API}
        if library:
            files['lib.raml'] = LIB + declaration + definition
            files['api.raml'] += 'uses:\n  lib: lib.raml\n'
        else:
            files['api.raml'] += declaration + definition
        files['api.raml'] += '/items:\n' + application.replace('PREFIX', 'lib.' if library else '')
        raml = parse(workspace, files, unwrap=True, validate=True)
        request = raml.endpoints['/items'].operations['get'].request
        parameters = request.query_parameters if field == 'queryParameters' else request.headers
        annotation = parameters['id'].declaration.base.annotations['ann']
        assert annotation.target is DomainLocation.TYPE_DECLARATION
        assert annotation.defined_by is not None

    @pytest.mark.parametrize(
        ('files', 'entry', 'expected'),
        [
            ({'api.raml': API + DECLARE + '(ann): 1\n'}, 'api.raml', DomainLocation.API),
            ({'lib.raml': LIB + DECLARE + '(ann): 1\n'}, 'lib.raml', DomainLocation.LIBRARY),
            (
                {'api.raml': API + DECLARE + 'types:\n  T:\n    type: string\n    (ann): 1\n'},
                'api.raml',
                DomainLocation.TYPE_DECLARATION,
            ),
            (
                {'api.raml': API + 'annotationTypes:\n  ann: any\n  Other:\n    type: string\n    (ann): 1\n'},
                'api.raml',
                DomainLocation.ANNOTATION_TYPE,
            ),
            (
                {'api.raml': API + DECLARE + 'documentation:\n  - title: T\n    content: C\n    (ann): 1\n'},
                'api.raml',
                DomainLocation.DOCUMENTATION_ITEM,
            ),
            (
                {
                    'api.raml': API
                    + DECLARE
                    + 'types:\n  T:\n    type: string\n    example:\n      value: x\n      (ann): 1\n'
                },
                'api.raml',
                DomainLocation.EXAMPLE,
            ),
        ],
        ids=['api', 'library', 'type', 'annotation-type', 'documentation', 'example'],
    )
    def test_each_reachable_site_records_itself(self, workspace, files, entry, expected):
        raml = parse(workspace, files, entry)
        assert extensions(raml)['ann'].target is expected

    @pytest.mark.parametrize(
        'declared',
        [
            '    properties:\n      p:\n        type: string\n        (ann): 1\n',
            '    properties:\n      p:\n        type: array\n        items:\n          type: string\n          (ann): 1\n',
        ],
        ids=['property', 'items'],
    )
    def test_a_site_under_a_subtype_records_itself(self, workspace, declared):
        # `type: Base` defers the properties to P7, where the stack holds only
        # the root: the site is the one they were written at.
        raml = parse(
            workspace,
            {'api.raml': API + DECLARE + 'types:\n  Base: object\n  T:\n    type: Base\n' + declared},
        )
        assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION

    def test_a_data_type_fragment_root_is_a_type_declaration(self, workspace):
        # The fragment imports the declaration itself: an annotation resolves in
        # the scope of the file it is written in, never the includer's.
        raml = parse(
            workspace,
            {
                'api.raml': API + 'types:\n  T: !include dt.raml\n',
                'dt.raml': '#%RAML 1.0 DataType\nuses:\n  l: lib.raml\ntype: string\n(l.ann): 1\n',
                'lib.raml': LIB + DECLARE,
            },
        )
        assert extensions(raml)['l.ann'].target is DomainLocation.TYPE_DECLARATION

    def test_a_facet_annotation_inherits_the_enclosing_declaration(self, workspace):
        # The annotated-scalar form. The spec's target vocabulary has no member
        # for a facet, so the site is what the facet belongs to (docs/09 § B4).
        raml = parse(
            workspace,
            {
                'api.raml': API
                + DECLARE
                + 'types:\n  T:\n    type: string\n    minLength:\n      value: 2\n      (ann): 1\n'
            },
        )
        assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION

    def test_a_root_facet_annotation_records_the_root(self, workspace):
        raml = parse(workspace, {'api.raml': '#%RAML 1.0\n' + DECLARE + 'title:\n  value: T\n  (ann): 1\n'})
        assert extensions(raml)['ann'].target is DomainLocation.API

    def test_a_raising_decode_does_not_leave_a_site_on_the_stack(self):
        # `target_scope` is a context manager for this reason. A decoder that
        # raises mid-construct would otherwise leave its site behind, and every
        # annotation decoded afterwards would record the wrong one.
        from fastraml.registry import Raml

        raml = Raml(workspace_root_uri='file:///w')
        before = raml.current_ctx().target
        with pytest.raises(ValueError, match='decode failed'), raml.target_scope(DomainLocation.EXAMPLE):
            raise ValueError('decode failed')
        assert raml.current_ctx().target is before

    def test_a_scope_narrows_without_disturbing_the_anchor(self):
        # It says where an annotation is applied, never which namespace a name
        # resolves in — the two are independent (docs/04 § 4).
        from fastraml.registry import ParseCtx, Raml

        raml = Raml(workspace_root_uri='file:///w')
        anchor = object()
        raml.push_ctx(ParseCtx(anchor=anchor, target=DomainLocation.LIBRARY))
        with raml.target_scope(DomainLocation.EXAMPLE):
            assert raml.current_ctx().anchor is anchor
            assert raml.current_ctx().target is DomainLocation.EXAMPLE
        assert raml.current_ctx().target is DomainLocation.LIBRARY

    def test_a_scope_reads_the_anchor_on_entry(self):
        # As the generator it replaced did: a scope created before another is
        # pushed carries that one's anchor, not the anchor at creation.
        from fastraml.registry import ParseCtx, Raml

        raml = Raml(workspace_root_uri='file:///w')
        pending = raml.target_scope(DomainLocation.EXAMPLE)
        anchor = object()
        raml.push_ctx(ParseCtx(anchor=anchor, target=DomainLocation.LIBRARY))
        with pending:
            assert raml.current_ctx().anchor is anchor

    def test_one_scope_serves_each_anchor_and_target(self):
        # docs/12 § 2: scopes are values, so a decoder pushing the same site
        # thousands of times allocates it once.
        from fastraml.registry import Raml

        raml = Raml(workspace_root_uri='file:///w')
        with raml.target_scope(DomainLocation.EXAMPLE):
            first = raml.current_ctx()
        with raml.target_scope(DomainLocation.EXAMPLE):
            assert raml.current_ctx() is first


class TestAllowedTargets:
    @pytest.mark.parametrize('unwrap', [False, True])
    @pytest.mark.parametrize('header', ['', '#%RAML 1.0 AnnotationTypeDeclaration\n'])
    @pytest.mark.parametrize('spelling', ['!include ann.raml', '\n    type: !include ann.raml'])
    @pytest.mark.parametrize('allowed', ['Method', '[]'])
    def test_an_included_declaration_keeps_its_target_restriction(self, workspace, unwrap, header, spelling, allowed):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {
                    'api.raml': API + f'annotationTypes:\n  ann: {spelling}\n(ann): x\n',
                    'ann.raml': header + f'type: string\nallowedTargets: {allowed}\n',
                },
                unwrap=unwrap,
                validate=True,
            )
        trace = next(
            chain[-1] for chain in caught.value.chains() if chain[-1].message == 'annotation not allowed at this target'
        )
        assert trace.info == {
            'annotation': 'ann',
            'target': 'API',
            'allowed': ['Method'] if allowed == 'Method' else [],
        }

    @pytest.mark.parametrize('unwrap', [False, True])
    def test_an_include_chain_preserves_an_allowed_site(self, workspace, unwrap):
        raml = parse(
            workspace,
            {
                'api.raml': API + 'annotationTypes:\n  ann: !include outer.raml\n(ann): x\n',
                'outer.raml': '#%RAML 1.0 AnnotationTypeDeclaration\ntype: !include inner.raml\n',
                'inner.raml': '#%RAML 1.0 AnnotationTypeDeclaration\ntype: string\nallowedTargets: API\n',
            },
            unwrap=unwrap,
            validate=True,
        )
        assert extensions(raml)['ann'].target is DomainLocation.API
        if unwrap:
            assert extensions(raml)['ann'].defined_by.allowed_targets == [DomainLocation.API]

    def test_an_included_annotation_type_keeps_its_root_target(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API + 'annotationTypes:\n  ann: !include ann.raml\n',
                'ann.raml': '#%RAML 1.0 AnnotationTypeDeclaration\nuses:\n  l: lib.raml\ntype: string\n(l.meta): x\n',
                'lib.raml': LIB + 'annotationTypes:\n  meta:\n    allowedTargets: AnnotationType\n',
            },
            unwrap=True,
            validate=True,
        )
        assert extensions(raml)['l.meta'].target is DomainLocation.ANNOTATION_TYPE

    def test_an_ordinary_type_cannot_declare_allowed_targets(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(workspace, {'api.raml': API + 'types:\n  T:\n    type: string\n    allowedTargets: Method\n'})
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'allowedTargets is only valid on annotation types'
        assert trace.info == {}

    @pytest.mark.parametrize('unwrap', [False, True])
    def test_an_annotation_alias_keeps_its_restrictions(self, workspace, unwrap):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {
                    'api.raml': API
                    + 'annotationTypes:\n  Parent:\n    allowedTargets: Method\n  ann: Parent\n(ann): x\n'
                },
                unwrap=unwrap,
                validate=True,
            )
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'annotation not allowed at this target'
        assert trace.info == {'annotation': 'ann', 'target': 'API', 'allowed': ['Method']}

    def test_a_subtype_cannot_widen_allowed_targets(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {
                    'api.raml': API
                    + 'annotationTypes:\n  Parent:\n    allowedTargets: Method\n  ann:\n    type: Parent\n    allowedTargets: [Method, API]\n'
                },
                unwrap=True,
            )
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'allowedTargets constraint violation'
        assert trace.info == {'source': ['Method'], 'target': ['Method', 'API']}

    def test_a_narrowed_target_list_is_independent_and_ordered(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  Parent:\n    allowedTargets: [Resource, Method, API]\n  inherited:\n    type: Parent\n  narrowed:\n    type: Parent\n    allowedTargets: [API, Method]\n'
            },
            unwrap=True,
            validate=True,
        )
        declared = raml.entry_point.annotation_types
        assert declared['narrowed'].allowed_targets == [DomainLocation.API, DomainLocation.METHOD]
        assert declared['inherited'].allowed_targets == declared['Parent'].allowed_targets
        assert declared['inherited'].allowed_targets is not declared['Parent'].allowed_targets

    @pytest.mark.parametrize('unwrap', [False, True])
    def test_multiple_annotation_parents_intersect_their_targets(self, workspace, unwrap):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  Left:\n    allowedTargets: [Method, API]\n  Right:\n    allowedTargets: [API, Resource]\n  ann:\n    type: [Left, Right]\n    allowedTargets: API\n(ann): x\n'
            },
            unwrap=unwrap,
            validate=True,
        )
        assert extensions(raml)['ann'].target is DomainLocation.API

    def test_a_nonscalar_target_does_not_hide_later_bad_entries(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: [{bad: value}, Bogus, Nonsense]\n'},
            )
        traces = [chain[-1] for chain in caught.value.chains()]
        assert [(trace.message, trace.info) for trace in traces] == [
            ('expected a scalar value', {}),
            ('unknown annotation target', {'target': 'Bogus'}),
            ('unknown annotation target', {'target': 'Nonsense'}),
        ]

    def test_a_single_target_is_accepted(self, workspace):
        raml = parse(workspace, {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: Method\n'})
        declared = raml.annotation_types_in(raml.location)['ann']
        assert declared.allowed_targets == [DomainLocation.METHOD]

    def test_a_sequence_of_targets_is_accepted(self, workspace):
        raml = parse(
            workspace,
            {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: [Method, Resource]\n'},
        )
        declared = raml.annotation_types_in(raml.location)['ann']
        assert declared.allowed_targets == [DomainLocation.METHOD, DomainLocation.RESOURCE]

    def test_absent_and_empty_are_different(self, workspace):
        # Absent means any target is allowed; empty means none is. P10 has to
        # tell them apart, so decoding must not collapse them.
        raml = parse(
            workspace,
            {'api.raml': API + 'annotationTypes:\n  absent: string\n  empty:\n    allowedTargets: []\n'},
        )
        declared = raml.annotation_types_in(raml.location)
        assert declared['absent'].allowed_targets is None
        assert declared['empty'].allowed_targets == []

    def test_an_unknown_target_is_an_error(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(workspace, {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: Nonsense\n'})
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'unknown annotation target'
        assert trace.info == {'target': 'Nonsense'}

    def test_the_error_points_at_the_offending_entry(self, workspace):
        # In a sequence, the key says nothing about which member is wrong.
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets:\n      - Method\n      - Bogus\n'},
            )
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.info == {'target': 'Bogus'}
        assert trace.position.line == 7, 'the sequence entry, not the allowedTargets key on line 5'

    def test_every_bad_entry_is_reported(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(workspace, {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: [Bogus, Nonsense]\n'})
        named = {trace[-1].info['target'] for trace in caught.value.chains()}
        assert named == {'Bogus', 'Nonsense'}

    def test_it_is_not_mistaken_for_a_custom_facet(self, workspace):
        raml = parse(workspace, {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: Method\n'})
        assert 'allowedTargets' not in raml.annotation_types_in(raml.location)['ann'].custom_facets

    def test_a_clone_gets_its_own_list(self, workspace):
        raml = parse(workspace, {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: [Method]\n'})
        declared = raml.annotation_types_in(raml.location)['ann']
        clone = declared.clone_detached()
        assert clone.allowed_targets == [DomainLocation.METHOD]
        clone.allowed_targets.append(DomainLocation.API)
        assert declared.allowed_targets == [DomainLocation.METHOD]

    def test_a_clone_keeps_an_absent_list_absent(self, workspace):
        raml = parse(workspace, {'api.raml': API + DECLARE})
        declared = raml.annotation_types_in(raml.location)['ann']
        assert declared.clone_detached().allowed_targets is None


class TestUnwrapRebinding:
    """docs/09 § B4: P9 replaces shape objects, so `defined_by` is re-bound."""

    #: A union parent forces the merge to produce a *new* shape rather than
    #: returning the child, which is the case a missed re-bind leaves stale.
    UNION = 'annotationTypes:\n  Parent: string | integer\n  ann:\n    type: Parent\n    description: d\n(ann): 1\n'

    def test_the_binding_survives_unwrap(self, workspace):
        raml = parse(workspace, {'api.raml': API + self.UNION}, unwrap=True)
        assert extensions(raml)['ann'].defined_by is raml.annotation_types_in(raml.location)['ann']

    def test_the_bound_shape_is_one_the_unwrapped_model_holds(self, workspace):
        # The strong form: a stale binding points at a pre-merge object, which
        # unwrap dropped when it rebuilt `raml.shapes`.
        raml = parse(workspace, {'api.raml': API + self.UNION}, unwrap=True)
        assert id(extensions(raml)['ann'].defined_by) in {id(shape) for shape in raml.shapes}

    def test_binding_happens_without_unwrap_too(self, workspace):
        raml = parse(workspace, {'api.raml': API + self.UNION})
        assert extensions(raml)['ann'].defined_by is not None


class TestNestedDeclarationTargets:
    QUERY = 'queryString:\n  type: object\n  (ann): x\n'
    PROPERTY = 'properties:\n  p:\n    type: string\n    (ann): x\n'

    @pytest.mark.parametrize('site', ['method', 'trait', 'resource-type', 'security'])
    @pytest.mark.parametrize('allowed', ['TypeDeclaration', 'Method'])
    def test_query_string_establishes_a_type_declaration_site(self, workspace, site, allowed):
        query = ''.join('      ' + line + '\n' for line in self.QUERY.splitlines())
        if site == 'method':
            body = '/items:\n  get:\n' + ''.join('    ' + line + '\n' for line in self.QUERY.splitlines())
        elif site == 'trait':
            body = 'traits:\n  t:\n' + ''.join('    ' + line + '\n' for line in self.QUERY.splitlines())
            body += '/items:\n  get:\n    is: [t]\n'
        elif site == 'resource-type':
            body = 'resourceTypes:\n  t:\n    get:\n' + query + '/items:\n  type: t\n'
        else:
            body = 'securitySchemes:\n  s:\n    type: Pass Through\n    describedBy:\n' + query
        files = {'api.raml': API + f'annotationTypes:\n  ann:\n    allowedTargets: {allowed}\n' + body}
        if allowed == 'TypeDeclaration':
            raml = parse(workspace, files, unwrap=True, validate=True)
            assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION
        else:
            with pytest.raises(RamlError) as caught:
                parse(workspace, files, unwrap=True, validate=True)
            assert next(iter(caught.value.chains()))[-1].info == {
                'annotation': 'ann',
                'target': 'TypeDeclaration',
                'allowed': ['Method'],
            }

    @pytest.mark.parametrize('kind', ['object', 'Base'])
    @pytest.mark.parametrize('response', [False, True])
    @pytest.mark.parametrize('child', ['property', 'pattern-property', 'items', 'facet', 'union-member'])
    def test_a_body_child_is_a_type_declaration(self, workspace, kind, response, child):
        if child == 'items':
            declaration = f'type: {"array" if kind == "object" else "ArrayBase"}\nitems:\n  type: string\n  (ann): x\n'
        elif child == 'union-member':
            declaration = f'type: {"union" if kind == "object" else "UnionBase"}\nanyOf:\n  - type: string\n    (ann): x\n  - integer\n'
        elif child == 'facet':
            declaration = f'type: {kind}\nfacets:\n  custom?:\n    type: string\n    (ann): x\n'
        else:
            declaration = (
                f'type: {kind}\n' + self.PROPERTY.replace('p:', '/p/:')
                if child == 'pattern-property'
                else f'type: {kind}\n' + self.PROPERTY
            )
        prefix = (
            '/items:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n'
            if response
            else '/items:\n  post:\n    body:\n      application/json:\n'
        )
        indent = '            ' if response else '        '
        body = prefix + ''.join(indent + line + '\n' for line in declaration.splitlines())
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  ann:\n    allowedTargets: TypeDeclaration\ntypes:\n  Base: object\n  ArrayBase: array\n  UnionBase:\n    type: union\n    anyOf: [string, integer]\n'
                + body
            },
            unwrap=True,
            validate=True,
        )
        assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION

    def test_an_annotation_types_property_is_an_ordinary_type_declaration(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  ann:\n    allowedTargets: TypeDeclaration\n  other:\n    type: object\n'
                + ''.join('    ' + line + '\n' for line in self.PROPERTY.splitlines())
            },
            unwrap=True,
            validate=True,
        )
        assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION


class TestTemplateDefinitionTargets:
    @pytest.mark.parametrize('collection', ['traits', 'resourceTypes'])
    def test_literal_template_roots_reused_by_libraries_keep_each_namespace(self, workspace, collection):
        application = '  get:\n    is: [PREFIXt]\n' if collection == 'traits' else '  type: PREFIXt\n  get:\n'
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'uses:\n  a: a.raml\n  b: b.raml\n/a:\n'
                + application.replace('PREFIX', 'a.')
                + '/b:\n'
                + application.replace('PREFIX', 'b.'),
                'a.raml': LIB + f'annotationTypes:\n  ann: string\n{collection}:\n  t: !include shared.yaml\n',
                'b.raml': LIB + f'annotationTypes:\n  ann: string\n{collection}:\n  t: !include shared.yaml\n',
                'shared.yaml': '(ann): value\n',
            },
            unwrap=True,
            validate=True,
        )
        assert len(raml.domain_extensions) == 2
        for name in ('a', 'b'):
            endpoint = raml.endpoints['/' + name]
            annotations = endpoint.operations['get'].annotations if collection == 'traits' else endpoint.annotations
            extension = annotations['ann']
            target = DomainLocation.TRAIT if collection == 'traits' else DomainLocation.RESOURCE_TYPE
            assert extension.target is target
            assert extension.defined_by is raml.entry_point.uses[name].link.annotation_types['ann']

    @pytest.mark.parametrize('collection', ['traits', 'resourceTypes'])
    @pytest.mark.parametrize('used', [False, True])
    @pytest.mark.parametrize('facet', ['root', 'usage'])
    def test_template_annotations_target_the_definition(self, workspace, collection, used, facet):
        target = 'Trait' if collection == 'traits' else 'ResourceType'
        application = '  get:\n    is: [t]\n' if collection == 'traits' else '  type: t\n  get:\n'
        annotation = '    (ann): x\n' if facet == 'root' else '    usage:\n      value: reusable\n      (ann): x\n'
        body = f'annotationTypes:\n  ann:\n    allowedTargets: {target}\n{collection}:\n  t:\n' + annotation
        if used:
            body += '/one:\n' + application + '/two:\n' + application
        raml = parse(workspace, {'api.raml': API + body}, unwrap=True, validate=True)
        assert raml.annotation_sites is None
        assert len(raml.domain_extensions) == 1
        extension = raml.domain_extensions[0]
        assert str(extension.target) == target
        if used and facet == 'root':
            for endpoint in raml.endpoints.values():
                annotations = endpoint.operations['get'].annotations if collection == 'traits' else endpoint.annotations
                assert annotations['ann'] is extension

    def test_an_unused_templates_illegal_target_is_checked(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {'api.raml': API + 'annotationTypes:\n  ann:\n    allowedTargets: API\ntraits:\n  t:\n    (ann): x\n'},
                validate=True,
            )
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'annotation not allowed at this target'
        assert trace.info == {'annotation': 'ann', 'target': 'Trait', 'allowed': ['API']}

    def test_a_library_releases_retained_sites_without_materializing_endpoints(self, workspace):
        raml = parse(
            workspace, {'lib.raml': LIB + DECLARE + 'traits:\n  t:\n    (ann): x\n'}, 'lib.raml', validate=True
        )
        assert raml.annotation_sites is None
        assert extensions(raml)['ann'].target is DomainLocation.TRAIT

    def test_a_failed_materialization_releases_retained_sites(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + DECLARE
                + 'traits:\n  t:\n    (ann): x\n/items:\n  get:\n    responses:\n      bad: {}\n'
            }
        )
        raml, error = workspace.lenient(root / 'api.raml')
        assert error is not None
        assert raml.annotation_sites is None
        assert extensions(raml)['ann'].target is DomainLocation.TRAIT

    @pytest.mark.parametrize(
        ('header', 'collection', 'target', 'application'),
        [
            ('Trait', 'traits', DomainLocation.TRAIT, '  get:\n    is: [t]\n'),
            ('ResourceType', 'resourceTypes', DomainLocation.RESOURCE_TYPE, '  type: t\n  get:\n'),
        ],
    )
    def test_an_included_template_root_keeps_its_authored_target(
        self, workspace, header, collection, target, application
    ):
        raml = parse(
            workspace,
            {
                'api.raml': API + f'{collection}:\n  t: !include template.raml\n/one:\n' + application,
                'template.raml': f'#%RAML 1.0 {header}\nuses:\n  l: lib.raml\n(l.ann): x\n',
                'lib.raml': LIB + f'annotationTypes:\n  ann:\n    allowedTargets: {target}\n',
            },
            unwrap=True,
            validate=True,
        )
        extension = raml.domain_extensions[0]
        assert len(raml.domain_extensions) == 1
        assert extension.target is target
        assert extension.location == (workspace.root / 'template.raml').as_uri()

    def test_an_explicit_override_keeps_the_methods_target(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  ann:\n    allowedTargets: [Trait, Method]\ntraits:\n  t:\n    (ann): template\n/one:\n  get:\n    is: [t]\n    (ann): explicit\n'
            },
            unwrap=True,
            validate=True,
        )
        assert [(str(extension.target), extension.value.raw) for extension in raml.domain_extensions] == [
            ('Trait', 'template'),
            ('Method', 'explicit'),
        ]

    @pytest.mark.parametrize(('key', 'tag'), [('(<<tag>>)', 'ann'), ('<<tag>>', '(ann)')])
    def test_a_substituted_template_annotation_keeps_its_site_and_callers_namespace(self, workspace, key, tag):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + f'uses:\n  l: lib.raml\nannotationTypes:\n  ann:\n    allowedTargets: Trait\n/one:\n  get:\n    is: [{{l.t: {{tag: {tag}, text: one}}}}]\n/two:\n  get:\n    is: [{{l.t: {{tag: {tag}, text: two}}}}]\n',
                'lib.raml': LIB + f'annotationTypes:\n  ann: integer\ntraits:\n  t:\n    {key}: <<text>>\n',
            },
            unwrap=True,
            validate=True,
        )
        assert len(raml.domain_extensions) == 2
        for endpoint in raml.endpoints.values():
            extension = endpoint.operations['get'].annotations['ann']
            assert extension.target is DomainLocation.TRAIT
            assert extension.defined_by is raml.entry_point.annotation_types['ann']
            assert extension.value.raw == endpoint.uri[1:]


class TestAnnotatedScalarTargets:
    def test_an_annotated_type_preserves_a_substituted_names_namespace(self, workspace):
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'uses:\n  l: lib.raml\ntypes:\n  Model: string\n/one:\n  get:\n    is: [{l.t: {item: Model}}]\n',
                'lib.raml': LIB
                + 'annotationTypes:\n  ann:\n    allowedTargets: TypeDeclaration\ntypes:\n  Model: integer\ntraits:\n  t:\n    queryString:\n      type:\n        value: <<item>>\n        (ann): x\n',
            },
            unwrap=True,
            validate=True,
        )
        query = raml.endpoints['/one'].operations['get'].request.query_string
        assert query.type == 'string'
        assert query.inherits[0] is raml.entry_point.types['Model']
        assert extensions(raml)['ann'].defined_by is raml.entry_point.uses['l'].link.annotation_types['ann']

    @pytest.mark.parametrize('facet', ['type', 'schema', 'default', 'discriminatorValue'])
    @pytest.mark.parametrize('included', [False, True])
    def test_each_scalar_path_captures_and_checks_annotations(self, workspace, facet, included):
        prefix = '    type: string\n' if facet == 'default' else ''
        value = 'string' if facet in {'type', 'schema'} else 'x'
        if facet == 'discriminatorValue':
            prefix = '    type: object\n    discriminator: kind\n    properties:\n      kind: string\n'
        wrapper = f'value: {value}\n(ann): x\n'
        if included:
            declaration = f'    {facet}: !include wrapper.yaml\n'
        else:
            declaration = f'    {facet}:\n' + ''.join('      ' + line + '\n' for line in wrapper.splitlines())
        raml = parse(
            workspace,
            {
                'api.raml': API
                + 'annotationTypes:\n  ann:\n    allowedTargets: TypeDeclaration\ntypes:\n  T:\n'
                + prefix
                + declaration,
                'wrapper.yaml': wrapper,
            },
            unwrap=True,
            validate=True,
        )
        assert len(raml.domain_extensions) == 1
        assert extensions(raml)['ann'].target is DomainLocation.TYPE_DECLARATION
        expected_location = (workspace.root / 'wrapper.yaml').as_uri() if included else raml.location
        assert extensions(raml)['ann'].location == expected_location
        base = raml.entry_point.types['T']
        if facet == 'default':
            assert base.default.raw == 'x'
            assert base.default.location == expected_location
        elif facet == 'discriminatorValue':
            assert base.shape.discriminator_value.raw == 'x'
        else:
            assert base.type == 'string'

    def test_an_any_default_does_not_bypass_target_validation(self, workspace):
        with pytest.raises(RamlError) as caught:
            parse(
                workspace,
                {
                    'api.raml': API
                    + 'annotationTypes:\n  ann:\n    allowedTargets: Method\ntypes:\n  T:\n    type: any\n    default:\n      value: x\n      (ann): x\n'
                },
                validate=True,
            )
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'annotation not allowed at this target'
        assert trace.info == {'annotation': 'ann', 'target': 'TypeDeclaration', 'allowed': ['Method']}

    @pytest.mark.parametrize('data', ['{value: x}', '{value: x, extra: y}', '{value: {nested: x}, (ann): x}'])
    def test_ordinary_default_maps_are_not_annotation_wrappers(self, workspace, data):
        raml = parse(
            workspace,
            {'api.raml': API + f'types:\n  T:\n    type: object\n    default: {data}\n'},
            unwrap=True,
            validate=True,
        )
        assert raml.domain_extensions == []
        assert isinstance(raml.entry_point.types['T'].default.raw, dict)
