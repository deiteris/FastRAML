"""`check()` — is a *declaration* self-consistent?

docs/10-validation.md section 2, one test per row of its table, plus the
discriminator rules of docs/05 section 9 which are checked here rather than at
decode time because the property named may be inherited.

This asks nothing about data. `tests/unit/test_validate.py` is the other half.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path

API = '#%RAML 1.0\ntitle: T\n'


def parse(workspace, body: str, **files: str):
    """Parse `types:\n<body>` and return the error, or `None` if it validated."""
    root = workspace({'api.raml': API + 'types:\n' + body, **files})
    try:
        parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
    except RamlError as err:
        return err
    return None


def traces(error: RamlError) -> list:
    return [chain[-1] for chain in error.chains()]


def messages(error: RamlError) -> set[str]:
    return {trace.message for trace in traces(error)}


class TestString:
    def test_min_length_may_not_exceed_max_length(self, workspace):
        error = parse(workspace, '  T:\n    type: string\n    minLength: 10\n    maxLength: 5\n')
        assert error is not None
        assert 'minLength exceeds maxLength' in messages(error)

    def test_equal_bounds_are_fine(self, workspace):
        assert parse(workspace, '  T:\n    type: string\n    minLength: 5\n    maxLength: 5\n') is None

    def test_one_bound_alone_is_fine(self, workspace):
        assert parse(workspace, '  T:\n    type: string\n    minLength: 10\n') is None


class TestNonNegativeBounds:
    """Spec, per facet: "Value MUST be equal to or greater than 0."

    A negative bound is worse than unsatisfiable: `minLength: -2` accepts
    everything while reading as though it constrains something.
    """

    @pytest.mark.parametrize(
        ('body', 'facet'),
        [
            ('  T:\n    type: string\n    minLength: -2\n', 'minLength'),
            ('  T:\n    type: string\n    maxLength: -14\n', 'maxLength'),
            ('  T:\n    type: file\n    maxLength: -1\n', 'maxLength'),
            ('  T:\n    type: array\n    items: string\n    minItems: -1\n', 'minItems'),
            ('  T:\n    type: array\n    items: string\n    maxItems: -1\n', 'maxItems'),
            ('  T:\n    type: object\n    minProperties: -1\n', 'minProperties'),
            ('  T:\n    type: object\n    maxProperties: -1\n', 'maxProperties'),
        ],
        ids=['minLength', 'maxLength', 'file-maxLength', 'minItems', 'maxItems', 'minProperties', 'maxProperties'],
    )
    def test_a_negative_bound_is_rejected(self, workspace, body, facet):
        error = parse(workspace, body)
        assert error is not None
        assert 'facet must not be negative' in messages(error)
        assert traces(error)[0].info['facet'] == facet

    def test_zero_is_allowed(self, workspace):
        assert parse(workspace, '  T:\n    type: string\n    minLength: 0\n    maxLength: 0\n') is None

    def test_both_negative_bounds_are_reported(self, workspace):
        # `-1 > -2` is also a disordered pair, so three diagnostics come back;
        # what this pins is that neither negative bound is swallowed.
        error = parse(workspace, '  T:\n    type: string\n    minLength: -1\n    maxLength: -2\n')
        assert error is not None
        negative = [t.info['facet'] for t in traces(error) if t.message == 'facet must not be negative']
        assert sorted(negative) == ['maxLength', 'minLength']


class TestNumeric:
    @pytest.mark.parametrize('kind', ['number', 'integer'])
    def test_minimum_may_not_exceed_maximum(self, workspace, kind):
        error = parse(workspace, f'  T:\n    type: {kind}\n    minimum: 10\n    maximum: 5\n')
        assert error is not None
        assert 'minimum exceeds maximum' in messages(error)

    @pytest.mark.parametrize('kind', ['number', 'integer'])
    def test_multiple_of_zero_is_rejected(self, workspace, kind):
        error = parse(workspace, f'  T:\n    type: {kind}\n    multipleOf: 0\n')
        assert error is not None
        assert 'multipleOf must not be zero' in messages(error)

    @pytest.mark.parametrize('declared', ['float', 'double'])
    def test_a_number_accepts_its_own_formats(self, workspace, declared):
        assert parse(workspace, f'  T:\n    type: number\n    format: {declared}\n') is None

    @pytest.mark.parametrize('declared', ['int8', 'int16', 'int32', 'int', 'int64', 'long'])
    def test_an_integer_accepts_its_own_formats(self, workspace, declared):
        assert parse(workspace, f'  T:\n    type: integer\n    format: {declared}\n') is None

    def test_an_integer_rejects_a_number_format(self, workspace):
        # Deviation D2: the spec's literal reading allows it, go-raml does not,
        # and neither do we.
        error = parse(workspace, '  T:\n    type: integer\n    format: float\n')
        assert error is not None
        assert traces(error)[0].info == {'format': 'float', 'type': 'integer'}

    def test_a_number_rejects_an_integer_format(self, workspace):
        error = parse(workspace, '  T:\n    type: number\n    format: int32\n')
        assert error is not None
        assert traces(error)[0].info == {'format': 'int32', 'type': 'number'}


class TestDateTime:
    @pytest.mark.parametrize('declared', ['rfc3339', 'rfc2616'])
    def test_the_two_formats_are_accepted(self, workspace, declared):
        assert parse(workspace, f'  T:\n    type: datetime\n    format: {declared}\n') is None

    def test_any_other_format_is_rejected(self, workspace):
        error = parse(workspace, '  T:\n    type: datetime\n    format: iso8601\n')
        assert error is not None
        assert 'unknown format' in messages(error)


class TestArray:
    def test_min_items_may_not_exceed_max_items(self, workspace):
        error = parse(workspace, '  T:\n    type: array\n    items: string\n    minItems: 5\n    maxItems: 2\n')
        assert error is not None
        assert 'minItems exceeds maxItems' in messages(error)

    def test_items_are_checked_recursively(self, workspace):
        error = parse(
            workspace,
            '  T:\n    type: array\n    items:\n      type: string\n      minLength: 9\n      maxLength: 2\n',
        )
        assert error is not None
        assert 'minLength exceeds maxLength' in messages(error)


class TestObject:
    def test_min_properties_may_not_exceed_max_properties(self, workspace):
        error = parse(workspace, '  T:\n    type: object\n    minProperties: 5\n    maxProperties: 2\n')
        assert error is not None
        assert 'minProperties exceeds maxProperties' in messages(error)

    def test_every_property_is_checked(self, workspace):
        error = parse(
            workspace,
            '  T:\n    properties:\n      a:\n        type: string\n        minLength: 9\n        maxLength: 2\n',
        )
        assert error is not None
        assert 'minLength exceeds maxLength' in messages(error)

    def test_pattern_properties_are_checked_too(self, workspace):
        error = parse(
            workspace,
            '  T:\n    properties:\n      /^a/:\n        type: integer\n        minimum: 9\n        maximum: 2\n',
        )
        assert error is not None
        assert 'minimum exceeds maximum' in messages(error)

    def test_pattern_properties_conflict_with_no_additional_properties(self, workspace):
        # A pattern property can only ever match a key the declaration did not
        # name, which `additionalProperties: false` has already forbidden.
        error = parse(
            workspace,
            '  T:\n    additionalProperties: false\n    properties:\n      /^a/: string\n',
        )
        assert error is not None
        assert 'pattern properties conflict with additionalProperties' in messages(error)

    def test_pattern_properties_are_fine_when_extras_are_allowed(self, workspace):
        assert parse(workspace, '  T:\n    additionalProperties: true\n    properties:\n      /^a/: string\n') is None


class TestUnion:
    def test_every_member_is_checked(self, workspace):
        error = parse(
            workspace,
            '  Bad:\n    type: string\n    minLength: 9\n    maxLength: 2\n  T: Bad | integer\n',
        )
        assert error is not None
        assert 'minLength exceeds maxLength' in messages(error)


class TestFile:
    def test_length_bounds_are_ordered(self, workspace):
        error = parse(workspace, '  T:\n    type: file\n    minLength: 9\n    maxLength: 2\n')
        assert error is not None
        assert 'minLength exceeds maxLength' in messages(error)

    @pytest.mark.parametrize('declared', ["'*/*'", 'image/png', 'application/vnd.api+json'])
    def test_a_wellformed_media_type_is_accepted(self, workspace, declared):
        # `*/*` is quoted because a bare `*` opens a YAML alias, not because
        # anything in RAML requires it.
        assert parse(workspace, f'  T:\n    type: file\n    fileTypes: [{declared}]\n') is None

    def test_a_malformed_media_type_is_rejected(self, workspace):
        error = parse(workspace, '  T:\n    type: file\n    fileTypes: [notamediatype]\n')
        assert error is not None
        assert traces(error)[0].info == {'fileType': 'notamediatype'}


class TestEnum:
    """`BaseShape.check()` validates every member against the shape itself."""

    def test_a_member_of_the_wrong_type_is_rejected(self, workspace):
        error = parse(workspace, "  T:\n    type: integer\n    enum: [1, 'two']\n")
        assert error is not None
        assert 'invalid enum member' in {trace.message for chain in error.chains() for trace in chain}

    def test_a_member_violating_a_facet_is_rejected(self, workspace):
        error = parse(workspace, '  T:\n    type: integer\n    maximum: 5\n    enum: [1, 99]\n')
        assert error is not None
        assert 'value is above the maximum' in messages(error)

    def test_the_offending_member_is_identified_by_index(self, workspace):
        error = parse(workspace, '  T:\n    type: integer\n    maximum: 5\n    enum: [1, 99]\n')
        assert error is not None
        indexes = [
            trace.info['index'] for chain in error.chains() for trace in chain if trace.message == 'invalid enum member'
        ]
        assert indexes == [1]

    def test_a_valid_enum_passes(self, workspace):
        assert parse(workspace, '  T:\n    type: integer\n    maximum: 100\n    enum: [1, 99]\n') is None


class TestDiscriminator:
    """docs/05 section 9."""

    def test_the_named_property_must_exist(self, workspace):
        error = parse(workspace, '  T:\n    properties:\n      a: string\n    discriminator: kind\n')
        assert error is not None
        assert traces(error)[0].info == {'property': 'kind'}

    def test_an_inline_declaration_may_not_declare_one(self, workspace):
        # Spec § Using Discriminator. Checked between P7 and P9, because a
        # discriminator is *inherited*: after unwrap every subtype of a
        # discriminated type carries one.
        body = (
            '  Person:\n    properties:\n      kind: string\n'
            '/p:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json:\n            discriminator: kind\n'
            '            properties:\n              kind: string\n'
        )
        root = workspace({'api.raml': API + 'types:\n' + body})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        assert 'discriminator on an inline type declaration' in str(caught.value)

    def test_a_body_that_inherits_a_discriminated_type_is_fine(self, workspace):
        """The false positive the ordering exists to avoid.

        The reference implementation carries this rule as a `FIXME` and enforces
        nothing, for exactly this reason.
        """
        body = (
            '  Person:\n    discriminator: kind\n    properties:\n      kind: string\n'
            '/p:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json: Person\n'
        )
        root = workspace({'api.raml': API + 'types:\n' + body})
        assert parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True)) is not None

    def test_an_inline_declaration_may_not_declare_a_discriminator_value(self, workspace):
        body = (
            '  Person:\n    discriminator: kind\n    properties:\n      kind: string\n'
            '/p:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json:\n            type: Person\n            discriminatorValue: p\n'
        )
        root = workspace({'api.raml': API + 'types:\n' + body})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        assert 'discriminator on an inline type declaration' in str(caught.value)

    def test_an_inherited_property_counts(self, workspace):
        # The reason this rule is checked in P10 and not at decode time.
        assert (
            parse(
                workspace,
                '  Parent:\n    properties:\n      kind: string\n  T:\n    type: Parent\n    discriminator: kind\n',
            )
            is None
        )

    def test_the_property_must_be_scalar(self, workspace):
        error = parse(
            workspace,
            '  T:\n    properties:\n      kind:\n        properties:\n          a: string\n    discriminator: kind\n',
        )
        assert error is not None
        assert 'discriminator property must be scalar' in messages(error)

    def test_a_discriminator_needs_properties(self, workspace):
        error = parse(workspace, '  T:\n    type: object\n    discriminator: kind\n')
        assert error is not None
        assert 'discriminator requires properties' in messages(error)

    def test_an_explicit_value_is_validated_against_the_property(self, workspace):
        error = parse(
            workspace,
            '  T:\n    properties:\n      kind: integer\n    discriminator: kind\n    discriminatorValue: notanumber\n',
        )
        assert error is not None
        assert 'invalid type' in messages(error)

    def test_a_compatible_explicit_value_passes(self, workspace):
        assert (
            parse(
                workspace,
                '  T:\n    properties:\n      kind: string\n    discriminator: kind\n    discriminatorValue: dog\n',
            )
            is None
        )

    def test_a_value_without_a_discriminator_is_rejected(self, workspace):
        error = parse(workspace, '  T:\n    properties:\n      a: string\n    discriminatorValue: dog\n')
        assert error is not None
        assert 'discriminatorValue without discriminator' in messages(error)

    def test_a_union_refuses_a_discriminator_at_decode_time(self, workspace):
        # The one discriminator rule that is *not* P10's: a union has no
        # properties, so it can never become valid later (docs/05 § 9).
        error = parse(workspace, '  T:\n    type: string | integer\n    discriminator: kind\n')
        assert error is not None
        assert 'discriminator cannot be used with union type' in messages(error)


class TestAccumulation:
    def test_several_bad_declarations_are_all_reported(self, workspace):
        # CLAUDE.md: accumulate, do not fail fast.
        error = parse(
            workspace,
            '  A:\n    type: string\n    minLength: 9\n    maxLength: 2\n'
            '  B:\n    type: integer\n    minimum: 9\n    maximum: 2\n',
        )
        assert error is not None
        assert messages(error) == {'minLength exceeds maxLength', 'minimum exceeds maximum'}

    def test_two_bad_properties_of_one_object_are_both_reported(self, workspace):
        error = parse(
            workspace,
            '  T:\n    properties:\n'
            '      a:\n        type: string\n        minLength: 9\n        maxLength: 2\n'
            '      b:\n        type: string\n        minLength: 8\n        maxLength: 1\n',
        )
        assert error is not None
        assert len([t for t in traces(error) if t.message == 'minLength exceeds maxLength']) == 2


class TestNotRunWithoutTheOption:
    def test_a_bad_declaration_parses_when_validation_is_off(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T:\n    type: string\n    minLength: 9\n    maxLength: 2\n'})
        raml = parse_from_path(root / 'api.raml', ParseOptions())
        assert raml.types_in(raml.location)['T'].shape.min_length.value == 9
