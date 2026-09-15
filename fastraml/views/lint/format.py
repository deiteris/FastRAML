"""Renderers for findings and for what producing them cost.

Presentation only. Nothing here decides which findings exist, what they mean or
how severe they are — that is `engine.py` applying `config.py`. A renderer that
dropped or re-graded a finding would be a second policy layer in the one place
nobody would look for it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastraml.views.lint.engine import LintReport, Severity, limit_findings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastraml.views.lint.engine import Finding, LintMetrics

__all__ = ['render_findings', 'render_metrics']

_RESET = '\x1b[0m'
_RED = '\x1b[31m'
_YELLOW = '\x1b[33m'
_BLUE = '\x1b[34m'
_UNDERLINE = '\x1b[4m'


def _styled(text: str, style: str, *, color: bool) -> str:
    return f'{style}{text}{_RESET}' if color else text


def _count(count: int, noun: str) -> str:
    return f'{count} {noun if count == 1 else noun + "s"}'


def _render_human(report: LintReport, *, color: bool) -> str:
    """A terminal report grouped for scanning, in Vale's compact style."""
    groups: dict[str, list[Finding]] = {}
    for finding in report.findings:
        groups.setdefault(finding.location, []).append(finding)

    lines: list[str] = []
    styles = {Severity.ERROR: _RED, Severity.WARNING: _YELLOW, Severity.INFO: _BLUE}
    for location, findings in groups.items():
        if lines:
            lines.append('')
        lines.extend((f' {_styled(location, _UNDERLINE, color=color)}', ''))
        width = max((len(str(finding.position)) if finding.position.is_known else 1) for finding in findings)
        for finding in findings:
            position = str(finding.position) if finding.position.is_known else '-'
            severity = _styled(f'{finding.severity:<7}', styles[finding.severity], color=color)
            lines.append(f' {position:<{width}}  {severity}  {finding.rendered_message()}  {finding.rule}')

    if lines:
        lines.append('')
    if report.truncated:
        omitted = (
            f'... {report.omitted_findings} findings omitted ({len(report.findings)} of {report.total_findings} shown)'
        )
        lines.extend((omitted, ''))

    errors = report.severity_counts.get(Severity.ERROR, 0)
    warnings = report.severity_counts.get(Severity.WARNING, 0)
    infos = report.severity_counts.get(Severity.INFO, 0)
    status = 'FAIL' if errors or warnings else 'OK'
    error_text = _styled(_count(errors, 'error'), _RED, color=color)
    warning_text = _styled(_count(warnings, 'warning'), _YELLOW, color=color)
    info_text = _styled(f'{infos} info {"finding" if infos == 1 else "findings"}', _BLUE, color=color)
    lines.append(f'{status} {error_text}, {warning_text} and {info_text}.')
    return '\n'.join(lines) + '\n'


def render_findings(findings: Sequence[Finding] | LintReport, format_: str, *, color: bool = False) -> str:
    """One report, in the format the CLI was asked for.

    `text` is the fallback rather than a named branch: an unknown format is the
    CLI's to reject, and `argparse` already does with a `choices` list.
    """
    report = findings if isinstance(findings, LintReport) else limit_findings(findings)
    shown = report.findings
    if format_ == 'human':
        return _render_human(report, color=color)
    if format_ == 'json':
        counts = {str(severity): report.severity_counts.get(severity, 0) for severity in Severity}
        shown_counts = {str(severity): sum(f.severity is severity for f in shown) for severity in Severity}
        shown_by_rule: dict[str, int] = {}
        for finding in shown:
            shown_by_rule[finding.rule] = shown_by_rule.get(finding.rule, 0) + 1
        omitted_by_rule = {
            rule: count - shown_by_rule.get(rule, 0)
            for rule, count in report.rule_counts.items()
            if count > shown_by_rule.get(rule, 0)
        }
        return (
            json.dumps(
                {
                    'schemaVersion': 1,
                    'findings': [finding.to_dict() for finding in shown],
                    'counts': counts,
                    'shownCounts': shown_counts,
                    'total': report.total_findings,
                    'shown': len(shown),
                    'truncated': report.truncated,
                    'omittedByRule': omitted_by_rule,
                },
                indent=2,
            )
            + '\n'
        )
    if format_ == 'summary':
        lines = ['severity  rule                         findings']
        rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        rules = sorted(
            report.rule_counts,
            key=lambda rule: (rank[report.rule_severities[rule]], -report.rule_counts[rule], rule),
        )
        lines.extend(f'{report.rule_severities[rule]:<9} {rule:<28} {report.rule_counts[rule]}' for rule in rules)
        lines.append(f'total                                      {report.total_findings}')
        if report.truncated:
            lines.append(f'shown                                      {len(shown)}')
        return '\n'.join(lines) + '\n'
    text = ''.join(
        f'{str(finding.severity).upper()} {finding.rule} {finding.where} {finding.rendered_message()}\n'
        for finding in shown
    )
    if report.truncated:
        count_fields = ' '.join(f'{severity}={report.severity_counts.get(severity, 0)}' for severity in Severity)
        text += (
            f'SUMMARY {count_fields} shown={len(shown)} total={report.total_findings} '
            f'omitted={report.omitted_findings} truncated=true\n'
        )
    return text


def _ms(nanoseconds: int | None) -> str:
    """A duration, or `-` where one was not measured.

    Not `0.000`: a graph the caller supplied was not timed by this run, and
    printing a zero would say it was free (`GraphMetric`).
    """
    return '-' if nanoseconds is None else f'{nanoseconds / 1_000_000:.3f}'


def render_metrics(metrics: LintMetrics, format_: str) -> str:
    """What the run cost, per rule and per provider — docs/18 § 7.1.

    Read top-down: the graph line first, because it is normally the largest
    single cost in the run and a reader who skips it will draw the wrong
    conclusion from the rule table below it.
    """
    if format_ == 'json':
        return json.dumps(metrics.to_dict(), indent=2) + '\n'

    graph = metrics.graph
    lines = [
        f'graph      {_ms(graph.nanoseconds):>10} ms  {graph.nodes} nodes, {graph.edges} edges ({graph.source})',
        (
            f'rules      {_ms(sum(rule.nanoseconds for rule in metrics.rules)):>10} ms  '
            f'{len(metrics.rules)} enabled, {metrics.fanout_calls} calls, {metrics.produced_findings} produced'
        ),
        f'engine     {_ms(metrics.engine_nanoseconds):>10} ms  dispatch, filters and sort',
        f'total      {_ms(metrics.nanoseconds):>10} ms',
        '',
        f'{"ms":>10}  {"calls":>7}  {"found":>6}  {"kind":<8}  rule',
    ]
    lines += [
        f'{_ms(rule.nanoseconds):>10}  {rule.calls:>7}  {rule.findings:>6}  {rule.kind:<8}  {rule.id}'
        for rule in metrics.rules
    ]
    # Only worth a section when more than one provider contributed: with just
    # the built-ins it restates the `rules` line above it.
    if len(metrics.plugins) > 1:
        lines += ['', f'{"ms":>10}  {"calls":>7}  {"found":>6}  {"rules":>5}  provider']
        lines += [
            f'{_ms(plugin.nanoseconds):>10}  {plugin.calls:>7}  {plugin.findings:>6}  '
            f'{len(plugin.rules):>5}  {plugin.name} ({plugin.source})'
            for plugin in metrics.plugins
        ]
    return '\n'.join(lines) + '\n'
