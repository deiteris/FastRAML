"""The lint engine and built-in rules — docs/18-linting.md."""

from __future__ import annotations

import json
import sys

import pytest

from fastraml.cli import EXIT_INVALID, EXIT_OK, main
from fastraml.parser.entry import ParseOptions, parse_from_path, parse_from_string
from fastraml.positions import Position
from fastraml.views.lint import (
    Category,
    Config,
    Finding,
    Linter,
    Registry,
    RuleMeta,
    RuleSetting,
    Severity,
    builtin_registry,
    config_shape,
    limit_findings,
    parse_config,
    render_findings,
)
from fastraml.views.lint.config import SCHEMA


def parsed(source: str, tmp_path):
    return parse_from_string(
        source,
        file_name='api.raml',
        base_dir=tmp_path,
        options=ParseOptions(unwrap=True, validate=False, retain_source=True),
    )


class TestRuleExamples:
    @pytest.mark.parametrize('rule', builtin_registry().all(), ids=lambda rule: rule.meta.id)
    def test_good_is_silent_and_bad_fires(self, rule, tmp_path):
        config = Config(extends=(), rules=(RuleSetting(id=rule.meta.id),))
        linter = Linter(builtin_registry(), config)
        assert not linter.run(parsed(rule.meta.good, tmp_path))
        findings = linter.run(parsed(rule.meta.bad, tmp_path))
        assert [finding.rule for finding in findings] == [rule.meta.id]
        assert findings[0].info

    @pytest.mark.parametrize(
        'source',
        [
            '#%RAML 1.0 Library\ntypes:\n  Exported: string\ntraits:\n  exported: {}\n',
            '#%RAML 1.0 DataType\ntype: string\n',
        ],
    )
    def test_exported_fragment_declarations_are_not_unused(self, source, tmp_path):
        findings = Linter(builtin_registry()).run(parsed(source, tmp_path))
        assert not [finding for finding in findings if finding.rule in {'unused-type', 'unused-trait'}]

    def test_lint_config_schema_is_clean_under_recommended_rules(self):
        raml = parse_from_path(SCHEMA, ParseOptions(unwrap=True, retain_source=True))
        assert not Linter(builtin_registry()).run(raml)

    def test_complete_ruleset_checks_named_and_nested_string_shapes_once(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  Name: string\n  Names:\n    type: array\n    items: string\n'
            '/a:\n  post:\n    body:\n      application/json:\n        properties:\n'
            '          name: Name\n          names: Names\n'
        )
        config = Config(extends=('security',))
        findings = [
            finding
            for finding in Linter(builtin_registry(), config).run(parsed(source, tmp_path))
            if finding.rule == 'unbounded-string'
        ]
        assert [finding.info['type'] for finding in findings] == ['Name', 'items']
        assert len({finding.iri for finding in findings}) == len(findings)

    def test_input_security_rules_ignore_response_only_shapes(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  Output:\n    properties:\n      message: string\n'
            '/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json: Output\n'
        )
        config = Config(extends=('security',))
        input_rules = {
            'bounded-additional-properties',
            'bounded-array',
            'bounded-integer',
            'integer-format',
            'no-additional-properties',
            'restricted-string',
            'unbounded-string',
        }
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert not input_rules & {finding.rule for finding in findings}

    def test_lint_config_schema_is_clean_under_complete_ruleset(self):
        raml = parse_from_path(SCHEMA, ParseOptions(unwrap=True, retain_source=True))
        config = parse_config('extends: [recommended, security]\n', builtin_registry())
        assert not Linter(builtin_registry(), config).run(raml)

    def test_all_builtin_rules_run_together(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\nprotocols: [HTTPS]\n/a:\n  get:\n'
        findings = Linter(builtin_registry(), Config(extends=('all',))).run(parsed(source, tmp_path))
        assert {'missing-description', 'missing-display-name', 'required-500-response'} <= {
            finding.rule for finding in findings
        }

    @pytest.mark.parametrize(
        'paths',
        [
            '/users/me:\n  get:\n/users/{id}:\n  get:\n',
            '/users/{id}:\n  get:\n/users/me:\n  get:\n',
            '/users/{id}/posts:\n  get:\n/users/me/posts:\n  get:\n',
        ],
    )
    def test_ambiguous_paths_are_found_in_either_declaration_order(self, paths, tmp_path):
        config = Config(extends=(), rules=(RuleSetting(id='no-ambiguous-paths'),))
        findings = Linter(builtin_registry(), config).run(parsed('#%RAML 1.0\ntitle: t\n' + paths, tmp_path))
        assert [finding.rule for finding in findings] == ['no-ambiguous-paths']

    def test_disjoint_methods_do_not_make_overlapping_paths_ambiguous(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/users/me:\n  get:\n/users/{id}:\n  post:\n'
        config = Config(extends=(), rules=(RuleSetting(id='no-ambiguous-paths'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    @pytest.mark.parametrize(
        ('media_type', 'shape', 'valid'),
        [
            ('application/json', 'object', True),
            ('application/json', 'string', True),
            ('application/problem+json', 'file', False),
            ('application/octet-stream', 'file', True),
            ('application/octet-stream', 'object', False),
            ('image/png', 'file', True),
            ('image/png', 'object', False),
            ('application/x-www-form-urlencoded', 'object', True),
            ('multipart/form-data', 'string', False),
            ('text/plain', 'string', True),
            ('text/plain', 'object', False),
            ('text/csv', 'integer', True),
        ],
    )
    def test_response_schema_must_match_media_type(self, media_type, shape, valid, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            f'          {media_type}: {shape}\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='meaningless-media-type-schema'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert bool(findings) is not valid

    def test_file_types_must_include_the_body_media_type(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          image/png:\n            type: file\n            fileTypes: [image/jpeg]\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='meaningless-media-type-schema'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].info['reason'] == 'fileTypes excludes the declared media type'

    def test_file_type_wildcard_can_include_the_body_media_type(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          image/png:\n            type: file\n            fileTypes: [image/*]\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='meaningless-media-type-schema'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_untyped_json_payload_has_only_the_untyped_finding(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n'
        findings = Linter(builtin_registry()).run(parsed(source, tmp_path))
        assert [finding.rule for finding in findings] == ['untyped-payload']

    @pytest.mark.parametrize('shape', ['integer', 'number'])
    def test_numeric_resource_id_checks_uri_parameter_shape_not_its_name(self, shape, tmp_path):
        source = f'#%RAML 1.0\ntitle: t\n/reports/{{year}}:\n  uriParameters:\n    year: {shape}\n'
        config = Config(extends=(), rules=(RuleSetting(id='numeric-resource-id'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].info == {'parameter': 'year', 'type': shape}

    def test_numeric_query_parameter_is_not_a_resource_id(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/reports:\n  get:\n    queryParameters:\n      year: integer\n'
        config = Config(extends=(), rules=(RuleSetting(id='numeric-resource-id'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_numeric_resource_id_follows_a_named_type(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  Sequence: integer\n'
            '/items/{cursor}:\n  uriParameters:\n    cursor: Sequence\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='numeric-resource-id'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].info['parameter'] == 'cursor'

    def test_explicit_uri_parameter_reports_only_synthesized_parameters(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/implicit/{id}:\n  get:\n/explicit/{id}:\n  uriParameters:\n    id: string\n  get:\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='explicit-uri-parameter'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info for finding in findings] == [{'parameter': 'id', 'path': '/implicit/{id}'}]

    def test_explicit_ancestor_uri_parameter_is_not_reported_on_a_child(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/users/{id}:\n  uriParameters:\n    id: string\n  /photos:\n    get:\n'
        config = Config(extends=(), rules=(RuleSetting(id='explicit-uri-parameter'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_unused_type_follows_reachability_from_the_effective_api(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  DeadChild: string\n  DeadParent:\n'
            '    properties:\n      child: DeadChild\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='unused-type'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert {finding.info['type'] for finding in findings} == {'DeadChild', 'DeadParent'}

    def test_base_uri_parameter_type_is_used(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\nbaseUri: https://example.test/{tenant}\n'
            'types:\n  Tenant: string\nbaseUriParameters:\n  tenant: Tenant\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='unused-type'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_bodyless_operation_does_not_need_a_payload_example(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/ping:\n  get:\n    responses:\n      204: {}\n'
        config = Config(extends=(), rules=(RuleSetting(id='missing-example'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_empty_examples_do_not_satisfy_missing_example(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\ntypes:\n  Empty:\n    type: string\n    examples: {}\n'
        config = Config(extends=(), rules=(RuleSetting(id='missing-example'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['type'] for finding in findings] == ['Empty']

    def test_documentation_rules_do_not_report_anonymous_nested_shapes(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ndescription: API\ntypes:\n  User:\n    description: A user\n    example: {name: Ada}\n'
            '    properties:\n      name: string\n'
        )
        config = Config(
            extends=(),
            rules=(RuleSetting(id='missing-description'), RuleSetting(id='missing-example')),
        )
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_https_only_uses_the_base_uri_protocol(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\nbaseUri: https://api.example.test\n/a:\n  get:\n'
        config = Config(extends=(), rules=(RuleSetting(id='https-only'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_optional_security_alternative_is_unsecured(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n'
            '/a:\n  get:\n    securedBy: [basic, null]\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='unsecured-operation'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.rule for finding in findings] == ['unsecured-operation']

    @pytest.mark.parametrize('secured_by', ['', '    securedBy: [null]\n'])
    def test_public_operation_does_not_require_a_401_response(self, secured_by, tmp_path):
        source = f'#%RAML 1.0\ntitle: t\n/a:\n  get:\n{secured_by}'
        config = Config(extends=(), rules=(RuleSetting(id='required-401-response'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_operation_without_request_inputs_does_not_require_a_validation_response(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/status:\n  get:\n'
        config = Config(extends=(), rules=(RuleSetting(id='validation-error-response'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_uri_parameter_requires_a_validation_response(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/users/{id}:\n  get:\n'
        config = Config(extends=(), rules=(RuleSetting(id='validation-error-response'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.rule for finding in findings] == ['validation-error-response']

    def test_retry_after_requires_delay_seconds_or_http_date(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      429:\n'
            '        headers:\n          Retry-After: object\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='retry-after-429'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].info['type'] == 'object'

    def test_rate_limit_headers_are_not_required_on_unrelated_client_errors(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      404: {}\n'
        config = Config(extends=(), rules=(RuleSetting(id='rate-limit-headers'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_rate_limit_header_requires_a_usable_shape(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n'
            '        headers:\n          X-RateLimit-Limit: string\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='rate-limit-headers'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].message == 'response has no usable rate-limit header'

    @pytest.mark.parametrize('header', ['X-Rate-Limit-Limit', 'RateLimit-Limit', 'RateLimit-Reset'])
    def test_rate_limit_rule_rejects_copied_or_obsolete_header_spellings(self, header, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n'
            f'        headers:\n          {header}: integer\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='rate-limit-headers'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].message == 'response has no rate-limit header'

    @pytest.mark.parametrize(
        ('header', 'shape'),
        [
            ('RateLimit', 'string'),
            ('RateLimit-Policy', 'string'),
            ('X-RateLimit-Limit', 'integer'),
            ('X-RateLimit-Remaining', 'integer'),
            ('X-RateLimit-Reset', 'integer'),
        ],
    )
    def test_rate_limit_rule_accepts_current_and_established_headers(self, header, shape, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n'
            f'        headers:\n          {header}: {shape}\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='rate-limit-headers'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_implicitly_open_input_object_needs_a_property_bound(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  Input:\n    type: object\n'
            '/a:\n  post:\n    body:\n      application/json: Input\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='bounded-additional-properties'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['type'] for finding in findings] == ['Input']

    @pytest.mark.parametrize(
        ('rule', 'status'), [('required-500-response', '500'), ('validation-error-response', '400')]
    )
    def test_required_response_must_be_typed(self, rule, status, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    queryParameters:\n      q: string\n    responses:\n'
            f'      {status}:\n        body:\n          application/json:\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id=rule),))
        assert [finding.rule for finding in Linter(builtin_registry(), config).run(parsed(source, tmp_path))] == [rule]

    def test_json_schema_projection_is_checked_against_media_type(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/octet-stream: |\n            {"type": "object"}\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='meaningless-media-type-schema'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert findings[0].info['reason'] == 'binary media requires a file shape'

    def test_json_ref_siblings_name_the_schema_and_every_exact_path(self, workspace):
        schema = json.dumps(
            {
                'definitions': {'Name': {'type': 'string'}},
                'properties': {
                    'a/b': {'$ref': '#/definitions/Name', 'description': 'name'},
                    't~x': {'allOf': [{'$ref': '#/definitions/Name', 'maxLength': 8}]},
                },
            }
        )
        api = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  T: !include schema.json\n'
            '/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json: T\n'
            '/b:\n  get:\n    responses:\n      200:\n        body:\n          application/json: T\n'
        )
        root = workspace({'api.raml': api, 'schema.json': schema})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        config = Config(extends=(), rules=(RuleSetting(id='json-ref-siblings'),))
        findings = Linter(builtin_registry(), config).run(raml)
        assert {finding.info['schemaPath'] for finding in findings} == {
            '#/properties/a~1b/$ref',
            '#/properties/t~0x/allOf/0/$ref',
        }
        assert {finding.info['siblings'] for finding in findings} == {'description', 'maxLength'}
        assert all(finding.location.endswith('/schema.json') for finding in findings)
        assert all(not finding.position.is_known for finding in findings)

    def test_raml_source_spelling_rules_ignore_external_json_schema_syntax(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  External: !include schema.json\n',
                'schema.json': '{"type": "string"}',
            }
        )
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        config = Config(extends=(), rules=(RuleSetting(id='prefer-inline-alias'),))
        assert not Linter(builtin_registry(), config).run(raml)

    def test_prefer_inline_alias_names_the_referenced_type(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  T: string\n/a:\n  post:\n    body:\n'
            '      application/json:\n        type: T\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='prefer-inline-alias'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['type'] for finding in findings] == ['T']

    def test_preceding_comment_suppresses_one_rule_at_one_site(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n'
            '      # fastraml: ignore optional-and-nil\n'
            '      ignored?: string?\n'
            '      reported?: string?\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['property'] for finding in findings] == ['reported']

    @pytest.mark.parametrize('newline', ['\n', '\r\n'])
    def test_suppression_handles_source_line_endings(self, newline, tmp_path):
        source = newline.join(
            (
                '#%RAML 1.0',
                'title: t',
                'types:',
                '  T:',
                '    properties:',
                '      # FASTRAML: IGNORE optional-and-nil',
                '      ignored?: string?',
                '',
            )
        )
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    @pytest.mark.parametrize(
        'between',
        [
            '      # fastraml: ignore optional-and-nil\n\n      ignored?: string?\n',
            '      ignored?: string? # fastraml: ignore optional-and-nil\n',
        ],
    )
    def test_non_preceding_suppression_comments_do_not_apply(self, between, tmp_path):
        source = '#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n' + between
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['property'] for finding in findings] == ['ignored']

    def test_wildcard_suppression_is_local_to_one_source_line(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n'
            '      # fastraml: ignore *\n'
            '      ignored?: string?\n'
            '      reported?: string?\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        findings = Linter(builtin_registry(), config).run(parsed(source, tmp_path))
        assert [finding.info['property'] for finding in findings] == ['reported']

    def test_suppression_does_not_require_the_targets_indentation(self, tmp_path):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n'
            '    # fastraml: ignore optional-and-nil\n'
            '      ignored?: string?\n'
        )
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_unknown_position_is_not_suppressed(self, tmp_path):
        class UnknownPositionRule:
            meta = RuleMeta('unknown-position', Category.STYLE, 'test', 'test', Severity.INFO)

            def api(self, ctx, iri, api):
                return (ctx.at(self.meta, 'test', location=api.location, iri=iri, entity='API'),)

        registry = Registry()
        registry.add(UnknownPositionRule())
        config = Config(extends=(), rules=(RuleSetting(id='unknown-position'),))
        source = '#%RAML 1.0\n# fastraml: ignore *\ntitle: t\n'
        assert [finding.rule for finding in Linter(registry, config).run(parsed(source, tmp_path))] == [
            'unknown-position'
        ]

    def test_api_finding_can_be_suppressed_at_the_root_mapping(self, tmp_path):
        source = '#%RAML 1.0\n# fastraml: ignore missing-description\ntitle: t\n'
        config = Config(extends=(), rules=(RuleSetting(id='missing-description'),))
        assert not Linter(builtin_registry(), config).run(parsed(source, tmp_path))

    def test_suppression_uses_an_included_files_location(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: t\nuses:\n  lib: lib.raml\n',
                'lib.raml': (
                    '#%RAML 1.0 Library\ntypes:\n  T:\n    properties:\n'
                    '      # fastraml: ignore optional-and-nil\n'
                    '      ignored?: string?\n'
                ),
            }
        )
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True, retain_source=True))
        config = Config(extends=(), rules=(RuleSetting(id='optional-and-nil'),))
        assert not Linter(builtin_registry(), config).run(raml)


class TestConfiguration:
    def test_source_spelling_rules_require_retained_source(self, tmp_path):
        raml = parse_from_string(
            '#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    type: array\n    items: string\n',
            file_name='api.raml',
            base_dir=tmp_path,
            options=ParseOptions(unwrap=True),
        )
        config = Config(extends=(), rules=(RuleSetting(id='prefer-array-expression'),))
        with pytest.raises(RuntimeError, match='need retained source'):
            Linter(builtin_registry(), config).run(raml)

    def test_registered_rule_id_must_be_configurable_and_suppressible(self):
        class InvalidIdRule:
            meta = RuleMeta('house/rule', Category.STYLE, 'test', 'test', Severity.INFO)

            def api(self, ctx, iri, api):
                return ()

        with pytest.raises(ValueError, match='invalid rule id'):
            Registry().add(InvalidIdRule())

    @pytest.mark.parametrize(
        'source',
        [
            'unknown: true\n',
            'extends: [recommended, 1]\n',
            'plugins: [true]\n',
            'categories:\n  security: {severity: fatal}\n',
            'rules: {}\n',
            'rules:\n  - severity: error\n',
            'rules:\n  - id: unused-type\n    options: []\n',
        ],
    )
    def test_data_type_rejects_invalid_structure(self, source):
        with pytest.raises(ValueError, match=r'.'):
            parse_config(source, builtin_registry())

    def test_config_data_type_is_available_to_consumers(self):
        shape = config_shape()
        assert shape.validate({'extends': ['recommended'], 'rules': [{'id': 'unused-type'}]}) is None

    def test_recommended_excludes_security(self):
        linter = Linter(builtin_registry())
        enabled = {rule.meta.id for rule in linter.rules}
        assert 'meaningless-media-type-schema' in enabled
        assert 'optional-and-nil' not in enabled
        assert 'unbounded-string' not in enabled

    def test_discovered_plugin_requires_activation(self):
        class HouseRule:
            meta = RuleMeta('house-rule', Category.STYLE, 'house rule', 'test rule', Severity.INFO)

            def operation(self, ctx, iri, operation):
                return ()

        registry = builtin_registry()
        registry.add(HouseRule(), source='house-package', plugin='house-style')
        assert 'house-rule' not in {rule.meta.id for rule in Linter(registry, Config(extends=('all',))).rules}
        enabled = Linter(registry, Config(extends=('all',), plugins=('house-style',)))
        assert 'house-rule' in {rule.meta.id for rule in enabled.rules}
        assert registry.source_of('house-rule') == 'house-package'

    def test_rule_overrides_category_and_ruleset(self):
        registry = builtin_registry()
        config = parse_config(
            'extends: security\ncategories:\n  security: {severity: error, disabled: true}\nrules:\n'
            '  - id: unsecured-operation\n    disabled: false\n',
            registry,
        )
        linter = Linter(registry, config)
        assert [rule.meta.id for rule in linter.rules] == ['unsecured-operation']
        assert linter.severity_of(registry.get('unsecured-operation').meta) is Severity.ERROR

    def test_visitor_receives_its_configured_options(self, tmp_path):
        class ConfiguredRule:
            meta = RuleMeta('configured', Category.STYLE, 'configured rule', 'test rule', Severity.INFO)

            def operation(self, ctx, iri, operation):
                if ctx.options.get('report'):
                    return (
                        ctx.at(self.meta, 'configured finding', location=operation.location, iri=iri, method='get'),
                    )
                return ()

        registry = Registry()
        registry.add(ConfiguredRule())
        config = Config(extends=(), rules=(RuleSetting(id='configured', options={'report': True}),))
        findings = Linter(registry, config).run(parsed('#%RAML 1.0\ntitle: t\n/a:\n  get:\n', tmp_path))
        assert [finding.rule for finding in findings] == ['configured']

    def test_match_suppresses_only_matching_findings(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      keep?: string?\n      skip?: string?\n'
        registry = builtin_registry()
        config = parse_config(
            "extends: []\nrules:\n  - id: optional-and-nil\n  - id: optional-and-nil\n    match: 'property: skip'\n    disabled: true\n",
            registry,
        )
        findings = Linter(registry, config).run(parsed(source, tmp_path))
        assert [finding.info['property'] for finding in findings] == ['keep']

    @pytest.mark.parametrize(
        ('source', 'message'),
        [
            ('extends: nope\n', 'unknown ruleset'),
            ('rules:\n  - id: nope\n', 'unknown rule'),
            ('categories:\n  nope: {}\n', 'unknown rule category'),
            ('plugins: [nope]\n', 'unknown lint plugin'),
            ('categories:\n  1: {}\n', 'unknown rule category'),
            ("rules:\n  - id: unused-type\n    match: '['\n", 'invalid match regex'),
        ],
    )
    def test_unknown_or_invalid_configuration_is_rejected(self, source, message):
        with pytest.raises(ValueError, match=message):
            parse_config(source, builtin_registry())


class TestLintCli:
    def test_list_and_explain_need_no_document(self, capsys):
        assert main(['lint', '--list-rules']) == EXIT_OK
        assert 'optional-and-nil' in capsys.readouterr().out
        assert main(['lint', '--explain', 'optional-and-nil']) == EXIT_OK
        assert 'Good:' in capsys.readouterr().out

    def test_default_warnings_do_not_fail_the_run(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\nschemas:\n  U: string\n'})
        assert main(['lint', str(root / 'api.raml')]) == EXIT_OK
        output = capsys.readouterr().out
        assert 'deprecated-schemas' in output
        assert 'FAIL 0 errors, 1 warning and 1 info finding.' in output

    def test_human_color_is_tty_only_and_can_be_disabled(self, workspace, capsys, monkeypatch):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\nschemas:\n  U: string\n'})
        monkeypatch.delenv('NO_COLOR', raising=False)
        monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
        assert main(['lint', str(root / 'api.raml')]) == EXIT_OK
        assert '\x1b[33m' in capsys.readouterr().out

        assert main(['lint', '--no-color', str(root / 'api.raml')]) == EXIT_OK
        assert '\x1b[' not in capsys.readouterr().out

        monkeypatch.setenv('NO_COLOR', '1')
        assert main(['lint', str(root / 'api.raml')]) == EXIT_OK
        assert '\x1b[' not in capsys.readouterr().out

    def test_human_output_file_is_never_colored(self, workspace, tmp_path, capsys, monkeypatch):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\nschemas:\n  U: string\n'})
        output = tmp_path / 'lint.txt'
        monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
        assert main(['lint', '-o', str(output), str(root / 'api.raml')]) == EXIT_OK
        assert capsys.readouterr().out == ''
        assert '\x1b[' not in output.read_text(encoding='utf-8')

    def test_configured_errors_fail_and_json_is_structured(self, workspace, tmp_path, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n'})
        config = tmp_path / 'lint.yaml'
        config.write_text(
            'lint:\n  extends: []\n  rules:\n    - id: optional-and-nil\n      severity: error\n',
            encoding='utf-8',
        )
        assert main(['lint', '--config', str(config), '--format', 'json', str(root / 'api.raml')]) == EXIT_INVALID
        output = json.loads(capsys.readouterr().out)
        assert output['schemaVersion'] == 1
        assert output['counts']['error'] == 1
        assert output['findings'][0]['info'] == {'property': 'a'}
        assert output['findings'][0]['location'].startswith('file://')
        assert output['findings'][0]['position'] == '6:7'
        assert output['findings'][0]['line'] == 6
        assert output['findings'][0]['column'] == 7

    def test_text_is_compact_uncolored_agent_output(self):
        finding = Finding(
            'test-rule',
            Severity.WARNING,
            'do the useful thing',
            'file:///api.raml',
            Position(3, 5, 3, 9),
            info={'value': 'bad'},
        )
        assert render_findings([finding], 'text', color=True) == (
            'WARNING test-rule file:///api.raml:3:5 do the useful thing: value: bad\n'
        )

    def test_human_groups_findings_and_puts_message_before_rule(self):
        findings = [
            Finding('first-rule', Severity.ERROR, 'first message', 'file:///a.raml', Position(2, 3)),
            Finding('second-rule', Severity.INFO, 'second message', 'file:///a.raml', Position(10, 1)),
            Finding('third-rule', Severity.WARNING, 'third message', 'https://example.test/b.raml'),
        ]
        assert render_findings(findings, 'human') == (
            ' file:///a.raml\n'
            '\n'
            ' 2:3   error    first message  first-rule\n'
            ' 10:1  info     second message  second-rule\n'
            '\n'
            ' https://example.test/b.raml\n'
            '\n'
            ' -  warning  third message  third-rule\n'
            '\n'
            'FAIL 1 error, 1 warning and 1 info finding.\n'
        )

    def test_clean_human_report_has_a_success_summary(self):
        assert render_findings([], 'human') == 'OK 0 errors, 0 warnings and 0 info findings.\n'

    def test_every_file_is_attempted_after_a_parse_failure(self, workspace, capsys):
        root = workspace(
            {
                'bad.raml': 'not RAML\n',
                'lint.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n',
            }
        )
        assert main(['lint', str(root / 'bad.raml'), str(root / 'lint.raml')]) == EXIT_INVALID
        captured = capsys.readouterr()
        assert 'invalid' in captured.err
        assert 'unused-type' in captured.out

    def test_summary_groups_findings_by_rule(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n'
        findings = Linter(builtin_registry()).run(parsed(source, tmp_path))
        summary = render_findings(findings, 'summary')
        assert 'unused-type' in summary
        assert '2' in summary

    def test_cli_limits_output_and_reports_truncation(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n  C: string\n'})
        assert main(['lint', '--max-findings', '1', '--max-findings-per-rule', '0', str(root / 'api.raml')]) == EXIT_OK
        output = capsys.readouterr().out
        assert output.count('unused-type') == 1
        assert '2 findings omitted (1 of 3 shown)' in output

    def test_truncated_text_ends_with_compact_complete_counts(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n  C: string\n'})
        assert (
            main(
                [
                    'lint',
                    '--format',
                    'text',
                    '--max-findings',
                    '1',
                    '--max-findings-per-rule',
                    '0',
                    str(root / 'api.raml'),
                ]
            )
            == EXIT_OK
        )
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith('INFO unused-type file://')
        assert lines[-1] == 'SUMMARY error=0 warning=0 info=3 shown=1 total=3 omitted=2 truncated=true'

    def test_cli_json_includes_complete_truncation_metadata(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n  C: string\n'})
        assert main(['lint', '--max-findings-per-rule', '1', '--format', 'json', str(root / 'api.raml')]) == EXIT_OK
        output = json.loads(capsys.readouterr().out)
        assert output['total'] == 3
        assert output['shown'] == 1
        assert output['truncated'] is True
        assert output['counts']['info'] == 3
        assert output['shownCounts']['info'] == 1
        assert output['omittedByRule'] == {'unused-type': 2}

    def test_zero_disables_finding_limits(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n'})
        assert main(['lint', '--max-findings', '0', '--max-findings-per-rule', '0', str(root / 'api.raml')]) == EXIT_OK
        assert capsys.readouterr().out.count('unused-type') == 2

    def test_negative_finding_limit_is_invalid(self, capsys):
        assert main(['lint', '--max-findings', '-1', 'api.raml']) == EXIT_INVALID
        assert 'must be non-negative' in capsys.readouterr().err

    def test_cli_rule_enables_an_opt_in_rule(self, workspace, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\n/users/{id}:\n  get:\n'})
        assert (
            main(
                [
                    'lint',
                    '--format',
                    'text',
                    '--rule',
                    'explicit-uri-parameter',
                    str(root / 'api.raml'),
                ]
            )
            == EXIT_OK
        )
        assert 'WARNING explicit-uri-parameter' in capsys.readouterr().out

    def test_cli_rule_can_regrade_and_override_file_config(self, workspace, tmp_path, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\n/users/{id}:\n  get:\n'})
        config = tmp_path / 'lint.yaml'
        config.write_text(
            'lint:\n  extends: []\n  rules:\n    - id: explicit-uri-parameter\n      severity: error\n',
            encoding='utf-8',
        )
        assert (
            main(
                [
                    'lint',
                    '--config',
                    str(config),
                    '--format',
                    'text',
                    '--rule',
                    'explicit-uri-parameter=warning',
                    str(root / 'api.raml'),
                ]
            )
            == EXIT_OK
        )
        assert 'WARNING explicit-uri-parameter' in capsys.readouterr().out

    def test_cli_rule_can_disable_a_default_rule(self, workspace, capsys):
        source = (
            '#%RAML 1.0\ntitle: t\ntypes:\n  User: |\n'
            '    {"definitions":{"Name":{"type":"string"}},'
            '"allOf":[{"$ref":"#/definitions/Name","maxLength":8}]}\n'
        )
        root = workspace({'api.raml': source})
        assert main(['lint', '--format', 'text', '--rule', 'json-ref-siblings=off', str(root / 'api.raml')]) == EXIT_OK
        assert 'json-ref-siblings' not in capsys.readouterr().out

    @pytest.mark.parametrize(
        ('arguments', 'message'),
        [
            (['--rule', 'not-a-rule'], 'unknown rule'),
            (['--rule', 'unused-type=loud'], 'unknown severity'),
            (['--rule', '=warning'], 'invalid rule override'),
            (['--rule', 'unused-type', '--rule', 'unused-type=off'], 'duplicate rule override'),
        ],
    )
    def test_invalid_cli_rule_override_is_rejected_before_parsing(self, arguments, message, capsys):
        assert main(['lint', *arguments, 'missing.raml']) == EXIT_INVALID
        assert message in capsys.readouterr().err


class TestMetrics:
    """`Linter.measure` and `--metrics` — docs/18 § 7.1."""

    #: Two unused types, so a document rule has real work and real output.
    UNUSED = '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n'

    def test_measure_agrees_with_run(self, tmp_path):
        raml = parsed(self.UNUSED, tmp_path)
        linter = Linter(builtin_registry())
        assert [f.rule for f in linter.measure(raml).findings] == [f.rule for f in linter.run(raml)]

    def test_report_is_bounded_without_changing_run(self, tmp_path):
        raml = parsed(self.UNUSED, tmp_path)
        linter = Linter(builtin_registry())
        report = linter.report(raml, max_findings_per_rule=1)
        assert len(linter.run(raml)) == 2
        assert len(report.findings) == 1
        assert report.total_findings == 2
        assert report.omitted_findings == 1
        assert report.truncated
        assert report.rule_counts == {'unused-type': 2}

    def test_per_rule_bound_reserves_space_for_other_rules(self):
        findings = [
            Finding('noisy', Severity.INFO, 'test', 'file:///a.raml'),
            Finding('noisy', Severity.INFO, 'test', 'file:///a.raml'),
            Finding('other', Severity.WARNING, 'test', 'file:///a.raml'),
        ]
        report = limit_findings(findings, max_findings=2, max_findings_per_rule=1)
        assert [finding.rule for finding in report.findings] == ['noisy', 'other']
        assert report.rule_counts == {'noisy': 2, 'other': 1}

    def test_default_report_bounds_a_large_noisy_document(self, tmp_path):
        declarations = ''.join(f'  T{index}: string\n' for index in range(1001))
        report = Linter(builtin_registry()).report(parsed(f'#%RAML 1.0\ntitle: t\ntypes:\n{declarations}', tmp_path))
        assert report.total_findings == 1001
        assert len(report.findings) == 100
        assert report.omitted_findings == 901
        assert report.rule_counts == {'unused-type': 1001}

    def test_a_generator_rule_is_timed_for_its_work(self, tmp_path):
        """Every rule in `rules/document.py` yields, so the engine has to
        materialise inside the timed region. Timing the bare call would report
        zero findings and near-zero time for exactly the slowest rules."""
        metrics = Linter(builtin_registry()).measure(parsed(self.UNUSED, tmp_path)).metrics
        unused = next(rule for rule in metrics.rules if rule.id == 'unused-type')
        assert unused.kind == 'document'
        assert unused.calls == 1
        assert unused.findings == 2
        assert unused.nanoseconds > 0

    def test_every_enabled_rule_gets_a_row_even_at_zero(self, tmp_path):
        linter = Linter(builtin_registry())
        metrics = linter.measure(parsed(self.UNUSED, tmp_path)).metrics
        assert {rule.id for rule in metrics.rules} == {rule.meta.id for rule in linter.rules}
        assert any(rule.calls == 0 or rule.findings == 0 for rule in metrics.rules)

    def test_rules_are_slowest_first(self, tmp_path):
        metrics = Linter(builtin_registry()).measure(parsed(self.UNUSED, tmp_path)).metrics
        assert [rule.nanoseconds for rule in metrics.rules] == sorted(
            (rule.nanoseconds for rule in metrics.rules), reverse=True
        )

    def test_a_supplied_graph_is_not_timed(self, tmp_path):
        """`-`, never `0`: this run did not build it and cannot say what it cost."""
        from fastraml.views.graph import build_graph

        raml = parsed(self.UNUSED, tmp_path)
        graph = build_graph(raml)
        metrics = Linter(builtin_registry()).measure(raml, graph=graph).metrics
        assert metrics.graph.source == 'supplied'
        assert metrics.graph.nanoseconds is None
        assert metrics.graph.to_dict()['ms'] is None
        assert metrics.graph.nodes == len(graph.nodes)

    def test_produced_counts_findings_a_filter_then_dropped(self, tmp_path):
        """A rule whose findings are all suppressed is still paying for them."""
        config = parse_config(
            'extends: [recommended]\nrules:\n  - id: unused-type\n    match: ".*"\n    disabled: true\n',
            builtin_registry(),
        )
        run = Linter(builtin_registry(), config).measure(parsed(self.UNUSED, tmp_path))
        assert not [f for f in run.findings if f.rule == 'unused-type']
        assert run.metrics.produced_findings == 2

    def test_built_ins_aggregate_under_their_provider(self, tmp_path):
        metrics = Linter(builtin_registry()).measure(parsed(self.UNUSED, tmp_path)).metrics
        assert [plugin.name for plugin in metrics.plugins] == ['fastraml']
        provider = metrics.plugins[0]
        assert provider.source == 'fastraml'
        assert provider.calls == metrics.fanout_calls
        assert provider.findings == metrics.produced_findings

    def test_metrics_go_to_stderr_leaving_stdout_parseable(self, workspace, capsys):
        root = workspace({'api.raml': self.UNUSED})
        assert main(['lint', '--metrics', '--format', 'json', str(root / 'api.raml')]) == EXIT_OK
        captured = capsys.readouterr()
        assert json.loads(captured.out)['findings']
        assert 'unused-type' in captured.err
        assert str(root / 'api.raml') in captured.err
