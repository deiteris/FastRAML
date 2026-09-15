"""Renderers for findings and for what producing them cost.

Presentation only. Nothing here decides which findings exist, what they mean or
how severe they are — that is `engine.py` applying `config.py`. A renderer that
dropped or re-graded a finding would be a second policy layer in the one place
nobody would look for it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastraml.views.lint.engine import Severity, sorted_by_rule, worst

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastraml.views.lint.engine import Finding, LintMetrics

__all__ = ['render_findings', 'render_metrics']


def render_findings(findings: Sequence[Finding], format_: str) -> str:
    """One report, in the format the CLI was asked for.

    `text` is the fallback rather than a named branch: an unknown format is the
    CLI's to reject, and `argparse` already does with a `choices` list.
    """
    if format_ == 'json':
        counts = {str(severity): sum(f.severity is severity for f in findings) for severity in Severity}
        return json.dumps({'findings': [finding.to_dict() for finding in findings], 'counts': counts}, indent=2) + '\n'
    if format_ == 'summary':
        lines = ['severity  rule                         findings']
        for rule, grouped in sorted_by_rule(findings):
            lines.append(f'{worst(grouped) or Severity.INFO:<9} {rule:<28} {len(grouped)}')
        lines.append(f'total                                      {len(findings)}')
        return '\n'.join(lines) + '\n'
    return ''.join(
        f'{finding.where}: {finding.severity}: {finding.rule}: {finding.rendered_message()}\n' for finding in findings
    )


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
