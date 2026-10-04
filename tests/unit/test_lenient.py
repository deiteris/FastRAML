"""`parse_lenient` — docs/13-public-api.md § 1.

Two properties, and they pull against each other. It must return a model useful
enough for an editor to keep working on a document mid-edit, and it must not
pretend to return one when there is nothing there.

The interesting assertions are about what *survives* a failure, not about the
error: an error is easy, and a strict parse already produces it.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, Stage, parse_lenient, path_to_file_uri
from tests.unit.conftest import write_files

API = '#%RAML 1.0\ntitle: T\n'
LIB = '#%RAML 1.0 Library\n'
BOTH = ParseOptions(unwrap=True, validate=True)


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


class TestCleanInput:
    def test_a_binary_entry_raises_a_reading_diagnostic(self, workspace):
        root = workspace({'api.raml': API})
        workspace.files[path_to_file_uri(root / 'api.raml')] = b'\xff\x00'
        with pytest.raises(RamlError) as caught:
            workspace.lenient(root / 'api.raml', BOTH)
        assert caught.value.head.message == 'entry is not UTF-8'

    def test_a_valid_document_reports_no_error(self, workspace):
        raml, error = workspace.lenient_document(API + 'types:\n  T: string\n', BOTH)
        assert error is None
        assert raml.entry_point.types['T'].type == 'string'

    def test_the_model_matches_a_strict_parse(self, workspace):
        """Leniency changes what happens on failure, and nothing else."""
        files = {'api.raml': API + 'types:\n  T:\n    type: string\n    minLength: 2\n/r:\n  get:\n'}
        root = workspace(files)
        lenient, error = workspace.lenient(root / 'api.raml', BOTH)
        strict = workspace.parse(root / 'api.raml', BOTH)
        assert error is None
        assert list(lenient.endpoints) == list(strict.endpoints)
        assert list(lenient.entry_point.types) == list(strict.entry_point.types)


class TestPartialModel:
    def test_a_bad_example_still_yields_the_endpoints(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  T:\n    type: integer\n    example: not-an-integer\n' + '/things:\n  get:\n  post:\n', BOTH
        )
        assert error is not None
        assert 'invalid example' in messages(error)
        assert list(raml.endpoints) == ['/things']
        assert sorted(raml.endpoints['/things'].operations) == ['get', 'post']

    def test_an_undeclared_type_still_yields_the_declared_ones(self, workspace):
        raml, error = workspace.lenient_document(API + 'types:\n  Good: string\n  Bad: NoSuchType\n', BOTH)
        assert error is not None
        assert raml.entry_point.types['Good'].type == 'string'

    def test_a_decode_time_error_still_yields_an_entry_point(self, workspace):
        """The commonest state an editor sees, and the one that shaped `_FATAL`.

        `minLength: two` fails during P1-P3, before `raml.entry_point` is
        assigned — so an `entry_point is None` test for "nothing to hand back"
        would call this fatal. The fragment was registered before its body was
        decoded, so there is in fact a partial one to return.
        """
        raml, error = workspace.lenient_document(
            API + 'types:\n  T:\n    type: string\n    minLength: two\n/r:\n  get:\n', BOTH
        )
        assert error is not None
        assert raml.entry_point is not None
        assert raml.entry_point.title.value == 'T'

    @pytest.mark.parametrize(
        ('header', 'shell'),
        [
            ('#%RAML 1.0\n', True),
            (LIB, True),
            ('#%RAML 1.0 Extension\nextends: base.raml\n', False),
            ('#%RAML 1.0 Overlay\nextends: base.raml\n', False),
        ],
        ids=['api', 'library', 'extension', 'overlay'],
    )
    def test_an_entry_that_does_not_compose_leaves_a_shell_or_nothing(self, workspace, header, shell):
        """docs/13 § 1: with `completed` empty, `entry_point` may be `None` or
        an empty shell; the syntax error is returned, not raised (docs/19 § 6)."""
        root = workspace({'entry.raml': header + 'title: T\nfoo: [unclosed\n', 'base.raml': API})
        raml, error = workspace.lenient(root / 'entry.raml', BOTH)
        assert error is not None
        assert raml.completed == []
        assert raml.stopped_at is Stage.DECODED
        if shell:
            assert raml.entry_point is raml.fragments[path_to_file_uri(root / 'entry.raml')]
        else:
            assert raml.entry_point is None

    def test_a_broken_library_leaves_the_entry_point_readable(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  T: string\n',
                'lib.raml': LIB + 'types:\n  B: NoSuchType\n',
            }
        )
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert raml.entry_point is not None
        assert raml.entry_point.types['T'].type == 'string'


LIBRARIES = {
    'lib.raml': LIB + 'uses:\n  inner: inner.raml\ntypes:\n  A: string\n',
    'inner.raml': LIB + 'types:\n  I: string\n',
}
BAD_LIB = LIB + 'uses:\n  inner: inner.raml\ntypes:\n  A: string\n  B:\n    minLength: two\n'


class TestUsesIsResolvedPastADecodeFailure:
    """docs/11 § 2, P1-P3 row: a mistake in a fragment's body does not leave
    its `uses:` unlinked, and a link whose library failed is marked
    (docs/13 § 1)."""

    def test_a_failure_in_the_entry_body_still_links_its_libraries(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  lib: lib.raml\nzzUnknown: 1\n', **LIBRARIES})
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        link = raml.entry_point.uses['lib']
        assert link.link is not None
        assert list(link.link.types) == ['A']
        assert link.id not in raml.broken

    def test_a_library_that_failed_is_linked_as_it_stands_and_marked(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  lib: lib.raml\n', **LIBRARIES, 'lib.raml': BAD_LIB})
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        link = raml.entry_point.uses['lib']
        assert link.link is raml.fragments[path_to_file_uri(root / 'lib.raml')]
        assert list(link.link.types) == ['A', 'B']
        assert link.link.types['B'].id in raml.broken
        assert raml.broken[link.id].head.message == 'parse uses library'
        assert error is not None

    def test_a_library_that_failed_still_links_its_own_libraries(self, workspace):
        root = workspace({'api.raml': API + 'uses:\n  lib: lib.raml\n', **LIBRARIES, 'lib.raml': BAD_LIB})
        raml, _error = workspace.lenient(root / 'api.raml', BOTH)
        inner = raml.entry_point.uses['lib'].link.uses['inner']
        assert inner.link is not None
        assert list(inner.link.types) == ['I']
        assert inner.id not in raml.broken

    def test_a_second_entry_naming_a_failed_library_is_marked_and_reports_nothing(self, workspace):
        files = {**LIBRARIES, 'lib.raml': BAD_LIB, 'other.raml': LIB + 'uses:\n  again: lib.raml\n'}
        root = workspace({'api.raml': API + 'uses:\n  lib: lib.raml\n', **files})
        _raml, alone = workspace.lenient(root / 'api.raml', BOTH)
        workspace({'api.raml': API + 'uses:\n  lib: lib.raml\n  other: other.raml\n'})
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        again = raml.entry_point.uses['other'].link.uses['again']
        assert again.link is raml.entry_point.uses['lib'].link
        assert raml.broken[again.id].head.message == 'parse uses library'
        assert len(list(error.chains())) == len(list(alone.chains()))

    def test_a_library_that_cannot_be_loaded_is_unlinked_and_marked(self, workspace):
        raml, _error = workspace.lenient_document(API + 'uses:\n  lib: absent.raml\n', BOTH)
        link = raml.entry_point.uses['lib']
        assert link.link is None
        assert raml.broken[link.id].head.message == 'parse uses library'


class TestTheModelSaysHowFarItGot:
    """docs/13 § 1 — `completed` and `stopped_at`.

    "The API has no endpoints" and "endpoints were never built" both read as
    `endpoints == {}`; only the stage tells them apart.
    """

    ORDER = (
        Stage.DECODED,
        Stage.ENDPOINTS,
        Stage.SECURITY,
        Stage.RESOLVED,
        Stage.ANNOTATIONS,
        Stage.UNWRAPPED,
        Stage.VALIDATED,
    )

    #: One mistake that fails at each stage.
    FAILURES = {  # noqa: RUF012 - a table, read once per parametrize
        Stage.DECODED: 'types:\n  Bad:\n    minLength: two\n',
        Stage.ENDPOINTS: '/r:\n  get:\n    is: [nosuch]\n',
        Stage.SECURITY: 'securedBy: [nope]\n',
        Stage.RESOLVED: 'types:\n  Bad: NoSuch\n',
        Stage.ANNOTATIONS: '(nosuch): x\n',
        Stage.UNWRAPPED: 'types:\n  N: integer\n  C:\n    type: [string, N]\n',
        Stage.VALIDATED: 'types:\n  E:\n    type: integer\n    example: nope\n',
    }

    @pytest.mark.parametrize('stage', list(FAILURES), ids=[stage.value for stage in FAILURES])
    def test_a_failure_names_its_stage_and_everything_before_it(self, workspace, stage):
        raml, error = workspace.lenient_document(API + self.FAILURES[stage], BOTH)
        assert error is not None
        assert raml.stopped_at is stage
        assert raml.completed == list(self.ORDER[: self.ORDER.index(stage)])

    @pytest.mark.parametrize(
        ('options', 'expected'),
        [
            (ParseOptions(), ORDER[:5]),
            (ParseOptions(validate=True), (*ORDER[:5], Stage.VALIDATED)),
            (BOTH, ORDER),
        ],
        ids=['neither', 'validate only', 'both'],
    )
    def test_a_clean_parse_lists_the_stages_it_ran(self, workspace, options, expected):
        """An optional stage that did not run is absent, not implied by a later one."""
        raml, error = workspace.lenient_document(API + 'types:\n  T: string\n', options)
        assert error is None
        assert raml.stopped_at is None
        assert raml.completed == list(expected)


class TestOneBadDeclarationKeepsItsSiblings:
    """docs/11 § 2 — a declaration map is the fragment's own, filled before
    its errors are raised. It used to be assigned only on success, so one bad
    facet left the fragment declaring nothing, while the registry held the rest.
    """

    #: The map's key, the fragment attribute, a failing entry and a good one.
    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'types': ('types', 'Bad:\n    minLength: two', 'Good: string'),
        'annotationTypes': ('annotation_types', 'bad:\n    minLength: two', 'good: string'),
        'traits': ('traits', 'bad: 5', 'good:\n    description: d'),
        'resourceTypes': ('resource_types', 'bad: 5', 'good:\n    description: d'),
        'securitySchemes': ('security_schemes', 'bad:\n    type: Nope', 'good:\n    type: Basic Authentication'),
    }

    @pytest.mark.parametrize('header', [API, LIB], ids=['api', 'library'])
    @pytest.mark.parametrize(('key', 'case'), CASES.items(), ids=list(CASES))
    def test_the_good_sibling_is_declared(self, workspace, header, key, case):
        attribute, bad, good = case
        raml, error = workspace.lenient_document(header + f'{key}:\n  {bad}\n  {good}\n', BOTH)
        assert error is not None
        declared = getattr(raml.fragments[raml.location], attribute)
        name = good.split(':')[0]
        assert name in declared
        assert declared[name].id not in raml.broken

    def test_the_fragment_and_the_registry_agree(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  Good: string\n  Bad:\n    minLength: two\n  User: object\n', BOTH
        )
        assert error is not None
        assert list(raml.entry_point.types) == list(raml.types_in(raml.location)) == ['Good', 'Bad', 'User']

    def test_the_error_is_the_strict_one(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  Bad:\n    minLength: two\n  Good: string\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml', BOTH)
        _, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert messages(error) == messages(caught.value)


class TestABrokenTypeDeclarationIsKeptAndMarked:
    """docs/13 § 1 — a type declaration is attached before its content is
    decoded, so one that fails stays in the model, marked in `Raml.broken`.
    """

    #: A failure after the kind is settled, one before it, and one inside a
    #: property, with the kind each declaration is left holding.
    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'kind facet': ('Bad:\n    type: string\n    minLength: two', 'StringShape'),
        'common facet': ('Bad:\n    displayName: {a: 1}', 'UnknownShape'),
        'nested property': ('Bad:\n    properties:\n      p:\n        minLength: two', 'ObjectShape'),
    }

    @pytest.mark.parametrize('header', [API, LIB], ids=['api', 'library'])
    @pytest.mark.parametrize(('body', 'kind'), CASES.values(), ids=list(CASES))
    def test_it_is_declared_registered_and_marked(self, workspace, header, body, kind):
        raml, error = workspace.lenient_document(header + f'types:\n  Good: string\n  {body}\n', BOTH)
        assert error is not None
        bad = raml.fragments[raml.location].types['Bad']
        assert raml.types_in(raml.location)['Bad'] is bad
        assert bad in raml.shapes
        assert type(bad.shape).__name__ == kind, 'never without a kind'
        assert set(raml.broken) == {bad.id}

    def test_the_mark_holds_the_declarations_own_failure(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  Bad:\n    minLength: two\n  Worse:\n    maxLength: x\n', BOTH
        )
        assert error is not None
        types = raml.entry_point.types
        assert [[chain[-1].message for chain in raml.broken[types[name].id].chains()] for name in types] == [
            ['expected an integer value'],
            ['expected an integer value'],
        ]
        assert [raml.broken[types[name].id].head.position.line for name in types] == [5, 7]

    def test_an_annotation_type_is_kept_the_same_way(self, workspace):
        raml, error = workspace.lenient_document(API + 'annotationTypes:\n  bad:\n    minLength: two\n', BOTH)
        assert error is not None
        bad = raml.entry_point.annotation_types['bad']
        assert bad.is_annotation_type
        assert set(raml.broken) == {bad.id}

    def test_a_valid_parse_marks_nothing(self, workspace):
        raml, error = workspace.lenient_document(API + 'types:\n  Good: string\n', BOTH)
        assert error is None
        assert raml.broken == {}


class TestADeclarationKeepsTheChildrenThatBuilt:
    """docs/13 § 1 — the kind is attached before its `properties`, `items` or
    `anyOf` are built into it, so one failed child leaves its siblings.

    It used to cost the whole kind: the declaration became an `UnknownShape`
    while the siblings it had built stayed registered, and when P7 built them
    they were resolved and marked inside nothing.
    """

    def test_a_failed_property_leaves_its_siblings(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  T:\n    properties:\n      a: string\n      b: {minLength: x}\n      c: string\n', BOTH
        )
        assert error is not None
        t = raml.entry_point.types['T']
        assert type(t.shape).__name__ == 'ObjectShape'
        assert list(t.shape.properties) == ['a', 'c']
        assert set(raml.broken) == {t.id}

    def test_every_failed_property_is_reported(self, workspace):
        _, error = workspace.lenient_document(
            API + 'types:\n  T:\n    properties:\n      a: {minLength: x}\n      b: {maxLength: y}\n', BOTH
        )
        assert error is not None
        assert [chain[-1].position.line for chain in error.chains()] == [6, 7]

    def test_a_failed_union_member_leaves_the_others(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  U:\n    type: union\n    anyOf: [string, {minLength: x}, integer]\n', BOTH
        )
        assert error is not None
        u = raml.entry_point.types['U']
        assert [member.type for member in u.shape.any_of] == ['string', 'integer']

    def test_a_property_that_p7_fails_is_inside_its_declaration(self, workspace):
        # P7 builds these properties, because `type: B` left the kind unknown
        # until then; `c: 11` fails the build after `b: Nope` was queued.
        raml, error = workspace.lenient_document(
            API
            + 'types:\n  B: object\n  T:\n    type: B\n    properties:\n      a: string\n'
            + '      b: Nope\n      c: 11\n',
            BOTH,
        )
        assert error is not None
        assert raml.stopped_at is Stage.RESOLVED
        t = raml.entry_point.types['T']
        assert list(t.shape.properties) == ['a', 'b']
        assert set(raml.broken) == {t.id, t.shape.properties['b'].base.id}


class TestABrokenDefinitionIsKeptAndMarked:
    """docs/13 § 1 — traits, resource types and security schemes are attached
    before their body is read, like type declarations.
    """

    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'trait': ('traits', 'traits:\n  bad: 5\n  good:\n    description: d\n'),
        'resource type': ('resource_types', 'resourceTypes:\n  bad:\n    nope: {}\n  good:\n    get:\n'),
        'security scheme': (
            'security_schemes',
            'securitySchemes:\n  bad:\n    type: Nope\n  good:\n    type: Basic Authentication\n',
        ),
    }

    @pytest.mark.parametrize('header', [API, LIB], ids=['api', 'library'])
    @pytest.mark.parametrize(('attribute', 'body'), CASES.values(), ids=list(CASES))
    def test_it_is_declared_and_marked(self, workspace, header, attribute, body):
        raml, error = workspace.lenient_document(header + body, BOTH)
        assert error is not None
        declared = getattr(raml.fragments[raml.location], attribute)
        assert list(declared) == ['bad', 'good']
        assert set(raml.broken) == {declared['bad'].id}

    def test_a_bad_described_by_response_marks_the_path_to_it(self, workspace):
        """Every mark names an entity in the model: the response, the
        description holding it and the scheme holding that.
        """
        raml, _ = workspace.lenient_document(
            API + 'securitySchemes:\n  s:\n    type: Basic Authentication\n'
            '    describedBy:\n      responses:\n        401:\n          foo: 1\n        403:\n',
            BOTH,
        )
        scheme = raml.entry_point.security_schemes['s']
        responses = scheme.described_by.responses
        assert list(responses) == ['401', '403']
        assert set(raml.broken) == {scheme.id, scheme.described_by.id, responses['401'].id}

    def test_an_include_that_fails_marks_the_definition_naming_it(self, workspace):
        raml, error = workspace.lenient_document(API + 'traits:\n  t: !include missing.raml\n', BOTH)
        assert error is not None
        trait = raml.entry_point.traits['t']
        assert trait.link is None
        assert set(raml.broken) == {trait.id}

    def test_a_definition_fragment_that_fails_keeps_its_name(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'securitySchemes:\n  s: !include scheme.raml\n',
                'scheme.raml': '#%RAML 1.0 SecurityScheme\ntype: Nope\n',
            }
        )
        raml, _ = workspace.lenient(root / 'api.raml', BOTH)
        fragment = raml.fragments[raml.entry_point.security_schemes['s'].link_uri]
        assert fragment.definition.name == 'scheme.raml', 'named after the file, as on success'
        assert fragment.definition.id in raml.broken


class TestAFragmentKeepsWhatBuilt:
    """docs/13 § 1: an included DataType or NamedExample fragment that fails
    keeps what built, as a declaration map does."""

    def test_a_data_type_fragment_root_is_kept_and_marked(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'types:\n  T: !include t.raml\n',
                't.raml': '#%RAML 1.0 DataType\nproperties:\n  a: string\n  b:\n    minLength: two\n',
            }
        )
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        fragment = raml.fragments[path_to_file_uri(root / 't.raml')]
        assert fragment.shape is not None
        assert fragment.shape.type == 'object'
        assert list(fragment.shape.shape.properties) == ['a']
        assert fragment.shape.id in raml.broken
        assert fragment.shape in raml.typedefs_in(fragment.location)

    def test_a_bad_named_example_does_not_hide_the_ones_after_it(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'types:\n  T:\n    type: integer\n    examples: !include ex.raml\n',
                'ex.raml': '#%RAML 1.0 NamedExample\none: 1\ntwo: !include absent.raml\nthree: 3\n',
            }
        )
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert list(raml.fragments[path_to_file_uri(root / 'ex.raml')].examples) == ['one', 'three']


class TestASecondIncludeOfAFailedFragmentIsMarked:
    """docs/13 § 1: an `!include` that finds a failed fragment in the cache is
    marked, as the first one was, and reports nothing again."""

    BAD_TYPE = '#%RAML 1.0 DataType\nproperties:\n  a: string\n  b:\n    minLength: two\n'

    def test_a_data_type_fragment(self, workspace):
        files = {'api.raml': API + 'types:\n  T: !include t.raml\n', 't.raml': self.BAD_TYPE}
        root = workspace(files)
        _raml, alone = workspace.lenient(root / 'api.raml', BOTH)
        workspace({'api.raml': API + 'types:\n  T: !include t.raml\n  U: !include t.raml\n'})
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        types = raml.entry_point.types
        assert types['T'].id in raml.broken
        assert raml.broken[types['U'].id].head.message == 'include'
        assert raml.broken[types['U'].id].head.info == {'path': path_to_file_uri(root / 't.raml')}
        assert len(list(error.chains())) == len(list(alone.chains()))

    def test_a_named_example_fragment(self, workspace):
        examples = '#%RAML 1.0 NamedExample\none: 1\ntwo: !include absent.raml\n'
        declaration = '    type: integer\n    examples: !include ex.raml\n'
        root = workspace(
            {'api.raml': API + 'types:\n  T:\n' + declaration + '  U:\n' + declaration, 'ex.raml': examples}
        )
        raml, _error = workspace.lenient(root / 'api.raml', BOTH)
        types = raml.entry_point.types
        assert types['T'].id in raml.broken
        assert raml.broken[types['U'].id].head.message == 'include'

    def test_a_definition_fragment(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'traits:\n  a: !include t.raml\n  b: !include t.raml\n',
                't.raml': '#%RAML 1.0 Trait\nusage: [1, 2]\n',
            }
        )
        raml, _error = workspace.lenient(root / 'api.raml', BOTH)
        traits = raml.entry_point.traits
        assert traits['a'].id in raml.broken
        assert raml.broken[traits['b'].id].head.message == 'include'

    @pytest.mark.parametrize(
        'failing',
        [
            'types:\n  D: !include d.raml\n  Z:\n    minLength: two\n',
            'uses:\n  x: absent.raml\ntypes:\n  D: !include d.raml\n',
        ],
        ids=['in-its-body', 'in-its-uses'],
    )
    def test_a_uses_entry_that_meets_its_library_still_being_decoded(self, workspace, failing):
        """docs/04 § 6: `d.raml` imports `a.raml` while `a.raml`'s body is
        being decoded; `a.raml` fails afterwards, in its body or its own
        `uses:`, and the entry is marked."""
        root = workspace(
            {
                'api.raml': API + 'uses:\n  a: a.raml\n',
                'a.raml': LIB + failing,
                'd.raml': '#%RAML 1.0 DataType\nuses:\n  a: a.raml\ntype: string\n',
            }
        )
        raml, _error = workspace.lenient(root / 'api.raml', BOTH)
        back = raml.fragments[path_to_file_uri(root / 'd.raml')].uses['a']
        assert back.link is raml.entry_point.uses['a'].link
        assert raml.broken[back.id].head.message == 'parse uses library'


class TestATemplateThatFailsToApplyMarksWhatLacksIt:
    """docs/13 § 1 — a trait or resource type fails in the source IR, before
    the model entity exists. The failure is noted on the IR, and stage 2 marks
    the operation or resource it becomes: that is what lacks the contribution.
    """

    @staticmethod
    def marked(workspace, body: str):
        raml, error = workspace.lenient_document(API + body, BOTH)
        assert error is not None
        names = {}
        for uri, endpoint in raml.endpoints.items():
            names[endpoint.id] = uri
            names.update({operation.id: f'{uri} {method}' for method, operation in endpoint.operations.items()})
        return raml, {names[entity] for entity in raml.broken}

    def test_an_unknown_trait(self, workspace):
        raml, marked = self.marked(workspace, '/a:\n  get:\n    is: [nosuch]\n  post:\n/b:\n  get:\n')
        assert marked == {'/a', '/a get'}
        (ref,) = raml.endpoints['/a'].operations['get'].traits
        assert ref.resolved is None

    def test_a_trait_that_resolves_but_fails_to_merge(self, workspace):
        _, marked = self.marked(
            workspace, 'traits:\n  paged:\n    description: <<what>>\n/a:\n  get:\n    is: [paged]\n  post:\n'
        )
        assert marked == {'/a', '/a get'}

    def test_every_trait_that_failed_is_on_the_mark(self, workspace):
        raml, _ = self.marked(
            workspace,
            'traits:\n  one:\n    description: <<a>>\n  two:\n    description: <<b>>\n'
            '/a:\n  get:\n    is: [one, two]\n',
        )
        mark = raml.broken[raml.endpoints['/a'].operations['get'].id]
        assert [chain[0].info for chain in mark.chains()] == [{'trait': 'one'}, {'trait': 'two'}]

    def test_the_failure_marks_every_enclosing_resource(self, workspace):
        # As a failure in content does: it passes through each of them.
        _, marked = self.marked(workspace, '/a:\n  /b:\n    get:\n      is: [nosuch]\n  /c:\n    get:\n')
        assert marked == {'/a', '/a/b', '/a/b get'}

    def test_a_skipped_reference_that_names_nothing_marks_its_operation(self, workspace):
        # The method's own `nosuch` is applied; the resource type's, written on
        # the same method, is skipped by the name rule, then bound and failed.
        raml, _ = self.marked(
            workspace,
            'resourceTypes:\n  rt:\n    get:\n      is: [nosuch]\n/a:\n  type: rt\n  get:\n    is: [nosuch]\n',
        )
        mark = raml.broken[raml.endpoints['/a'].operations['get'].id]
        assert [chain[0].position.line for chain in mark.chains()] == [10, 6]

    def test_an_unknown_resource_type_marks_the_resource(self, workspace):
        _, marked = self.marked(workspace, '/a:\n  type: nosuch\n  get:\n')
        assert marked == {'/a'}

    def test_a_failure_in_what_did_merge_joins_the_mark(self, workspace):
        # `nosuch` fails to apply; `bad` applies, and its `minLength` then fails
        # when the merged operation is decoded. The mark holds both.
        raml, _ = self.marked(
            workspace,
            'traits:\n  bad:\n    queryParameters:\n      q:\n        minLength: x\n'
            '/a:\n  get:\n    is: [nosuch, bad]\n',
        )
        mark = raml.broken[raml.endpoints['/a'].operations['get'].id]
        assert [chain[0].message for chain in mark.chains()] == ['apply trait', 'expected an integer value']


class TestALaterStageMarksWhatItCouldNotSettle:
    """docs/13 § 1 — P5, P7, P8 and P9 fail on entities already in the model;
    each such entity, and each one the failure passed through, is marked.
    """

    @staticmethod
    def parse(workspace, body: str):
        raml, error = workspace.lenient_document(API + body, BOTH)
        assert error is not None
        return raml

    def test_an_unknown_security_scheme(self, workspace):
        raml = self.parse(workspace, 'securedBy: [nope]\n/a:\n  get:\n')
        (scheme,) = raml.global_secured_by
        assert scheme.definition is None
        assert set(raml.broken) == {scheme.id}
        # Bound once per level that inherits it, and marked once.
        assert len(list(raml.broken[scheme.id].chains())) == 1

    def test_an_unknown_type_name_stays_unknown(self, workspace):
        raml = self.parse(workspace, 'types:\n  Bad: NoSuch\n  Good: string\n')
        types = raml.entry_point.types
        assert type(types['Bad'].shape).__name__ == 'UnknownShape'
        assert set(raml.broken) == {types['Bad'].id}

    def test_a_referrer_the_failure_passed_through_is_marked_too(self, workspace):
        raml = self.parse(workspace, 'types:\n  A: B\n  B: NoSuch\n')
        types = raml.entry_point.types
        assert set(raml.broken) == {types['A'].id, types['B'].id}

    def test_a_bad_property_type_marks_the_property_not_its_holder(self, workspace):
        """The worklist resolves the property's own shape; the failure never
        passes through the object that holds it.
        """
        raml = self.parse(workspace, 'types:\n  User:\n    properties:\n      p: NoSuch\n')
        user = raml.entry_point.types['User']
        assert set(raml.broken) == {user.shape.properties['p'].base.id}

    def test_an_unknown_annotation(self, workspace):
        raml = self.parse(workspace, '(nosuch): x\n')
        (extension,) = raml.domain_extensions
        assert extension.defined_by is None
        assert set(raml.broken) == {extension.id}

    def test_a_failed_merge_marks_it_and_every_shape_enclosing_it(self, workspace):
        raml = self.parse(
            workspace, 'types:\n  N: integer\n  Outer:\n    properties:\n      c:\n        type: [string, N]\n'
        )
        outer = raml.entry_point.types['Outer']
        assert set(raml.broken) == {outer.id, outer.shape.properties['c'].base.id}

    def test_a_scheme_whose_scopes_failed_keeps_its_definition(self, workspace):
        raml = self.parse(
            workspace,
            'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
            'securedBy: [basic: {scopes: [read]}]\n/a:\n  get:\n',
        )
        (scheme,) = raml.global_secured_by
        assert scheme.definition is not None
        assert raml.broken[scheme.id].head.message == 'scopes override is only valid for OAuth 2.0 schemes'


class TestACheckThatBuildsNothingMarksNothing:
    """docs/13 § 1: the discriminator declaration check and P10 name whole
    entities; only the returned error reports them."""

    def test_the_discriminator_declaration_check(self, workspace):
        raml, error = workspace.lenient_document(
            API + 'types:\n  T:\n    properties:\n      p:\n'
            '        properties:\n          kind: string\n        discriminator: kind\n',
            BOTH,
        )
        assert error.head.message == 'discriminator on an inline type declaration'
        assert raml.stopped_at is Stage.RESOLVED
        assert raml.broken == {}
        # Stopped at RESOLVED, but P7 finished: every shape has its kind (I5).
        assert not [shape for shape in raml.shapes if type(shape.shape).__name__ == 'UnknownShape']

    def test_validation(self, workspace):
        raml, error = workspace.lenient_document(API + 'types:\n  T:\n    type: integer\n    example: nope\n', BOTH)
        assert error is not None
        assert raml.stopped_at is Stage.VALIDATED
        assert raml.broken == {}


class TestTheEndpointTreeKeepsWhatFailed:
    """docs/13 § 1 — responses, operations and resources are attached before
    their content is decoded. A failure marks the entity it happened in and
    every entity it passed through; everything beside it stays unmarked.
    """

    DOCUMENT = (
        '/a:\n  get:\n  /b:\n    get:\n    post:\n      responses:\n'
        '        200:\n          foo: 1\n        201:\n  /c:\n    get:\n/d:\n  get:\n'
    )

    @staticmethod
    def marked(raml) -> set[str]:
        names = set()
        for uri, endpoint in raml.endpoints.items():
            if endpoint.id in raml.broken:
                names.add(uri)
            for method, operation in endpoint.operations.items():
                if operation.id in raml.broken:
                    names.add(f'{uri} {method}')
                names.update(
                    f'{uri} {method} {code}'
                    for code, response in operation.responses.items()
                    if response.id in raml.broken
                )
        return names

    def test_a_bad_response_key_keeps_the_whole_tree(self, workspace):
        raml, error = workspace.lenient_document(API + self.DOCUMENT, BOTH)
        assert error is not None
        assert list(raml.endpoints) == ['/a', '/a/b', '/a/c', '/d']
        assert list(raml.endpoints['/a/b'].operations) == ['get', 'post']
        assert list(raml.endpoints['/a/b'].operations['post'].responses) == ['200', '201']

    def test_it_marks_the_response_and_everything_enclosing_it(self, workspace):
        raml, _ = workspace.lenient_document(API + self.DOCUMENT, BOTH)
        assert self.marked(raml) == {'/a', '/a/b', '/a/b post', '/a/b post 200'}

    def test_a_bad_operation_key_marks_the_operation_and_its_resource(self, workspace):
        raml, _ = workspace.lenient_document(API + '/a:\n  get:\n    foo: 1\n  post:\n', BOTH)
        assert list(raml.endpoints['/a'].operations) == ['get', 'post']
        assert self.marked(raml) == {'/a', '/a get'}

    def test_a_bad_resource_key_keeps_its_children(self, workspace):
        raml, _ = workspace.lenient_document(API + '/a:\n  foo: 1\n  get:\n  /b:\n    get:\n', BOTH)
        assert list(raml.endpoints) == ['/a', '/a/b']
        assert list(raml.endpoints['/a'].operations) == ['get']
        assert self.marked(raml) == {'/a'}

    def test_the_error_is_the_strict_one(self, workspace):
        root = workspace({'api.raml': API + self.DOCUMENT})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml', BOTH)
        _, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert messages(error) == messages(caught.value)

    def test_a_kept_resource_still_gets_its_uri_parameters_checked(self, workspace):
        """P6 runs over a kept resource: an unused parameter is its own mistake."""
        _, error = workspace.lenient_document(API + '/a/{id}:\n  uriParameters:\n    other: string\n  foo: 1\n', BOTH)
        assert error is not None
        assert [chain[-1].message for chain in error.chains()] == ['unknown field', 'uri parameter is not used']

    def test_a_nested_duplicate_uri_is_kept_and_marked_with_what_encloses_it(self, workspace):
        """docs/13 § 1: the loser stays in its parent's `endpoints`, outside
        `Raml.endpoints`, so it and its parent are marked."""
        raml, _ = workspace.lenient_document(API + '/users/foo:\n  get:\n/users:\n  /foo:\n    get:\n', BOTH)
        winner, users = raml.endpoints['/users/foo'], raml.endpoints['/users']
        loser = users.endpoints['/foo']
        assert loser is not winner
        assert raml.broken[loser.id].head.message == 'duplicate resource URI'
        assert raml.broken[loser.id].head.info == {'uri': '/users/foo'}
        assert users.id in raml.broken
        assert winner.id not in raml.broken

    def test_a_top_level_duplicate_uri_is_absent_with_its_subtree_and_unmarked(self, workspace):
        raml, _ = workspace.lenient_document(
            API + '/users:\n  /foo:\n    get:\n/users/foo:\n  post:\n  /bar:\n    get:\n', BOTH
        )
        assert list(raml.endpoints['/users/foo'].operations) == ['get']
        assert list(raml.endpoints) == ['/users', '/users/foo']
        assert raml.broken == {}

    def test_a_top_level_duplicate_uri_still_has_its_subtree_checked(self, workspace):
        """docs/11 § 2: errors accumulate. The absent subtree is not
        registered, but its own mistakes are reported, strict or lenient."""
        root = workspace(
            {
                'api.raml': API + '/users:\n  /foo:\n    get:\n'
                '/users/foo:\n  uriParameters:\n    nope: string\n'
                '  /{x}:\n    uriParameters:\n      y: string\n    get:\n'
            }
        )
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml', BOTH)
        reported = [(chain[-1].message, chain[-1].info) for chain in caught.value.chains()]
        assert reported == [
            ('duplicate resource URI', {'uri': '/users/foo'}),
            ('uri parameter is not used', {'parameter': 'nope', 'uri': '/users/foo'}),
            ('uri parameter is not used', {'parameter': 'y', 'uri': '/{x}'}),
        ]
        raml, _ = workspace.lenient(root / 'api.raml', BOTH)
        assert list(raml.endpoints) == ['/users', '/users/foo']
        assert raml.broken == {}


class TestItStopsWhereStrictStops:
    """The design decision, pinned with the measurement that produced it.

    An earlier draft continued to the next pass after one failed, to collect
    the diagnostics a strict parse never reaches. It does not work, because the
    passes consume each other's output: a pass walking state an earlier one
    already reported as broken re-derives the same fault rather than finding a
    new one. One missing library used by twenty types produced **41**
    diagnostics instead of one.

    So the error is exactly what a strict parse would have raised, and the
    model is the difference. Recovering the genuinely independent diagnostics
    means skipping the broken *entities* inside P9 and P10, not the passes;
    `docs/research/partial-models.md` § 7 proposes it, gated on measurement.
    """

    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'dangling type name': 'types:\n  Bad: NoSuchType\n',
        'dangling used by five': 'types:\n  Bad: NoSuchType\n'
        + ''.join(f'  D{index}:\n    properties:\n      p: Bad\n' for index in range(5)),
        'missing library': 'uses:\n  lib: absent.raml\ntypes:\n'
        + ''.join(f'  D{index}:\n    properties:\n      p: lib.Thing\n' for index in range(5)),
    }

    @pytest.mark.parametrize('body', CASES.values(), ids=list(CASES))
    def test_the_error_matches_a_strict_parse_exactly(self, workspace, body):
        root = workspace({'api.raml': API + body})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml', BOTH)
        _, lenient = workspace.lenient(root / 'api.raml', BOTH)

        assert lenient is not None
        assert len(list(lenient.chains())) == len(list(caught.value.chains()))
        assert messages(lenient) == messages(caught.value)

    def test_one_missing_library_is_one_diagnostic(self, workspace):
        """The case that settled it, at the size that made it obvious."""
        body = 'uses:\n  lib: absent.raml\ntypes:\n' + ''.join(
            f'  D{index}:\n    properties:\n      p: lib.Thing\n' for index in range(20)
        )
        _, error = workspace.lenient_document(API + body, BOTH)
        assert error is not None
        assert len(list(error.chains())) == 1


class TestOneMistakeOneChain:
    """docs/11 § 2 — one mistake reaches a pass once per copy of the construct
    holding it, and every copy reports the same chain at the same place.
    """

    CASES = {  # noqa: RUF012 - a table, read once per parametrize
        'trait applied three times': (
            (
                'traits:\n  q:\n    queryParameters:\n      a: NoSuch\n'
                '/a:\n  get:\n    is: [q]\n  post:\n    is: [q]\n/b:\n  get:\n    is: [q]\n'
            ),
            'resolve shape',
        ),
        'resource type applied twice': (
            (
                'resourceTypes:\n  c:\n    get:\n      body:\n        application/json: NoSuch\n'
                '/a:\n  type: c\n/b:\n  type: c\n'
            ),
            'resolve shape',
        ),
        'root securedBy inherited three levels': (
            'securedBy: [nope]\n/a:\n  /b:\n    get:\n',
            'get security scheme definition',
        ),
    }

    @pytest.mark.parametrize(('body', 'message'), CASES.values(), ids=list(CASES))
    def test_in_a_strict_parse(self, workspace, body, message):
        with pytest.raises(RamlError) as caught:
            workspace.document(API + body, BOTH)
        assert [chain[0].message for chain in caught.value.chains()] == [message]

    @pytest.mark.parametrize(('body', 'message'), CASES.values(), ids=list(CASES))
    def test_in_a_lenient_parse(self, workspace, body, message):
        _, error = workspace.lenient_document(API + body, BOTH)
        assert error is not None
        assert [chain[0].message for chain in error.chains()] == [message]

    def test_two_uses_of_one_missing_name_are_two_mistakes(self, workspace):
        """Written twice, at two places: both are reported."""
        _, error = workspace.lenient_document(API + 'types:\n  A: NoSuch\n  B: NoSuch\n', BOTH)
        assert error is not None
        assert len(list(error.chains())) == 2


class TestStillFatal:
    """The four cases docs/13 § 1 keeps fail-fast: nothing to hand back."""

    def test_an_unreadable_entry_file_raises(self, workspace):
        root = workspace({'api.raml': API})
        with pytest.raises(RamlError) as caught:
            workspace.lenient(root / 'absent.raml')
        assert 'load resource' in messages(caught.value)

    def test_a_missing_header_raises(self, workspace):
        with pytest.raises(RamlError) as caught:
            workspace.lenient_document('title: no header here\n')
        assert 'unknown fragment kind' in messages(caught.value)

    def test_a_non_mapping_root_raises(self, workspace):
        with pytest.raises(RamlError) as caught:
            workspace.lenient_document(API.splitlines()[0] + '\n- a\n- b\n')
        assert 'must be map' in messages(caught.value)

    def test_an_overlay_whose_master_cannot_be_loaded_raises(self, workspace):
        # With no root API there is no model to hand back (docs/19 § 2).
        with pytest.raises(RamlError) as caught:
            workspace.lenient_document('#%RAML 1.0 Overlay\nextends: base.raml\ntitle: T\n')
        assert caught.value.head.message == 'resolve extends'

    def test_the_same_problem_in_an_included_file_is_not_fatal(self, workspace):
        """`_FATAL` matches the *head* of the error, and that is the point.

        A library whose root is a sequence is one bad file; the entry document
        is still worth handing back. Matching anywhere in the chain would make
        every included file's syntax error abandon the parse.
        """
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\ntypes:\n  T: string\n',
                'lib.raml': LIB + '- a\n- b\n',
            }
        )
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        assert 'must be map' in messages(error)
        assert raml.entry_point.types['T'].type == 'string'

    INCLUDED = {  # noqa: RUF012 - a table, read once per parametrize
        'missing': ({}, 'load resource'),
        'root not a mapping': ({'t.raml': '#%RAML 1.0 DataType\n- a\n'}, 'must be map'),
        'wrong fragment kind': ({'t.raml': '#%RAML 1.0 Trait\ndescription: x\n'}, 'unexpected fragment kind'),
    }

    @pytest.mark.parametrize(('files', 'message'), INCLUDED.values(), ids=list(INCLUDED))
    def test_a_type_include_that_fails_is_not_fatal(self, workspace, files, message):
        """A fragment `!include`d in a type position fails under an `include`
        frame at the include (docs/11 § 3), whose key is not fatal. The
        message inside is a fatal one; the frame outside it is what says it is
        not the entry's.
        """
        root = workspace({'api.raml': API + 'types:\n  A: !include t.raml\n  T: string\n', **files})
        raml, error = workspace.lenient(root / 'api.raml', BOTH)
        assert error is not None
        outer, *inner = error.frames()
        assert (outer.message, outer.location.rsplit('/', 1)[1]) == ('include', 'api.raml')
        # At the include, so an editor can show a file that failed to load.
        assert outer.position is not None
        assert outer.position.is_known
        assert message in [frame.message for frame in inner]
        assert raml.entry_point is not None

    def test_an_include_outside_the_workspace_is_not_fatal(self, tmp_path):
        root = write_files(
            tmp_path,
            {
                'sub/api.raml': API + 'types:\n  A: !include ../t.raml\n  T: string\n',
                't.raml': '#%RAML 1.0 DataType\ntype: string\n',
            },
        )
        raml, error = parse_lenient(root / 'sub' / 'api.raml', BOTH)
        assert error is not None
        assert [frame.message for frame in error.frames()][:2] == ['include', 'load resource']
        assert raml.entry_point is not None
