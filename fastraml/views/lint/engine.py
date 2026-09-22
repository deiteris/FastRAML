"""The rule engine — docs/18-linting.md §§ 2, 3, 5, 7.1.

Spec-agnostic in the sense that matters: nothing here knows a RAML rule, or any
rule at all. It holds the vocabulary a judgement is stated in (`RuleMeta`,
`Finding`, `Severity`), the two shapes a rule can take, the registry that names
them, the configuration that enables and grades them, and the driver that fans
one loop over the completed graph's nodes out to every visitor.

The rules themselves are in `rules/`. That boundary is load-bearing: a rule is
data plus a small function, and everything about *running* rules is here, so a
plugin (§ 6) is on exactly the same footing as a built-in.

Nothing here decides a RAML rule. It runs after P10 and imports the model
rather than being imported by it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter_ns
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol, cast

from fastraml.nodes import (
    ApiNode,
    EndPointNode,
    GraphNode,
    OperationNode,
    ParameterNode,
    PatternPropertyNode,
    PayloadNode,
    PropertyNode,
    RequestNode,
    ResourceTypeNode,
    ResponseNode,
    SecuritySchemeNode,
    TraitNode,
    TypeNode,
    UnitNode,
    UnresolvedNode,
)
from fastraml.positions import UNKNOWN, Position
from fastraml.views.graph import build_graph
from fastraml.views.lint.source import SuppressionIndex
from fastraml.views.severity import Ranking

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.registry import Raml
    from fastraml.views.graph import Graph

__all__ = [
    'DEFAULT_MAX_FINDINGS',
    'DEFAULT_MAX_FINDINGS_PER_RULE',
    'Category',
    'Context',
    'DocumentRule',
    'Finding',
    'GraphMetric',
    'LintMetrics',
    'LintReport',
    'LintRun',
    'Linter',
    'PluginMetric',
    'Registry',
    'Rule',
    'RuleMeta',
    'RuleMetric',
    'RuleSetting',
    'Severity',
    'VisitorRule',
    'limit_findings',
]


class Severity(StrEnum):
    """How much a finding matters. Only `ERROR` affects the exit code."""

    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


#: Worst first, for sorting and for `--severity` to mean "this and worse".
#: The arithmetic is shared with `backward`, which grades on a different axis with
#: the same operations (`views/severity.py`).
_RANK: Final[Ranking[Severity]] = Ranking((Severity.ERROR, Severity.WARNING, Severity.INFO))
DEFAULT_MAX_FINDINGS: Final = 1000
DEFAULT_MAX_FINDINGS_PER_RULE: Final = 100

#: Spellings a config file may use. `warn` and `hint` are what people type.
_ALIASES: Final[dict[str, Severity]] = {
    'error': Severity.ERROR,
    'warn': Severity.WARNING,
    'warning': Severity.WARNING,
    'hint': Severity.INFO,
    'info': Severity.INFO,
}
_RULE_ID: Final = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def parse_severity(value: str) -> Severity:
    """A configured severity, by any of its accepted spellings."""
    found = _ALIASES.get(value.strip().lower())
    if found is None:
        raise ValueError(f'unknown severity: {value!r}')
    return found


class Category(StrEnum):
    """What kind of judgement a rule makes — docs/18 § 1.

    Not a taxonomy of subject matter. `SPEC` and `SECURITY` are the two
    provenance groups that ship here, and `STYLE` exists so a plugin's rules
    land somewhere that is visibly not either of them.
    """

    #: Derived from RAML's own semantics (§ 1 group 1).
    SPEC = 'spec'
    #: Derived from a published standard, off by default (§ 1 group 2).
    SECURITY = 'security'
    #: Taste. No built-in is in this category (§ 1 group 3).
    STYLE = 'style'


@dataclass(frozen=True, slots=True)
class RuleMeta:
    """One rule's identity and its documentation, in one object.

    `good` and `bad` are RAML the suite actually parses: a rule must be silent
    on the first and fire on the second (docs/18 § 2.1). That makes the
    documentation a test rather than a comment, which is the lesson docs/16
    § 6.2 paid for.
    """

    id: str
    category: Category
    summary: str
    rationale: str
    severity: Severity
    good: str = ''
    bad: str = ''


@dataclass(frozen=True, slots=True)
class Finding:
    """One judgement at one place.

    Not a `RamlError`: no chain, no exception, and a severity that is not
    always `error` (docs/18 § 2). `info` carries the variables, so a test
    asserts on the rule and the dict rather than on assembled text
    (docs/11 § 6).
    """

    rule: str
    severity: Severity
    message: str
    location: str
    position: Position = UNKNOWN
    #: The node this is about, so a reader can `fastraml show` it.
    iri: str = ''
    info: dict[str, object] = field(default_factory=dict)

    def rendered_message(self) -> str:
        if not self.info:
            return self.message
        return f'{self.message}: ' + ': '.join(f'{k}: {v}' for k, v in self.info.items())

    @property
    def where(self) -> str:
        return f'{self.location}:{self.position}' if self.position.is_known else self.location

    def to_dict(self) -> dict[str, Any]:
        known = self.position.is_known
        return {
            'rule': self.rule,
            'severity': str(self.severity),
            'message': self.rendered_message(),
            'location': self.location,
            'position': str(self.position) if known else '',
            'line': self.position.line if known else None,
            'column': self.position.column if known else None,
            'endLine': self.position.end_line or None if known else None,
            'endColumn': self.position.end_column or None if known else None,
            'iri': self.iri,
            'info': dict(self.info),
        }

    def __repr__(self) -> str:
        return f'Finding({self.rule!r}, {self.severity}, {self.where})'


def _milliseconds(nanoseconds: int | None) -> float | None:
    return None if nanoseconds is None else round(nanoseconds / 1_000_000, 3)


@dataclass(slots=True, eq=False)
class _Tally:
    """One rule's running totals while a measured run is in flight.

    Mutable and private: every public metric below is frozen, and this exists
    only so the hot path increments three integers rather than rebuilding a
    frozen dataclass per call.
    """

    nanoseconds: int = 0
    calls: int = 0
    findings: int = 0


@dataclass(frozen=True, slots=True)
class RuleMetric:
    """What one rule cost, and what it produced.

    `calls` is the number of times the rule was invoked: once for a document
    rule, and once per projected node of a role it implements for a visitor. A
    visitor with a high `calls` and a low `ms` is doing the right thing; the
    pair is what tells a slow rule apart from a merely busy one.

    `findings` counts what the rule *produced*, before the config's severity
    overrides and `match:` filters ran. It is therefore >= the number that
    reaches the report, and deliberately so: a rule whose findings are all
    being filtered away is still paying for them, and that is worth seeing.
    """

    id: str
    kind: str
    plugin: str
    source: str
    nanoseconds: int
    calls: int
    findings: int

    def to_dict(self) -> dict[str, object]:
        return {
            'id': self.id,
            'kind': self.kind,
            'plugin': self.plugin or None,
            'source': self.source,
            'ms': _milliseconds(self.nanoseconds),
            'calls': self.calls,
            'findings': self.findings,
        }


@dataclass(frozen=True, slots=True)
class PluginMetric:
    """One provider's rules, totalled — docs/18 § 7.1.

    The question this answers is the one an entry-point mechanism creates: a
    plugin is installed by a package a project may not have chosen directly
    (§ 6), so when a lint run gets slow, "which distribution is it" has to be
    answerable without reading anyone's source. Built-ins aggregate under
    `fastraml` like any other provider, so the comparison is like for like.
    """

    name: str
    source: str
    rules: tuple[str, ...]
    nanoseconds: int
    calls: int
    findings: int

    def to_dict(self) -> dict[str, object]:
        return {
            'name': self.name,
            'source': self.source,
            'rules': list(self.rules),
            'ms': _milliseconds(self.nanoseconds),
            'calls': self.calls,
            'findings': self.findings,
        }


@dataclass(frozen=True, slots=True)
class GraphMetric:
    """Building the projection every rule reads.

    `nanoseconds` is `None` when the caller supplied a graph rather than
    letting the run build one — the CLI does, so that `lint` and another verb
    share one. Reporting `0` there would read as "free" when the true answer is
    "not measured here", and the graph is the single largest cost in the run
    (docs/18 § 4): 545 ms of the `bench_endpoints` pipeline against single-digit
    milliseconds for every rule put together.
    """

    source: str
    nanoseconds: int | None
    nodes: int
    edges: int

    def to_dict(self) -> dict[str, object]:
        return {
            'source': self.source,
            'ms': _milliseconds(self.nanoseconds),
            'nodes': self.nodes,
            'edges': self.edges,
        }


@dataclass(frozen=True, slots=True)
class LintMetrics:
    """One measured run — docs/18 § 7.1.

    `engine_nanoseconds` is what the run cost *minus* the graph and every rule:
    the fan-out dispatch, the severity overrides, the `match:` filters and the
    sort. It is a residual rather than its own measurement, so it absorbs the
    timing overhead too — which is the honest place to put it, because
    instrumenting the rules is what created it.
    """

    nanoseconds: int
    graph: GraphMetric
    #: Slowest first. A metrics report is read from the top.
    rules: tuple[RuleMetric, ...]
    plugins: tuple[PluginMetric, ...]
    engine_nanoseconds: int
    fanout_calls: int
    #: Findings the rules produced, before filtering. See `RuleMetric.findings`.
    produced_findings: int

    def to_dict(self) -> dict[str, object]:
        return {
            'total_ms': _milliseconds(self.nanoseconds),
            'graph': self.graph.to_dict(),
            'rules': [rule.to_dict() for rule in self.rules],
            'plugins': [plugin.to_dict() for plugin in self.plugins],
            'engine_overhead_ms': _milliseconds(self.engine_nanoseconds),
            'fanout_calls': self.fanout_calls,
            'produced_findings': self.produced_findings,
        }


@dataclass(frozen=True, slots=True)
class LintReport:
    """A possibly bounded finding set, with totals from the complete run."""

    findings: list[Finding]
    total_findings: int
    severity_counts: Mapping[Severity, int]
    rule_counts: Mapping[str, int]
    rule_severities: Mapping[str, Severity]

    @property
    def omitted_findings(self) -> int:
        return self.total_findings - len(self.findings)

    @property
    def truncated(self) -> bool:
        return self.omitted_findings > 0


def limit_findings(
    findings: Sequence[Finding],
    *,
    max_findings: int | None = None,
    max_findings_per_rule: int | None = None,
) -> LintReport:
    """Bound a sorted report while preserving totals for omitted findings.

    Selection is worst severity first, so a bound can never show an info finding
    in place of an error or warning; the survivors keep their input order. The
    per-rule bound counts per source file, so one file cannot spend a rule's
    whole allowance and hide it in the rest (docs/18 § 7).
    """
    for name, value in (('max_findings', max_findings), ('max_findings_per_rule', max_findings_per_rule)):
        if value is not None and value < 1:
            raise ValueError(f'{name} must be positive or None')

    severity_counts = dict.fromkeys(Severity, 0)
    rule_counts: dict[str, int] = {}
    rule_severities: dict[str, Severity] = {}
    for finding in findings:
        severity_counts[finding.severity] += 1
        rule_counts[finding.rule] = rule_counts.get(finding.rule, 0) + 1
        previous = rule_severities.get(finding.rule)
        if previous is None or _RANK.rank(finding.severity) < _RANK.rank(previous):
            rule_severities[finding.rule] = finding.severity

    if max_findings is None and max_findings_per_rule is None:
        shown = list(findings)
    else:
        # A stable sort: within one severity, the input's reading order decides.
        by_severity = sorted(range(len(findings)), key=lambda index: _RANK.rank(findings[index].severity))
        selected: list[int] = []
        per_rule: dict[tuple[str, str], int] = {}
        for index in by_severity:
            if max_findings is not None and len(selected) >= max_findings:
                break
            finding = findings[index]
            key = (finding.location, finding.rule)
            if max_findings_per_rule is not None and per_rule.get(key, 0) >= max_findings_per_rule:
                continue
            selected.append(index)
            per_rule[key] = per_rule.get(key, 0) + 1
        shown = [findings[index] for index in sorted(selected)]
    return LintReport(
        findings=shown,
        total_findings=len(findings),
        severity_counts=severity_counts,
        rule_counts=rule_counts,
        rule_severities=rule_severities,
    )


@dataclass(frozen=True, slots=True)
class LintRun:
    """What `Linter.measure` returns: the report, and what it cost to produce.

    `Linter.run` returns the findings alone, because that is what every caller
    but a profiler wants and because measuring is not free (§ 7.1).
    """

    findings: list[Finding]
    metrics: LintMetrics
    total_findings: int
    severity_counts: Mapping[Severity, int]
    rule_counts: Mapping[str, int]
    rule_severities: Mapping[str, Severity]

    @property
    def omitted_findings(self) -> int:
        return self.total_findings - len(self.findings)

    @property
    def truncated(self) -> bool:
        return self.omitted_findings > 0

    def report(self) -> LintReport:
        return LintReport(
            findings=self.findings,
            total_findings=self.total_findings,
            severity_counts=self.severity_counts,
            rule_counts=self.rule_counts,
            rule_severities=self.rule_severities,
        )


#: What an unmeasured run reports: every counter zero and no rows. `run` unwraps
#: the `LintRun` before a caller sees it, so this is only reachable by someone
#: calling `_execute` directly, and a shared immutable is cheaper than building
#: an empty one per run.
_NO_METRICS: Final = LintMetrics(
    nanoseconds=0,
    graph=GraphMetric(source='unmeasured', nanoseconds=None, nodes=0, edges=0),
    rules=(),
    plugins=(),
    engine_nanoseconds=0,
    fanout_calls=0,
    produced_findings=0,
)


@dataclass(frozen=True, slots=True)
class Context:
    """What every rule is handed.

    `graph` is built once for the whole run. A rule that only visits never
    touches it, and building it lazily was rejected: a `DocumentRule` would then
    pay for it inside its own timing, which makes a slow rule look like a slow
    graph.
    """

    raml: Raml
    graph: Graph
    #: This rule's configured options. Empty for every built-in today; the slot
    #: exists because a plugin rule with a threshold has nowhere else to read
    #: one from, and adding it later would change every rule signature.
    options: Mapping[str, object] = field(default_factory=dict)

    def at(
        self,
        rule: RuleMeta,
        message: str,
        *,
        location: str,
        position: Position = UNKNOWN,
        iri: str = '',
        **info: object,
    ) -> Finding:
        """Build a finding at this rule's configured severity.

        The severity is the rule's default here; `Linter` applies the config's
        override afterwards, in one place, so a rule never has to consult it.
        """
        return Finding(
            rule=rule.id,
            severity=rule.severity,
            message=message,
            location=location,
            position=position,
            iri=iri,
            info=info,
        )


class VisitorRule(Protocol):
    """A subset of `views.walk.Sink`, each method returning findings.

    One loop over the completed graph's nodes is fanned out to every rule of
    this shape, so a rule costs its own body and nothing else. Implement only
    the roles you care about.
    """

    meta: ClassVar[RuleMeta]


class DocumentRule(Protocol):
    """For what cannot be judged one node at a time: counting, and walking back."""

    meta: ClassVar[RuleMeta]

    def run(self, ctx: Context) -> Iterable[Finding]: ...


type Rule = VisitorRule | DocumentRule

#: Every `Sink` role a `VisitorRule` may implement. Kept as an explicit tuple
#: rather than read off the protocol: `Sink` gains a method when the walk
#: reaches something new, and a rule engine that silently started dispatching a
#: new role would change every plugin's behaviour without anyone saying so.
VISITS: Final = (
    'unit',
    'api',
    'type_',
    'property_',
    'pattern_property',
    'parameter',
    'payload',
    'request',
    'response',
    'operation',
    'endpoint',
    'trait',
    'resource_type',
    'security_scheme',
)

_NODE_ROLES: Final[dict[type[GraphNode[Any]], str]] = {
    UnitNode: 'unit',
    ApiNode: 'api',
    TypeNode: 'type_',
    PropertyNode: 'property_',
    PatternPropertyNode: 'pattern_property',
    ParameterNode: 'parameter',
    PayloadNode: 'payload',
    RequestNode: 'request',
    ResponseNode: 'response',
    OperationNode: 'operation',
    EndPointNode: 'endpoint',
    TraitNode: 'trait',
    ResourceTypeNode: 'resource_type',
    SecuritySchemeNode: 'security_scheme',
}


# -- configuration -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleSetting:
    """One `rules:` entry — docs/18 § 5.

    `match` is a regex over the finding's rendered message. With it, the entry
    filters findings; without it, the entry configures the rule itself. That is
    the whole of the difference, and it is what lets a project silence three
    noisy findings without giving up the rule that produced the other forty.
    """

    id: str
    severity: Severity | None = None
    disabled: bool | None = None
    match: re.Pattern[str] | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Config:
    """A whole lint configuration. Built from YAML by `config.py`."""

    extends: tuple[str, ...] = ('recommended',)
    plugins: tuple[str, ...] = ()
    categories: Mapping[str, RuleSetting] = field(default_factory=dict)
    rules: tuple[RuleSetting, ...] = ()


# -- the registry --------------------------------------------------------------


class Registry:
    """Rules by id, and the named sets that enable them.

    Insertion order is preserved and is the order `--list-rules` prints, but it
    is *not* the order rules run in: the graph's node order decides that for
    visitors, and document rules run in id order so a run is reproducible.
    """

    __slots__ = ('_documents', '_plugins', '_rules', '_sets', '_sources')

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}
        self._sets: dict[str, list[str]] = {}
        self._sources: dict[str, str] = {}
        self._plugins: dict[str, str] = {}
        self._documents: set[str] = set()

    def __repr__(self) -> str:
        return f'<Registry rules={len(self._rules)} sets={sorted(self._sets)}>'

    def add(
        self,
        rule: Rule,
        *,
        sets: Sequence[str] = (),
        source: str = 'fastraml',
        plugin: str = '',
    ) -> None:
        """Register one rule, and put it in the named sets.

        A duplicate id is refused rather than overwritten. Two rules answering
        to one name is how a plugin silently replaces a built-in, and the
        failure mode — a judgement quietly changing meaning — is worse than an
        install that fails loudly.
        """
        rule_id = rule.meta.id
        if _RULE_ID.fullmatch(rule_id) is None:
            raise ValueError(f'invalid rule id: {rule_id!r}')
        if rule_id in self._rules:
            raise ValueError(f'duplicate rule id: {rule_id}')
        is_document = callable(getattr(rule, 'run', None))
        is_visitor = any(callable(getattr(rule, role, None)) for role in VISITS)
        if is_document == is_visitor:
            raise TypeError(f'rule {rule_id!r} must be exactly one of visitor or document rule')
        self._rules[rule_id] = rule
        self._sources[rule_id] = source
        self._plugins[rule_id] = plugin
        if is_document:
            self._documents.add(rule_id)
        for name in sets:
            self._sets.setdefault(name, []).append(rule_id)

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def all(self) -> list[Rule]:
        return list(self._rules.values())

    def source_of(self, rule_id: str) -> str:
        return self._sources.get(rule_id, '')

    def plugin_of(self, rule_id: str) -> str:
        return self._plugins.get(rule_id, '')

    def is_document(self, rule_id: str) -> bool:
        return rule_id in self._documents

    def ids_in(self, name: str) -> list[str]:
        """The rule ids one set enables. `all` is every rule registered."""
        if name == 'all':
            return list(self._rules)
        return list(self._sets.get(name, ()))

    def sets(self) -> list[str]:
        return sorted({'all', *self._sets})

    def categories(self) -> list[str]:
        return sorted({str(rule.meta.category) for rule in self._rules.values()})


# -- the driver ----------------------------------------------------------------


class _FanOut:
    """Forward each projected node to the rules implementing its role.

    Built once per run. The per-role lists are resolved up front so the hot
    path is a list iteration rather than a `getattr` per node per rule — the
    same reason `nodes.py` binds `attributes` to a local before reading it
    seven times (docs/16 § 2.8).
    """

    __slots__ = ('_by_role', 'ctx', 'findings', 'tallies')

    def __init__(self, ctx: Context, rules: Sequence[VisitorRule], *, measure: bool = False) -> None:
        self.ctx = ctx
        self.findings: list[Finding] = []
        #: Per rule id, or `None` when this run is not measured.
        self.tallies: dict[str, _Tally] | None = {} if measure else None
        self._by_role: dict[str, list[tuple[Any, Context]]] = {}
        for rule in rules:
            options = cast('Mapping[str, object]', ctx.options.get(rule.meta.id, {}))
            rule_ctx = Context(raml=ctx.raml, graph=ctx.graph, options=options)
            for role in VISITS:
                method = getattr(rule, role, None)
                if method is None:
                    continue
                if self.tallies is not None:
                    method = self._instrument(rule.meta.id, method)
                self._by_role.setdefault(role, []).append((method, rule_ctx))

    def _instrument(self, rule_id: str, method: Any) -> Any:
        """`method` wrapped so its time and its output are attributed to `rule_id`.

        Wrapped **once, at construction**, so the unmeasured path pays nothing
        at all: `_fan` below calls whatever is in the list and has no branch of
        its own. Instrumenting inside `_fan` would put a test per rule per node
        on the hot loop of every ordinary run, which is the shape docs/12 § 12
        rules out.

        The result is materialised inside the timed region. It has to be: a
        rule may return a generator — every rule in `rules/document.py` does —
        and timing the bare call would then measure building the generator and
        none of the work, reporting zero for the rules most likely to be slow.
        """
        tallies = self.tallies
        assert tallies is not None  # noqa: S101 - only called when measuring; narrows for mypy
        tally = tallies.setdefault(rule_id, _Tally())

        def timed(ctx: Context, *args: object) -> tuple[Finding, ...]:
            start = perf_counter_ns()
            found = tuple(method(ctx, *args))
            tally.nanoseconds += perf_counter_ns() - start
            tally.calls += 1
            tally.findings += len(found)
            return found

        return timed

    def _fan(self, role: str, *args: object) -> None:
        for method, ctx in self._by_role.get(role, ()):
            found = method(ctx, *args)
            if found:
                self.findings.extend(found)

    def run(self, graph: Graph) -> None:
        """Visit every projected entity once; edges stay on `graph`."""
        for iri, node in graph.nodes.items():
            if isinstance(node, UnresolvedNode):
                raise RuntimeError(  # noqa: TRY004 - the node type is valid; its presence violates pipeline state
                    f'unresolved reference reached lint graph at {iri}: {node.entity.name}'
                )
            role = _NODE_ROLES.get(type(node))
            if role is None:
                raise RuntimeError(f'lint has no visitor role for graph node: {type(node).__name__}')
            if isinstance(node, TypeNode):
                self._fan(role, iri, node.entity, node.shape_kind)
            else:
                self._fan(role, iri, node.entity)


class Linter:
    """One configured rule set, ready to run over any number of documents."""

    __slots__ = ('_enabled', '_filters', '_settings', 'config', 'registry')

    def __init__(self, registry: Registry, config: Config | None = None) -> None:
        self.registry = registry
        self.config = config or Config()
        self._settings: dict[str, RuleSetting] = {}
        for entry in self.config.rules:
            if entry.match is not None:
                continue
            if entry.id in self._settings:
                raise ValueError(f'duplicate rule setting: {entry.id}')
            self._settings[entry.id] = entry
        self._filters = tuple(entry for entry in self.config.rules if entry.match is not None)
        self._enabled = self._resolve_enabled()

    def __repr__(self) -> str:
        return f'<Linter rules={len(self._enabled)}>'

    def _plugin_enabled(self, rule_id: str) -> bool:
        plugin = self.registry.plugin_of(rule_id)
        return not plugin or plugin in self.config.plugins

    def _apply_categories(self, status: dict[str, bool]) -> None:
        for rule in self.registry.all():
            if not self._plugin_enabled(rule.meta.id):
                continue
            setting = self.config.categories.get(str(rule.meta.category))
            if setting is not None and setting.disabled is not None:
                status[rule.meta.id] = not setting.disabled

    def _resolve_enabled(self) -> list[Rule]:
        """Which rules run, applying ruleset then category then rule.

        Sorted by id, so a run is reproducible and a document rule's findings
        arrive in a stable order before sorting by position.
        """
        status: dict[str, bool] = {}
        for name in self.config.extends:
            for rule_id in self.registry.ids_in(name):
                if self._plugin_enabled(rule_id):
                    status[rule_id] = True
        self._apply_categories(status)
        for rule_id, setting in self._settings.items():
            if not self._plugin_enabled(rule_id):
                continue
            if setting.disabled is not None:
                status[rule_id] = not setting.disabled
            elif self.registry.get(rule_id) is not None:
                # Naming a rule with no `disabled:` turns it on: a project that
                # writes a severity for a rule outside its ruleset means to run
                # it, and silently ignoring the entry is the unhelpful reading.
                status[rule_id] = True
        found: list[Rule] = []
        for rule_id, on in status.items():
            found_rule = self.registry.get(rule_id)
            if on and found_rule is not None:
                found.append(found_rule)
        return sorted(found, key=lambda rule: rule.meta.id)

    def severity_of(self, rule: RuleMeta) -> Severity:
        """The effective severity: rule setting, else category, else default."""
        setting = self._settings.get(rule.id)
        if setting is not None and setting.severity is not None:
            return setting.severity
        category = self.config.categories.get(str(rule.category))
        if category is not None and category.severity is not None:
            return category.severity
        return rule.severity

    def options_for(self, rule: RuleMeta) -> Mapping[str, object]:
        setting = self._settings.get(rule.id)
        return setting.options if setting is not None else {}

    @property
    def rules(self) -> list[Rule]:
        return list(self._enabled)

    def run(self, raml: Raml, *, graph: Graph | None = None) -> list[Finding]:
        """Every finding on one parsed document, sorted for reading.

        `graph` is accepted so a caller already holding one — the CLI running
        several verbs, a test — does not build it twice.

        Unmeasured. `measure` is the same run with the clocks on (§ 7.1); this
        one pays nothing for them, which is why the two are separate entry
        points rather than one with a flag defaulting to off.
        """
        return self._execute(raml, graph=graph, measure=False, max_findings=None, max_findings_per_rule=None).findings

    def report(
        self,
        raml: Raml,
        *,
        graph: Graph | None = None,
        max_findings: int | None = DEFAULT_MAX_FINDINGS,
        max_findings_per_rule: int | None = DEFAULT_MAX_FINDINGS_PER_RULE,
    ) -> LintReport:
        """A bounded report with complete counts; `None` disables either bound."""
        return self._execute(
            raml,
            graph=graph,
            measure=False,
            max_findings=max_findings,
            max_findings_per_rule=max_findings_per_rule,
        ).report()

    def measure(
        self,
        raml: Raml,
        *,
        graph: Graph | None = None,
        max_findings: int | None = None,
        max_findings_per_rule: int | None = None,
    ) -> LintRun:
        """`run`, plus what each rule, each plugin and the graph cost.

        Instrumenting is not free — every rule's output is materialised inside
        its timed region so that a generator rule is measured for its work
        rather than for building a generator — so the timings describe a
        *measured* run and are a guide to proportions, not a benchmark. The
        proportion is the useful part, and it is stable: the graph dominates
        (§ 4).
        """
        return self._execute(
            raml,
            graph=graph,
            measure=True,
            max_findings=max_findings,
            max_findings_per_rule=max_findings_per_rule,
        )

    def _execute(
        self,
        raml: Raml,
        *,
        graph: Graph | None,
        measure: bool,
        max_findings: int | None,
        max_findings_per_rule: int | None,
    ) -> LintRun:
        """One run, measured or not. The single path, so the two cannot drift."""
        if not raml.is_unwrapped:
            raise RuntimeError('lint needs an unwrapped model: parse with ParseOptions(unwrap=True)')
        if any(getattr(rule, 'requires_source', False) for rule in self._enabled) and not raml.retain_source:
            raise RuntimeError('enabled lint rules need retained source: parse with ParseOptions(retain_source=True)')

        started = perf_counter_ns() if measure else 0
        if graph is None:
            graph_started = perf_counter_ns()
            graph = build_graph(raml)
            graph_ns: int | None = perf_counter_ns() - graph_started if measure else None
            graph_source = 'built'
        else:
            # Not timed rather than timed at zero: the caller built it, so this
            # run genuinely does not know what it cost (`GraphMetric`).
            graph_ns, graph_source = None, 'supplied'

        visitors: list[VisitorRule] = []
        documents: list[DocumentRule] = []
        for rule in self._enabled:
            if self.registry.is_document(rule.meta.id):
                documents.append(cast('DocumentRule', rule))
            else:
                visitors.append(cast('VisitorRule', rule))

        findings: list[Finding] = []
        tallies: dict[str, _Tally] = {}
        if visitors:
            # One flat projected-node loop shared by every visitor rule. The
            # context each sees differs only in `options`.
            options = {rule.meta.id: self.options_for(rule.meta) for rule in visitors}
            sink = _FanOut(Context(raml=raml, graph=graph, options=options), visitors, measure=measure)
            sink.run(graph)
            findings.extend(sink.findings)
            if sink.tallies is not None:
                tallies.update(sink.tallies)

        for rule in documents:
            ctx = Context(raml=raml, graph=graph, options=self.options_for(rule.meta))
            if not measure:
                findings.extend(rule.run(ctx))
                continue
            tally = tallies.setdefault(rule.meta.id, _Tally())
            start = perf_counter_ns()
            # Materialised inside the timed region: every document rule is a
            # generator, so `extend` outside it would do the work untimed.
            produced = tuple(rule.run(ctx))
            tally.nanoseconds += perf_counter_ns() - start
            tally.calls += 1
            tally.findings += len(produced)
            findings.extend(produced)

        report = limit_findings(
            self._finish(findings, raml),
            max_findings=max_findings,
            max_findings_per_rule=max_findings_per_rule,
        )
        if not measure:
            return LintRun(
                findings=report.findings,
                metrics=_NO_METRICS,
                total_findings=report.total_findings,
                severity_counts=report.severity_counts,
                rule_counts=report.rule_counts,
                rule_severities=report.rule_severities,
            )
        total = perf_counter_ns() - started
        return LintRun(
            findings=report.findings,
            metrics=self._metrics(
                tallies,
                total=total,
                graph=GraphMetric(
                    source=graph_source, nanoseconds=graph_ns, nodes=len(graph.nodes), edges=len(graph.edges)
                ),
            ),
            total_findings=report.total_findings,
            severity_counts=report.severity_counts,
            rule_counts=report.rule_counts,
            rule_severities=report.rule_severities,
        )

    def _metrics(self, tallies: Mapping[str, _Tally], *, total: int, graph: GraphMetric) -> LintMetrics:
        """Per-rule tallies rolled up per rule, per plugin and per run.

        A rule that was enabled and never invoked still gets a row, at zero.
        Omitting it would make the report answer "which rules ran" when the
        question it is read for is "which of my rules cost anything" — and a
        rule that never fires because it visits a role the document has none of
        is exactly what someone tuning a slow run wants to see.
        """
        rules: list[RuleMetric] = []
        for rule in self._enabled:
            rule_id = rule.meta.id
            tally = tallies.get(rule_id, _Tally())
            rules.append(
                RuleMetric(
                    id=rule_id,
                    kind='document' if self.registry.is_document(rule_id) else 'visitor',
                    plugin=self.registry.plugin_of(rule_id),
                    source=self.registry.source_of(rule_id),
                    nanoseconds=tally.nanoseconds,
                    calls=tally.calls,
                    findings=tally.findings,
                )
            )
        rules.sort(key=lambda metric: (-metric.nanoseconds, metric.id))

        grouped: dict[tuple[str, str], list[RuleMetric]] = {}
        for metric in rules:
            # Keyed by provider, not by plugin name alone: a built-in has no
            # plugin name and would otherwise share a bucket with any plugin
            # that also left one unset.
            grouped.setdefault((metric.plugin, metric.source), []).append(metric)
        plugins = [
            PluginMetric(
                # A built-in carries no plugin name, and an empty one in the
                # report reads as a missing value rather than as "this is the
                # parser's own". The provider is the answer in both cases.
                name=name or source,
                source=source,
                rules=tuple(metric.id for metric in members),
                nanoseconds=sum(metric.nanoseconds for metric in members),
                calls=sum(metric.calls for metric in members),
                findings=sum(metric.findings for metric in members),
            )
            for (name, source), members in grouped.items()
        ]
        plugins.sort(key=lambda metric: (-metric.nanoseconds, metric.source, metric.name))

        measured = sum(metric.nanoseconds for metric in rules) + (graph.nanoseconds or 0)
        return LintMetrics(
            nanoseconds=total,
            graph=graph,
            rules=tuple(rules),
            plugins=tuple(plugins),
            # Clamped at zero: the residual is a subtraction of clocks read at
            # different moments, and a run where the rules cost nothing
            # measurable can produce a negative by a few nanoseconds.
            engine_nanoseconds=max(total - measured, 0),
            fanout_calls=sum(metric.calls for metric in rules),
            produced_findings=sum(metric.findings for metric in rules),
        )

    def _finish(self, findings: Iterable[Finding], raml: Raml) -> list[Finding]:
        """Apply source/config suppressions and severity overrides, then sort."""
        suppressions = SuppressionIndex(raml.source_texts)
        kept = [
            graded
            for finding in findings
            if (graded := self._apply(finding)) is not None and not suppressions.suppresses(graded)
        ]
        kept.sort(key=lambda f: (f.location, f.position.line, f.position.column, f.rule))
        return kept

    def _apply(self, finding: Finding) -> Finding | None:
        """One finding, re-graded or dropped. `None` means a filter suppressed it."""
        severity = self.severity_of_id(finding.rule)
        message = finding.rendered_message()
        for entry in self._filters:
            if entry.id and entry.id != finding.rule:
                continue
            if entry.match is not None and not entry.match.search(message):
                continue
            if entry.disabled:
                return None
            if entry.severity is not None:
                severity = entry.severity
        if severity is finding.severity:
            return finding
        return Finding(
            rule=finding.rule,
            severity=severity,
            message=finding.message,
            location=finding.location,
            position=finding.position,
            iri=finding.iri,
            info=finding.info,
        )

    def severity_of_id(self, rule_id: str) -> Severity:
        rule = self.registry.get(rule_id)
        if rule is None:
            raise RuntimeError(f'finding names unregistered rule: {rule_id}')
        return self.severity_of(rule.meta)


def worst(findings: Iterable[Finding]) -> Severity | None:
    """The most severe finding's severity, or `None` for none at all."""
    return _RANK.worst(finding.severity for finding in findings)


def at_least(severity: Severity) -> frozenset[Severity]:
    """`severity` and everything worse — what `--severity` selects."""
    return _RANK.at_least(severity)


def sorted_by_rule(findings: Iterable[Finding]) -> Iterator[tuple[str, list[Finding]]]:
    """Findings grouped by rule, worst rule first, for the summary renderer."""
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.rule, []).append(finding)
    order = sorted(
        grouped,
        key=lambda rule: (min(_RANK.rank(finding.severity) for finding in grouped[rule]), -len(grouped[rule]), rule),
    )
    for rule in order:
        yield rule, grouped[rule]
