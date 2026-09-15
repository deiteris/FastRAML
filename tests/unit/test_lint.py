"""The lint engine and built-in rules — docs/18-linting.md."""

from __future__ import annotations

import json

import pytest

from fastraml.cli import EXIT_INVALID, EXIT_OK, main
from fastraml.parser.entry import ParseOptions, parse_from_string
from fastraml.views.lint import (
    Category,
    Config,
    Linter,
    Registry,
    RuleMeta,
    RuleSetting,
    Severity,
    builtin_registry,
    parse_config,
    render_findings,
)


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


class TestConfiguration:
    def test_recommended_excludes_security(self):
        linter = Linter(builtin_registry())
        assert 'optional-and-nil' in {rule.meta.id for rule in linter.rules}
        assert 'unbounded-string' not in {rule.meta.id for rule in linter.rules}

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
            'extends: security\ncategories:\n  security: {severity: error}\nrules:\n  - id: unbounded-string\n    disabled: true\n',
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
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n'})
        assert main(['lint', str(root / 'api.raml')]) == EXIT_OK
        assert 'optional-and-nil' in capsys.readouterr().out

    def test_configured_errors_fail_and_json_is_structured(self, workspace, tmp_path, capsys):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n'})
        config = tmp_path / 'lint.yaml'
        config.write_text('extends: []\nrules:\n  - id: optional-and-nil\n    severity: error\n', encoding='utf-8')
        assert main(['lint', '--config', str(config), '--format', 'json', str(root / 'api.raml')]) == EXIT_INVALID
        output = json.loads(capsys.readouterr().out)
        assert output['counts']['error'] == 1
        assert output['findings'][0]['info'] == {'property': 'a'}
        assert output['findings'][0]['location'].startswith('file://')
        assert output['findings'][0]['position'] == '6:7'

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
        assert 'optional-and-nil' in captured.out

    def test_summary_groups_findings_by_rule(self, tmp_path):
        source = '#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n      b?: string?\n'
        findings = Linter(builtin_registry()).run(parsed(source, tmp_path))
        summary = render_findings(findings, 'summary')
        assert 'optional-and-nil' in summary
        assert '2' in summary


class TestMetrics:
    """`Linter.measure` and `--metrics` — docs/18 § 7.1."""

    #: Two unused types, so a document rule has real work and real output.
    UNUSED = '#%RAML 1.0\ntitle: t\ntypes:\n  A: string\n  B: string\n'

    def test_measure_agrees_with_run(self, tmp_path):
        raml = parsed(self.UNUSED, tmp_path)
        linter = Linter(builtin_registry())
        assert [f.rule for f in linter.measure(raml).findings] == [f.rule for f in linter.run(raml)]

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
