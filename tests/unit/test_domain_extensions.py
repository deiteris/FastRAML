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


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


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
