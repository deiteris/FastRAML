"""The benchmark runner.

```
python -m bench run                  # every bench, every configuration
python -m bench run --bench large --config parse
python -m bench baseline             # record bench/baselines.json
python -m bench compare              # fail on a >25 % regression
python -m bench linearity            # time and memory against a half-size corpus
python -m bench startup              # cold process, package and CLI startup
python -m bench micro [PATTERN]      # leaf functions, per call (bench/micro.py)
python -m bench ab REF --bench enums # this tree against REF, alternated (bench/ab.py)
```

Each measurement runs in a **fresh subprocess**. `harness.py` explains why in
detail; the short version is that peak RSS is a process high-water mark, so two
benches in one interpreter cannot both report their own.

Baselines are fingerprinted by interpreter, platform and YAML backend, and
`compare` refuses to compare across a fingerprint change rather than reporting a
"regression" that is really a different machine. A number recorded on a laptop
does not bound a number recorded in CI, and pretending otherwise produces a
gate that everyone learns to ignore.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bench import corpus
from bench.harness import Measurement, measure

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastraml.registry import Raml

BASELINE_PATH = Path(__file__).parent / 'baselines.json'

#: The eight configurations every bench runs (docs/12 § 4). `unwrap+graph`,
#: `unwrap+lint` and `unwrap+occurrences` measure the consumer paths: parse,
#: unwrap, then project, lint or index. `service` is an edit in the language
#: service: a changed buffer, its reparse, its diagnostics and its occurrence
#: index. Each runs in a fresh subprocess, so one bench cannot inflate another.
CONFIGS: tuple[str, ...] = (
    'parse',
    'unwrap',
    'validate',
    'unwrap+validate',
    'unwrap+graph',
    'unwrap+lint',
    'unwrap+occurrences',
    'service',
)

#: For `compare`. Generous on purpose: it flags a change that made something
#: much slower, not a noisy machine (docs/12 § 5).
DEFAULT_TOLERANCE = 0.25

#: The linearity bound CI asserts (docs/12 § 5): the only performance property
#: of the parser rather than of the machine it ran on.
LINEARITY_TOLERANCE = 0.15


@dataclass(frozen=True, slots=True)
class Bench:
    name: str
    write: Callable[[Path, float], Path]


def _at(base: int, scale: float) -> int:
    return max(1, round(base * scale))


BENCHES: tuple[Bench, ...] = (
    Bench('small', lambda root, scale: corpus.write_small(root, type_count=_at(100, scale))),
    Bench(
        'large',
        lambda root, scale: corpus.write_large(root, type_count=_at(7000, scale), library_count=_at(150, scale)),
    ),
    Bench('endpoints', lambda root, scale: corpus.write_endpoints(root, resource_count=_at(500, scale))),
    Bench('extensions', lambda root, scale: corpus.write_extensions(root, resource_count=_at(500, scale))),
    Bench('validate', lambda root, scale: corpus.write_validate(root, type_count=_at(1000, scale))),
    Bench('jsonschema', lambda root, scale: corpus.write_jsonschema(root, schema_count=_at(200, scale))),
    Bench(
        'datatype-fragments', lambda root, scale: corpus.write_datatype_fragments(root, fragment_count=_at(200, scale))
    ),
    Bench('schema-export', lambda root, scale: corpus.write_jsonschema(root, schema_count=_at(200, scale))),
    Bench('schema-allof', lambda root, scale: corpus.write_schema_allof(root, schema_count=_at(200, scale))),
    Bench('raml-schema', lambda root, scale: corpus.write_validate(root, type_count=_at(200, scale))),
    Bench('projections', lambda root, scale: corpus.write_projections(root, family_count=_at(200, scale))),
    Bench('enums', lambda root, scale: corpus.write_enums(root, family_count=_at(40, scale))),
    Bench('unions', lambda root, scale: corpus.write_unions(root, family_count=_at(60, scale))),
    Bench('facets', lambda root, scale: corpus.write_facets(root, family_count=_at(150, scale))),
    Bench('inheritance', lambda root, scale: corpus.write_inheritance(root, family_count=_at(150, scale))),
    Bench('diamonds', lambda root, scale: corpus.write_diamonds(root, family_count=_at(20, scale))),
    Bench('templates', lambda root, scale: corpus.write_templates(root, resource_count=_at(250, scale))),
    Bench('template-scopes', lambda root, scale: corpus.write_template_scopes(root, resource_count=_at(500, scale))),
    Bench('lenient-recovery', lambda root, scale: corpus.write_lenient_recovery(root, family_count=_at(200, scale))),
    Bench('sequence-merge', lambda root, scale: corpus.write_sequence_merge(root, resource_count=_at(60, scale))),
    Bench(
        'reference-namespaces',
        lambda root, scale: corpus.write_reference_namespaces(root, resource_count=_at(500, scale)),
    ),
    Bench('includes', lambda root, scale: corpus.write_includes(root, resource_count=_at(500, scale))),
    Bench('include-content', lambda root, scale: corpus.write_include_content(root, resource_count=_at(250, scale))),
    Bench('inline-json', lambda root, scale: corpus.write_inline_json(root, type_count=_at(1000, scale))),
    Bench(
        'annotation-targets', lambda root, scale: corpus.write_annotation_targets(root, family_count=_at(250, scale))
    ),
    Bench('doc-links', lambda root, scale: corpus.write_doc_links(root, resource_count=_at(250, scale))),
    Bench(
        'non-strict-examples', lambda root, scale: corpus.write_non_strict_examples(root, family_count=_at(400, scale))
    ),
    Bench('hover', lambda root, scale: corpus.write_hover(root, family_count=_at(400, scale))),
    Bench('effective-types', lambda root, scale: corpus.write_hover(root, family_count=_at(300, scale))),
    Bench('inlays', lambda root, scale: corpus.write_hover(root, family_count=_at(400, scale))),
    Bench('source-structure', lambda root, scale: corpus.write_hover(root, family_count=_at(400, scale))),
)

_BY_NAME = {bench.name: bench for bench in BENCHES}


# -- fingerprint --------------------------------------------------------------


def fingerprint() -> dict[str, str]:
    """What a recorded number is only valid for."""
    from fastraml import backend_name  # noqa: PLC0415 - kept out of the worker's import cost

    return {
        'python': '.'.join(str(part) for part in sys.version_info[:2]),
        'platform': f'{platform.system()}-{platform.machine()}',
        'yaml_backend': backend_name(),
    }


# -- the worker ---------------------------------------------------------------


def run_one(bench: str, config: str, entry: Path, repeat: int) -> Measurement:
    """Measure one configuration. Runs in the subprocess, not the driver."""
    from fastraml import ParseOptions, parse_from_path  # noqa: PLC0415 - see module docstring

    if config == 'unwrap' and bench in {
        'schema-export',
        'raml-schema',
        'datatype-fragments',
        'projections',
        'doc-links',
        'hover',
        'effective-types',
        'inlays',
        'source-structure',
    }:
        return _measure_view(bench, entry, repeat)
    if config == 'service':
        return _measure_edit(bench, entry, repeat)
    options = ParseOptions(
        unwrap='unwrap' in config,
        validate='validate' in config,
        retain_source='lint' in config,
        retain_text='occurrences' in config,
    )

    def parse() -> Raml:
        if bench == 'lenient-recovery':
            from fastraml import parse_lenient  # noqa: PLC0415 - feature workload only

            return parse_lenient(entry, options)[0]
        return parse_from_path(entry, options)

    if 'lint' in config:
        from fastraml.views.lint import Config, Linter, builtin_registry  # noqa: PLC0415 - as above

        linter = Linter(builtin_registry(), Config(extends=('all',)))
        # Fixed output limits would cap retained memory at both corpus sizes.
        limits = {'max_findings': None, 'max_findings_per_rule': None} if bench == 'non-strict-examples' else {}
        return measure(bench, config, lambda: linter.report(parse(), **limits), repeat=repeat)
    if 'graph' in config:
        from fastraml.views.graph import build_graph  # noqa: PLC0415 - as above

        return measure(bench, config, lambda: build_graph(parse()), repeat=repeat)
    if 'occurrences' in config:
        from fastraml.views.occurrences import build_occurrences  # noqa: PLC0415 - as above

        return measure(bench, config, lambda: build_occurrences(parse()), repeat=repeat)
    return measure(bench, config, parse, repeat=repeat)


def _measure_view(bench: str, entry: Path, repeat: int) -> Measurement:
    from fastraml import ParseOptions, parse_from_path  # noqa: PLC0415 - feature workload only

    service_workload = {
        'hover': _measure_hover,
        'effective-types': _measure_effective_types,
        'inlays': _measure_inlays,
        'source-structure': _measure_source_structure,
    }.get(bench)
    if service_workload is not None:
        return service_workload(entry, repeat)
    if bench == 'projections':
        from fastraml.views.jsonschema import to_json_schema  # noqa: PLC0415 - feature workload only
        from fastraml.views.openapi import to_openapi  # noqa: PLC0415 - feature workload only
        from fastraml.views.render import render  # noqa: PLC0415 - feature workload only

        def project_effective() -> object:
            raml = parse_from_path(entry, ParseOptions(unwrap=True))
            declared = raml.types_in(raml.location).values()
            return (
                raml,
                to_openapi(raml)[0].to_dict(),
                [(to_json_schema(base)[0], list(render(base))) for base in declared],
            )

        return measure(bench, 'unwrap', project_effective, repeat=repeat)

    if bench == 'datatype-fragments':
        from fastraml.views.graph import build_graph  # noqa: PLC0415 - feature workload only
        from fastraml.views.tree import build_tree, positions_of  # noqa: PLC0415 - feature workload only

        def project() -> object:
            raml = parse_from_path(entry, ParseOptions(unwrap=True))
            graph = build_graph(raml)
            return graph, build_tree(raml, addresses=graph.addresses), positions_of(raml)

        return measure(bench, 'unwrap', project, repeat=repeat)

    if bench == 'doc-links':
        from fastraml.views.graph import build_graph  # noqa: PLC0415 - feature workload only
        from fastraml.views.lint import Config, Linter, builtin_registry  # noqa: PLC0415 - feature workload only
        from fastraml.views.tree import build_tree  # noqa: PLC0415 - feature workload only

        # Both readers of the links: the tree sends them, lint checks them.
        linter = Linter(builtin_registry(), Config(extends=('documentation',)))

        def link() -> object:
            raml = parse_from_path(entry, ParseOptions(unwrap=True))
            graph = build_graph(raml)
            return graph, build_tree(raml, addresses=graph.addresses), linter.run(raml, graph=graph)

        return measure(bench, 'unwrap', link, repeat=repeat)

    if bench == 'schema-export':
        from fastraml.views.raml import to_raml  # noqa: PLC0415 - feature workload only

        def export_raml() -> tuple[object, list[str]]:
            raml = parse_from_path(entry, ParseOptions(unwrap=True))
            return raml, [to_raml(base.shape) for base in raml.types_in(raml.location).values()]

        return measure(bench, 'unwrap', export_raml, repeat=repeat)

    from fastraml.views.jsonschema import to_json_schema  # noqa: PLC0415 - feature workload only

    def export_json() -> tuple[object, list[str]]:
        raml = parse_from_path(entry, ParseOptions(unwrap=True))
        return raml, [json.dumps(to_json_schema(base)[0]) for base in raml.types_in(raml.location).values()]

    return measure(bench, 'unwrap', export_json, repeat=repeat)


def _measure_hover(entry: Path, repeat: int) -> Measurement:
    """A cold snapshot followed by repeated, differently targeted authoring hovers."""
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415 - feature workload only
    from fastraml.service import queries  # noqa: PLC0415 - feature workload only
    from fastraml.service.workspace import Workspace  # noqa: PLC0415 - feature workload only
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415 - feature workload only

    text = entry.read_text(encoding='utf-8')
    uri, folder = path_to_file_uri(entry), path_to_file_uri(entry.parent)
    probes = []
    definition_probes = []
    for line, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.lstrip()
        if stripped == 'type: string':
            probes.append((line, raw.index('string') + 1))
        elif stripped.startswith('note:'):
            probes.append((line, len(raw) - len(stripped) + 1))
            if not stripped.startswith('note: {'):
                probes.append((line, raw.index(':') + 3))
        elif (
            raw.startswith('  Name')
            or stripped.startswith(('minLength:', 'label?:', '404:', 'summary:'))
            or stripped == 'get:'
        ):
            probes.append((line, len(raw) - len(stripped) + 1))
        if stripped.startswith('summary:'):
            definition_probes.append((line, len(raw) - len(stripped) + 1))

    def hover() -> object:
        workspace = Workspace([folder])
        workspace.open(uri, text, 1)
        snapshot = workspace.snapshot(uri)
        answers = [queries.hover(snapshot, uri, line, column) for line, column in probes]
        definitions = [queries.definition(snapshot, uri, line, column) for line, column in definition_probes]
        if (
            snapshot.error is not None
            or any(answer is None for answer in answers)
            or any(not sites for sites in definitions)
        ):
            message = 'hover workload no longer reaches every authoring query'
            raise RuntimeError(message)
        return snapshot, answers, definitions

    with tuned_gc():
        return measure('hover', 'unwrap', hover, repeat=repeat)


def _measure_effective_types(entry: Path, repeat: int) -> Measurement:
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415 - feature workload only
    from fastraml.service import lenses  # noqa: PLC0415 - feature workload only
    from fastraml.service.workspace import Workspace  # noqa: PLC0415 - feature workload only
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415 - feature workload only

    text = entry.read_text(encoding='utf-8')
    uri, folder = path_to_file_uri(entry), path_to_file_uri(entry.parent)

    def effective() -> object:
        workspace = Workspace([folder])
        workspace.open(uri, text, 1)
        snapshot = workspace.snapshot(uri)
        sites = lenses.code_lenses(snapshot, uri)
        answers = [
            lenses.effective_type(snapshot, uri, site.span.line, site.span.column, name=site.name) for site in sites
        ]
        if snapshot.error is not None or not sites or any(answer is None for answer in answers):
            message = 'effective-type workload no longer reaches each lens rendering'
            raise RuntimeError(message)
        return snapshot, answers

    with tuned_gc():
        return measure('effective-types', 'unwrap', effective, repeat=repeat)


def _measure_inlays(entry: Path, repeat: int) -> Measurement:
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415 - feature workload only
    from fastraml.positions import Position  # noqa: PLC0415 - feature workload only
    from fastraml.service import inlays  # noqa: PLC0415 - feature workload only
    from fastraml.service.workspace import Workspace  # noqa: PLC0415 - feature workload only
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415 - feature workload only

    text = entry.read_text(encoding='utf-8')
    uri, folder = path_to_file_uri(entry), path_to_file_uri(entry.parent)
    span = Position(1, 1, len(text.splitlines()) + 1, 1)

    def hints() -> object:
        workspace = Workspace([folder])
        workspace.open(uri, text, 1)
        snapshot = workspace.snapshot(uri)
        result = inlays.inlay_hints(snapshot, uri, span)
        if snapshot.error is not None or not result:
            message = 'inlay workload no longer reaches inline type and inherited-facet hints'
            raise RuntimeError(message)
        return snapshot, result

    with tuned_gc():
        return measure('inlays', 'unwrap', hints, repeat=repeat)


def _measure_source_structure(entry: Path, repeat: int) -> Measurement:
    """Folding and selection requests served from the workspace's source cache."""
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415 - feature workload only
    from fastraml.service import queries  # noqa: PLC0415 - feature workload only
    from fastraml.service.workspace import Workspace  # noqa: PLC0415 - feature workload only
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415 - feature workload only

    text = entry.read_text(encoding='utf-8')
    uri, folder = path_to_file_uri(entry), path_to_file_uri(entry.parent)
    lines = text.splitlines()
    probes = 12
    step = max(1, len(lines) // probes)
    positions = [
        (number, len(raw) - len(raw.lstrip()) + 1)
        for number, raw in enumerate(lines[2:], start=3)
        if ':' in raw and not raw.lstrip().startswith('#')
    ][::step][:probes]

    def structure() -> object:
        workspace = Workspace([folder])
        workspace.open(uri, text, 1)
        folded = [queries.folding_ranges_of(workspace.source(uri)) for _ in range(3)]
        selected = [queries.selection_ranges_of(workspace.source(uri), line, column) for line, column in positions]
        if len(positions) < probes or not folded[0] or any(not spans for spans in selected):
            message = 'source-structure workload no longer reaches folding and selection'
            raise RuntimeError(message)
        return workspace.source(uri), folded, selected

    with tuned_gc():
        return measure('source-structure', 'unwrap', structure, repeat=repeat)


def _measure_edit(bench: str, entry: Path, repeat: int) -> Measurement:
    """One edit to the root's buffer, and what the editor then asks for first."""
    from itertools import count  # noqa: PLC0415 - as above

    from fastraml.gctuning import tuned_gc  # noqa: PLC0415 - as above
    from fastraml.service import queries  # noqa: PLC0415 - as above
    from fastraml.service.workspace import Workspace  # noqa: PLC0415 - as above
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415 - as above

    root = path_to_file_uri(entry)
    workspace = Workspace([path_to_file_uri(entry.parent)])
    text = entry.read_text(encoding='utf-8')
    versions = count(1)

    def edit() -> object:
        version = next(versions)
        workspace.change(root, f'{text}\n# edit {version}\n', version)
        # As the server does after the pause, before the parse (docs/21 § 2).
        workspace.collect()
        snapshot = workspace.snapshot(root)
        return queries.diagnostics(snapshot, lint=False), snapshot.occurrences

    # A server defers full collections for its whole run (docs/21 § 2).
    with tuned_gc():
        return measure(bench, 'service', edit, repeat=repeat)


# -- the driver ---------------------------------------------------------------


def _spawn(bench: str, config: str, entry: Path, repeat: int, *, cwd: Path | None = None) -> Measurement:
    """Measure in a fresh process started in `cwd`, whose `fastraml` it imports."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, sys.executable, no shell
        [sys.executable, '-m', 'bench', 'worker', bench, config, str(entry), str(repeat)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd or Path(__file__).parent.parent),
    )
    if completed.returncode != 0:
        raise RuntimeError(f'{bench}/{config} failed:\n{completed.stdout}\n{completed.stderr}')
    return Measurement(**json.loads(completed.stdout.strip().splitlines()[-1]))


def run_suite(  # noqa: PLR0913 - the selectors, and whether to print
    names: Sequence[str],
    configs: Sequence[str],
    *,
    scale: float,
    repeat: int,
    keep: Path | None,
    quiet: bool = False,
) -> list[Measurement]:
    results: list[Measurement] = []
    for name in names:
        bench = _BY_NAME[name]
        root = Path(tempfile.mkdtemp(prefix=f'fastraml-bench-{name}-')) if keep is None else keep / name
        root.mkdir(parents=True, exist_ok=True)
        try:
            # Generation is outside every timed run, and shared by all four
            # configurations: writing 7000 types takes longer than parsing them.
            entry = bench.write(root, scale)
            for config in configs:
                result = _spawn(name, config, entry, repeat)
                if not quiet:
                    print(_format(result))
                results.append(result)
        finally:
            if keep is None:
                shutil.rmtree(root, ignore_errors=True)
    return results


def _format(result: Measurement) -> str:
    rss = 'n/a' if result.max_rss_bytes is None else f'{result.max_rss_bytes / 1e6:8.1f} MB'
    return (
        f'{result.bench:<11} {result.config:<16} '
        f'{result.seconds * 1e3:9.1f} ms  '
        f'{result.allocated_bytes / 1e6:8.1f} MB alloc  {rss} rss'
    )


# -- baselines ----------------------------------------------------------------


def _key(result: Measurement) -> str:
    return f'{result.bench}/{result.config}'


def write_baseline(results: Sequence[Measurement]) -> None:
    """Record the measurements, **merging** into what is already there.

    Merging rather than replacing, because `baseline --bench small` otherwise
    silently deletes the other four benches' rows: the driver only ever passes
    what it just ran. Replacing is right only when the whole suite ran, and the
    function cannot tell whether it did.

    A fingerprint change *does* replace. Rows recorded on another interpreter or
    platform are not comparable and keeping them would let `compare` mix them.
    """
    current = fingerprint()
    kept: dict[str, object] = {}
    if BASELINE_PATH.exists():
        document = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
        if document.get('fingerprint') == current:
            kept = document.get('measurements', {})
        else:
            print('fingerprint changed; the previous baseline is discarded rather than merged')
    measurements = {**kept, **{_key(result): result.as_dict() for result in results}}
    document = {'fingerprint': current, 'measurements': measurements}
    BASELINE_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'wrote {BASELINE_PATH}: {len(results)} recorded, {len(measurements)} total')


def compare(results: Sequence[Measurement], tolerance: float) -> int:
    if not BASELINE_PATH.exists():
        print('no baseline recorded; run `python -m bench baseline`')
        return 1
    document = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
    if document['fingerprint'] != fingerprint():
        print('baseline was recorded on a different configuration; not comparable')
        print(f'  baseline: {document["fingerprint"]}')
        print(f'  current:  {fingerprint()}')
        return 0
    failures = 0
    for result in results:
        recorded = document['measurements'].get(_key(result))
        if recorded is None:
            print(f'{_key(result):<28} new, no baseline')
            continue
        delta = result.seconds / recorded['seconds'] - 1.0
        verdict = 'FAIL' if delta > tolerance else 'ok'
        failures += verdict == 'FAIL'
        print(f'{_key(result):<28} {delta * 100:+7.1f} %  {verdict}')
    return 1 if failures else 0


# -- linearity ----------------------------------------------------------------


#: The configuration each workload's linearity is measured in: the one that
#: runs the code it exists for (docs/12 § 4).
LINEARITY_CONFIGS: dict[str, str] = {
    'lenient-recovery': 'unwrap+validate',
    'large': 'parse',
    'enums': 'unwrap+validate',
    'unions': 'unwrap+validate',
    'facets': 'unwrap+validate',
    'inheritance': 'unwrap+validate',
    'diamonds': 'unwrap+validate',
    'templates': 'unwrap+validate',
    'template-scopes': 'unwrap+validate',
    'sequence-merge': 'parse',
    'reference-namespaces': 'unwrap+validate',
    'includes': 'parse',
    'include-content': 'parse',
    'inline-json': 'unwrap+validate',
    'annotation-targets': 'unwrap+validate',
    'schema-export': 'unwrap',
    'schema-allof': 'unwrap+graph',
    'raml-schema': 'unwrap',
    'projections': 'unwrap',
    'datatype-fragments': 'unwrap',
    'doc-links': 'unwrap',
    'non-strict-examples': 'unwrap+lint',
    'hover': 'unwrap',
    'effective-types': 'unwrap',
    'inlays': 'unwrap',
    'source-structure': 'unwrap',
}


@dataclass(frozen=True, slots=True)
class Linearity:
    """Full size against half size: each ratio is 1.0 when the cost is linear."""

    bench: str
    config: str
    full: Measurement
    half: Measurement

    @property
    def time_ratio(self) -> float:
        return self.full.seconds / (2 * self.half.seconds)

    @property
    def peak_ratio(self) -> float:
        return self.full.allocated_bytes / (2 * self.half.allocated_bytes)

    @property
    def retained_ratio(self) -> float:
        return self.full.retained_bytes / (2 * self.half.retained_bytes)

    def failures(self) -> list[str]:
        ratios = (('time', self.time_ratio), ('peak memory', self.peak_ratio), ('retained memory', self.retained_ratio))
        return [
            f'{what} ratio to linear {ratio:.3f}' for what, ratio in ratios if abs(ratio - 1.0) > LINEARITY_TOLERANCE
        ]


def measure_linearity(name: str, *, scale: float, repeat: int, attempts: int = 1) -> Linearity:
    """Best of `attempts` at each size, interleaved so a slow stretch hits both."""
    config = LINEARITY_CONFIGS.get(name, 'parse')
    full: Measurement | None = None
    half: Measurement | None = None
    for _ in range(attempts):
        at_full = run_suite([name], [config], scale=scale, repeat=repeat, keep=None, quiet=True)[0]
        at_half = run_suite([name], [config], scale=scale / 2, repeat=repeat, keep=None, quiet=True)[0]
        full = at_full if full is None or at_full.seconds < full.seconds else full
        half = at_half if half is None or at_half.seconds < half.seconds else half
    if full is None or half is None:
        msg = 'attempts must be at least 1'
        raise ValueError(msg)
    return Linearity(name, config, full, half)


def linearity(names: Sequence[str], repeat: int, scale: float) -> int:
    """The linearity bound of docs/12 § 5, in time and in memory."""
    failed = 0
    for name in names:
        result = measure_linearity(name, scale=scale, repeat=repeat)
        print(
            f'{name}/{result.config}: time {result.time_ratio:.3f} '
            f'({result.full.seconds * 1e3:.1f} / {result.half.seconds * 1e3:.1f} ms), '
            f'peak {result.peak_ratio:.3f} ({result.full.allocated_bytes / 1e6:.1f} / '
            f'{result.half.allocated_bytes / 1e6:.1f} MB), retained {result.retained_ratio:.3f} '
            f'({result.full.retained_bytes / 1e6:.1f} / {result.half.retained_bytes / 1e6:.1f} MB)'
        )
        for failure in result.failures():
            print(f'  FAIL: {failure}, outside {LINEARITY_TOLERANCE * 100:.0f} %')
            failed += 1
    return 1 if failed else 0


# -- startup ------------------------------------------------------------------

_STARTUP_CASES = (
    ('python', ('-c', 'pass')),
    ('import fastraml', ('-c', 'import fastraml')),
    ('import parse API', ('-c', 'from fastraml import ParseOptions, parse_from_path')),
    ('cli --version', ('-m', 'fastraml.cli', '--version')),
)


def startup(repeat: int) -> int:
    """Measure cold imports in fresh interpreters; the parse benches start warm."""
    timings: dict[str, float] = {}
    for name, arguments in _STARTUP_CASES:
        best = float('inf')
        for _ in range(repeat):
            started = time.perf_counter()
            subprocess.run(  # noqa: S603 - this interpreter and fixed arguments
                [sys.executable, *arguments], capture_output=True, check=True
            )
            best = min(best, time.perf_counter() - started)
        timings[name] = best
    process = timings['python']
    for name, seconds in timings.items():
        print(f'{name:<18} {seconds * 1e3:7.1f} ms  ({(seconds - process) * 1e3:7.1f} ms after process start)')
    return 0


# -- micro --------------------------------------------------------------------


def micro(pattern: str) -> int:
    """Print each microbenchmark's time per call (docs/12 § 4)."""
    from bench.micro import run_micro  # noqa: PLC0415 - imports the package under test

    results = run_micro(pattern)
    width = max((len(name) for name in results), default=0)
    for name, seconds in results.items():
        shown = 'missing' if seconds is None else f'{seconds * 1e6:10.2f} us'
        print(f'{name:<{width}} {shown}')
    return 0


# -- entry point --------------------------------------------------------------


def _ab(args: argparse.Namespace) -> int:
    from bench.ab import run_ab  # noqa: PLC0415 - only this command needs git

    if not args.rest:
        print('usage: python -m bench ab REF [--bench NAME] [--config NAME] [--rounds N]')
        return 2
    return run_ab(
        args.rest[0],
        args.bench or [bench.name for bench in BENCHES],
        args.config or list(CONFIGS),
        scale=args.scale,
        repeat=args.repeat,
        rounds=args.rounds,
        spawn=_spawn,
        write=lambda name, root, scale: _BY_NAME[name].write(root, scale),
    )


def _worker(rest: Sequence[str]) -> int:
    bench, config, entry, repeat = rest
    print(json.dumps(run_one(bench, config, Path(entry), int(repeat)).as_dict()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='bench', description=__doc__)
    parser.add_argument(
        'command', choices=('run', 'baseline', 'compare', 'linearity', 'startup', 'micro', 'ab', 'worker')
    )
    parser.add_argument('rest', nargs='*')
    parser.add_argument('--bench', action='append', choices=[bench.name for bench in BENCHES])
    parser.add_argument('--config', action='append', choices=CONFIGS)
    parser.add_argument('--scale', type=float, default=1.0)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument('--keep', type=Path, default=None, help='write corpora here instead of a temp dir')
    parser.add_argument('--rounds', type=int, default=3, help='ab: alternations of the two trees')
    args = parser.parse_args(argv)

    # The commands that do not run the suite.
    standalone: dict[str, Callable[[], int]] = {
        'worker': lambda: _worker(args.rest),
        'linearity': lambda: linearity(args.bench or list(LINEARITY_CONFIGS), args.repeat, args.scale),
        'ab': lambda: _ab(args),
        'startup': lambda: startup(args.repeat),
        'micro': lambda: micro(args.rest[0] if args.rest else ''),
    }
    if args.command in standalone:
        return standalone[args.command]()

    names = args.bench or [bench.name for bench in BENCHES]
    configs = args.config or list(CONFIGS)
    results = run_suite(names, configs, scale=args.scale, repeat=args.repeat, keep=args.keep)

    if args.command == 'baseline':
        write_baseline(results)
        return 0
    if args.command == 'compare':
        return compare(results, args.tolerance)
    return 0


if __name__ == '__main__':
    sys.exit(main())
