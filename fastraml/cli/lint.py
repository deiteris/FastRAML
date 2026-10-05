"""`lint`: the effective document against lint rules (docs/18)."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, emit_document, fail, parse_or_report, rule_overrides

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

    from fastraml.views.lint import Config as LintConfig
    from fastraml.views.lint import Registry as LintRegistry


def _lint(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0912 - command failures return at their source
    from pathlib import Path  # noqa: PLC0415 - lint's display root only

    from fastraml.uris import path_to_file_uri  # noqa: PLC0415
    from fastraml.views.lint import (  # noqa: PLC0415
        Linter,
        at_least,
        builtin_registry,
        decode_config,
        discover_plugins,
        limit_findings,
        parse_severity,
        render_findings,
        render_metrics,
    )

    registry = builtin_registry()
    if args.max_findings < 0 or args.max_findings_per_rule < 0:
        return fail('lint: finding limits must be non-negative')
    try:
        config = decode_config(args.fastraml_config.lint, registry, plugins=discover_plugins(registry))
    except (OSError, TypeError, ValueError) as err:
        return fail(f'lint config: {err}')
    try:
        config = _lint_rule_overrides(_lint_rulesets(config, args.ruleset, registry), args.rule, registry)
    except ValueError as err:
        return fail(f'lint: {err}')

    if args.list_rules:
        rows = [
            f'{rule.meta.id:<34} {rule.meta.category:<9} {rule.meta.severity:<7} {registry.source_of(rule.meta.id)}'
            for rule in registry.all()
        ]
        return emit_document(args, '\n'.join(rows) + ('\n' if rows else ''))
    if args.explain:
        rule = registry.get(args.explain)
        if rule is None:
            return fail(f'{args.explain}: no such lint rule')
        meta = rule.meta
        text = f'{meta.id} [{meta.category}, {meta.severity}]\n\n{meta.summary}\n\n{meta.rationale}\n'
        if meta.references:
            text += '\nReferences:\n\n' + ''.join(f'- {reference}\n' for reference in meta.references)
        for name, source in meta.files:
            text += f'\n{name}:\n\n{source}'
        if meta.good:
            text += f'\nGood:\n\n{meta.good}'
        if meta.bad:
            text += f'\nBad:\n\n{meta.bad}'
        return emit_document(args, text if text.endswith('\n') else text + '\n')
    if not args.files:
        return fail('lint: at least one FILE is required unless --list-rules or --explain is used')

    linter = Linter(registry, config)
    findings = []
    failed = False
    for path in args.files:
        raml = parse_or_report(args, path, retain_source=True)
        if raml is None:
            failed = True
            continue
        if not args.metrics:
            findings.extend(linter.run(raml))
            continue
        # Metrics per file, on stderr, so stdout stays exactly the findings
        # report (docs/18 § 5.2).
        run = linter.measure(raml)
        findings.extend(run.findings)
        print(f'== {path}', file=sys.stderr)
        print(render_metrics(run.metrics, args.format), end='', file=sys.stderr)
    visible = at_least(parse_severity(args.severity))
    shown = [finding for finding in findings if finding.severity in visible]
    report = limit_findings(
        shown,
        max_findings=args.max_findings or None,
        max_findings_per_rule=args.max_findings_per_rule or None,
    )
    failing = at_least(parse_severity(args.fail_on))
    failed = failed or any(finding.severity in failing for finding in findings)
    color = (
        args.format == 'human'
        and not args.no_color
        and args.output is None
        and 'NO_COLOR' not in os.environ
        and sys.stdout.isatty()
    )
    root = path_to_file_uri(Path.cwd()).rstrip('/') + '/'
    emitted = emit_document(args, render_findings(report, args.format, color=color, failed=failed, root=root))
    return EXIT_INVALID if failed or emitted == EXIT_INVALID else EXIT_OK


def _lint_rulesets(config: LintConfig, names: Sequence[str], registry: LintRegistry) -> LintConfig:
    """Add repeatable `--ruleset NAME` entries to the configured `extends`.

    Rulesets only switch rules on, so configured categories and rules still
    apply after them, as they do to `extends` (docs/18 § 3).
    """
    available = registry.sets()
    for name in names:
        if name not in available:
            raise ValueError(f'unknown ruleset: {name} (available: {", ".join(available)})')
    extends = tuple(dict.fromkeys((*config.extends, *names)))
    return replace(config, extends=extends)


def _lint_rule_overrides(config: LintConfig, values: Sequence[str], registry: LintRegistry) -> LintConfig:
    """Apply repeatable `--rule ID[=SEVERITY|off]` entries after file config."""
    from fastraml.views.lint import RuleSetting, parse_severity  # noqa: PLC0415

    rules = list(config.rules)
    for _, rule_id, action in rule_overrides(values, 'rule'):
        if registry.get(rule_id) is None:
            raise ValueError(f'unknown rule: {rule_id}')
        plugin = registry.plugin_of(rule_id)
        if plugin and plugin not in config.plugins:
            raise ValueError(f'rule {rule_id!r} requires lint plugin {plugin!r} in the config')

        disabled = action == 'off'
        severity = None if action is None or disabled else parse_severity(action)
        existing_index = next(
            (index for index, setting in enumerate(rules) if setting.id == rule_id and setting.match is None),
            None,
        )
        existing = rules[existing_index] if existing_index is not None else None
        setting = RuleSetting(
            id=rule_id,
            severity=severity if severity is not None else (existing.severity if existing else None),
            disabled=disabled,
            options=existing.options if existing else {},
        )
        if existing_index is None:
            rules.append(setting)
        else:
            rules[existing_index] = setting
    return replace(config, rules=tuple(rules))
