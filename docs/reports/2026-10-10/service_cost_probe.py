"""Reproducible phase/cache diagnostics; no production implementation changes.

Run with the repository's Python. Each tree runs in a fresh subprocess, while
all trees share the corpus and this driver's scenario. The no-capture variant
only removes ProjectionRequest from the parked workspace's parse options.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import runpy
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[3]
SCENARIO = REPOSITORY / 'bench/service_session.py'


@contextmanager
def variant(module, no_capture):
    real = module.parse_lenient

    def parse(path, options):
        return real(path, replace(options, projection=None) if no_capture else options)

    with patch.object(module, 'parse_lenient', parse):
        yield


@contextmanager
def phase_timers(registry, entry, yamlnode, elapsed):
    """Coarse nested timers, never per-node profiling."""
    real_stage = registry.Raml.stage

    @contextmanager
    def stage(raml, which, **kwargs):
        start = time.perf_counter()
        try:
            with real_stage(raml, which, **kwargs):
                yield
        finally:
            elapsed['stage.' + which.value] += time.perf_counter() - start

    def timer(name, fn):
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed[name] += time.perf_counter() - start
        return measured

    real_compose = yamlnode.compose
    with ExitStack() as stack:
        stack.enter_context(patch.object(registry.Raml, 'stage', stage))
        # Imported function aliases must all point at the same timer.
        for module in tuple(sys.modules.values()):
            if module is not None and getattr(module, '__name__', '').startswith('fastraml.'):
                if getattr(module, 'compose', None) is real_compose:
                    stack.enter_context(patch.object(module, 'compose', timer('compose.total', real_compose)))
        stack.enter_context(patch.object(yamlnode.yaml, 'compose', timer('yaml.native', yamlnode.yaml.compose)))
        for name in ('_detach_type_expressions', 'finish_projections'):
            if hasattr(entry, name):
                stack.enter_context(patch.object(entry, name, timer(name, getattr(entry, name))))
        if hasattr(registry.Raml, 'finish_sources'):
            stack.enter_context(patch.object(registry.Raml, 'finish_sources', timer('finish_sources', registry.Raml.finish_sources)))
        yield


@contextmanager
def outline_timers(outline, elapsed):
    """Time coarse outline suboperations; recursive placement is counted once."""
    def timer(name, fn):
        depth = [0]

        def measured(*arguments, **kwargs):
            outer = depth[0] == 0
            depth[0] += 1
            start = time.perf_counter() if outer else 0
            try:
                return fn(*arguments, **kwargs)
            finally:
                depth[0] -= 1
                if outer:
                    elapsed[name] += time.perf_counter() - start
        return measured

    with ExitStack() as stack:
        if hasattr(outline, '_place_sections'):
            stack.enter_context(patch.object(outline, '_place_sections', timer('outline.place_sections', outline._place_sections)))
            import fastraml.service.source as source
            import fastraml.sourceprojection as projection
            stack.enter_context(patch.object(source.SourceIndex, '_projection', timer('outline.source_projection', source.SourceIndex._projection)))
            stack.enter_context(patch.object(projection.SourceProjection, '_index_starts', timer('outline.cursor_index', projection.SourceProjection._index_starts)))
        yield


def working_set():
    """Current Windows working set, not tracemalloc or a process high-water mark."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                'PagefileUsage', 'PeakPagefileUsage',
            )
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if not kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), ctypes.sizeof(counters)):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize


def worker(args):
    sys.path.insert(0, str(Path.cwd()))
    import fastraml
    import fastraml.registry as registry
    import fastraml.parser.entry as entry_module
    import fastraml.service.workspace as module
    import fastraml.yamlnode as yamlnode
    from fastraml.gctuning import tuned_gc
    from fastraml.service import inlays, lenses, outline, queries
    from fastraml.positions import Position

    if not Path(fastraml.__file__).resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError('worker imported the wrong tree: ' + fastraml.__file__)
    scenario = runpy.run_path(str(SCENARIO))
    prepared = scenario['prepare'](Path(args.entry), focus=Path(args.focus))
    phases = []
    root = prepared.root
    with tuned_gc(), variant(module, args.no_capture):
        if args.mixed:
            harness = runpy.run_path(str(REPOSITORY / 'bench/harness.py'))
            measured = harness['measure']('service-source-first' if args.source_first else 'service-session',
                                           'unwrap', lambda: scenario['exercise'](prepared, source_first=args.source_first),
                                           repeat=args.repeat)
            print(json.dumps({'mixed': measured.as_dict()}))
            return
        if args.ownership:
            module.Workspace([prepared.folder]).linter
            gc.collect()
            tracemalloc.start(8)
            workspace = module.Workspace([prepared.folder])
            workspace.open(root, prepared.versions[0], 1)
            snapshot = workspace.snapshot(root)
            assert snapshot.error is None
            snapshot.occurrences
            gc.collect()
            allocation = tracemalloc.take_snapshot()
            attributed = Counter()
            for statistic in allocation.statistics('traceback'):
                origin = 'other'
                for frame in reversed(statistic.traceback):
                    normalized = frame.filename.replace('\\', '/')
                    if '/fastraml/' in normalized:
                        origin = normalized.split('/fastraml/', 1)[1]
                        break
                attributed[origin] += statistic.size
            print(json.dumps({'ownership_MB': {name: size / 1e6 for name, size in attributed.most_common()},
                              'traced_kept_MB': sum(attributed.values()) / 1e6}))
            tracemalloc.stop()
            return
        if args.rss_edits:
            # Warm configuration before the import-only reference measurement.
            module.Workspace([prepared.folder]).linter
            gc.collect()
            baseline = working_set()
            baseline_models = sum(isinstance(obj, registry.Raml) for obj in gc.get_objects())
            workspace = module.Workspace([prepared.folder])
            if prepared.focus != root:
                workspace.open(prepared.focus, prepared.focus_text, 1)
            retention = []
            for version in range(1, args.rss_edits + 1):
                text = prepared.versions[0] + f'\n# retained edit {version}\n'
                workspace.change(root, text, version)
                workspace.collect()
                snapshot = workspace.snapshot(root)
                assert snapshot.error is None
                queries.diagnostics(snapshot, lint=False)
                snapshot.occurrences
                if version > args.rss_edits // 2:
                    outline.document_symbols(snapshot, prepared.focus)
                    inlays.inlay_hints(snapshot, prepared.focus, Position(1, 1, 120, 1))
                    scenario['folding'](workspace, prepared.focus)
                    for line, column in prepared.probes:
                        assert queries.hover(snapshot, prepared.focus, line, column)
                del snapshot
                gc.collect()
                models = sum(isinstance(obj, registry.Raml) for obj in gc.get_objects())
                rss = working_set()
                retention.append({'version': version, 'automatic_queries': version > args.rss_edits // 2,
                                  'rss_MB': None if rss is None else rss / 1e6, 'live_Raml': models,
                                  'snapshots': len(workspace._snapshots)})
            print(json.dumps({'baseline_rss_MB': baseline / 1e6 if baseline else None,
                              'baseline_live_Raml': baseline_models, 'retention': retention}))
            return
        # The host/lint configuration and first snapshot are warm for edit timing.
        workspace = module.Workspace([prepared.folder])
        workspace.open(root, prepared.versions[0], 1)
        if prepared.focus != root:
            workspace.open(prepared.focus, prepared.focus_text, 1)
        initial = workspace.snapshot(root)
        assert initial.error is None
        initial.occurrences
        del initial
        for version in range(2, args.repeat + 2):
            text = prepared.versions[0] + f'\n# measured edit {version}\n'
            elapsed = defaultdict(float)
            timer = phase_timers(registry, entry_module, yamlnode, elapsed) if args.detail else nullcontext()
            with timer:
                start = time.perf_counter()
                workspace.change(root, text, version)
                changed = time.perf_counter()
                workspace.collect()
                collected = time.perf_counter()
                snapshot = workspace.snapshot(root)
                parsed = time.perf_counter()
                assert snapshot.error is None
                diagnostics = queries.diagnostics(snapshot, lint=False)
                diagnosed = time.perf_counter()
                occurrences = snapshot.occurrences
                indexed = time.perf_counter()
            elapsed.update({
                'change': changed - start,
                'collect': collected - changed,
                'snapshot': parsed - collected,
                'diagnostics': diagnosed - parsed,
                'occurrences': indexed - diagnosed,
                'total': indexed - start,
            })
            phases.append(dict(elapsed))
            del snapshot, occurrences, diagnostics
        del workspace
        gc.collect()

        # Separate allocation run: retain one current workspace, not answers.
        tracemalloc.start()
        workspace = module.Workspace([prepared.folder])
        workspace.open(root, prepared.versions[0], 1)
        if prepared.focus != root:
            workspace.open(prepared.focus, prepared.focus_text, 1)
        snapshot = workspace.snapshot(root)
        assert snapshot.error is None
        memory = {}

        def keep(name):
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            memory[name] = {'kept': current, 'peak': peak}

        keep('snapshot')
        queries.diagnostics(snapshot, lint=False)
        snapshot.occurrences
        keep('diagnostics_occurrences')
        outline.document_symbols(snapshot, prepared.focus)
        queries.links(snapshot, prepared.focus)
        lenses.code_lenses(snapshot, prepared.focus)
        inlays.inlay_hints(snapshot, prepared.focus, Position(1, 1, 120, 1))
        scenario['folding'](workspace, prepared.focus)
        keep('automatic_queries')
        for line, column in prepared.probes:
            assert queries.hover(snapshot, prepared.focus, line, column) is not None
        keep('sparse_hovers')
        model = snapshot.raml
        assert model is not None
        dimensions = {'read_files': len(snapshot.read), 'shapes': len(model.shapes), 'endpoints': len(model.endpoints)}
        captures = getattr(model, 'source_projections', {})
        records = set()
        pending = [document.record(document.root) for document in captures.values() if document is not None]
        while pending:
            record = pending.pop()
            if record not in records:
                records.add(record)
                pending.extend(record.content)
        dimensions.update({'captured_files': len(captures), 'retained_source_records': len(records)})
        tracemalloc.stop()
        del snapshot, workspace, model, records, pending, captures
        gc.collect()

        # Untraced first-use/warm timings, new snapshot per repeat.
        queries_timed = []
        for _ in range(args.repeat):
            workspace = module.Workspace([prepared.folder])
            workspace.open(root, prepared.versions[0], 1)
            if prepared.focus != root:
                workspace.open(prepared.focus, prepared.focus_text, 1)
            snapshot = workspace.snapshot(root)
            snapshot.occurrences
            row = {}

            def request(name, operation):
                start = time.perf_counter()
                result = operation()
                row[name] = time.perf_counter() - start
                return result

            details = defaultdict(float)
            with outline_timers(outline, details) if args.detail else nullcontext():
                request('outline.first', lambda: outline.document_symbols(snapshot, prepared.focus))
            row.update(details)
            request('lenses.first', lambda: lenses.code_lenses(snapshot, prepared.focus))
            request('inlays.first', lambda: inlays.inlay_hints(snapshot, prepared.focus, Position(1, 1, 120, 1)))
            request('folding.first', lambda: scenario['folding'](workspace, prepared.focus))
            line, column = prepared.probes[0]
            assert request('hover.first_after_auto', lambda: queries.hover(snapshot, prepared.focus, line, column))
            request('outline.warm', lambda: outline.document_symbols(snapshot, prepared.focus))
            request('inlays.warm', lambda: inlays.inlay_hints(snapshot, prepared.focus, Position(1, 1, 120, 1)))
            request('folding.warm', lambda: scenario['folding'](workspace, prepared.focus))
            request('hover.warm', lambda: queries.hover(snapshot, prepared.focus, line, column))
            queries_timed.append(row)
            del snapshot, workspace
            gc.collect()

        # Work counters: separate run; no instrumented timings are reported.
        counts = Counter()
        event = ['setup']
        real_compose = yamlnode.yaml.compose
        real_convert = yamlnode._Converter.convert

        def counted_compose(text, **kwargs):
            counts[event[0] + '.yaml_composes'] += 1
            counts['input.' + hashlib.sha256(text.encode()).hexdigest()[:12]] += 1
            return real_compose(text, **kwargs)

        def counted_convert(converter, *arguments, **kwargs):
            counts[event[0] + '.converted_nodes'] += 1
            return real_convert(converter, *arguments, **kwargs)

        workspace = module.Workspace([prepared.folder])
        if prepared.focus != root:
            workspace.open(prepared.focus, prepared.focus_text, 1)
        with ExitStack() as stack:
            stack.enter_context(patch.object(yamlnode.yaml, 'compose', counted_compose))
            stack.enter_context(patch.object(yamlnode._Converter, 'convert', counted_convert))
            if hasattr(registry.Raml, 'compose_source'):
                import fastraml.sourcecapture as capture
                import fastraml.sourceprojection as projection
                for owner, attribute in ((capture._RecordNode, 'kind'), (projection.SourceRecord, 'line')):
                    original = getattr(owner, attribute)
                    label = owner.__name__ + '.' + attribute

                    def getter(node, original=original, label=label):
                        counts[event[0] + '.' + label] += 1
                        return original.fget(node)

                    stack.enter_context(patch.object(owner, attribute, property(getter)))
            for version, text in enumerate(prepared.versions, 1):
                workspace.change(root, text, version)
                workspace.collect()
                event[0] = f'v{version}.snapshot'
                snapshot = workspace.snapshot(root)
                event[0] = f'v{version}.occurrences'
                snapshot.occurrences
                event[0] = f'v{version}.automatic'
                outline.document_symbols(snapshot, prepared.focus)
                inlays.inlay_hints(snapshot, prepared.focus, Position(1, 1, 120, 1))
                scenario['folding'](workspace, prepared.focus)
                event[0] = f'v{version}.hovers'
                for line, column in prepared.probes:
                    assert queries.hover(snapshot, prepared.focus, line, column)
                event[0] = f'v{version}.warm'
                scenario['folding'](workspace, prepared.focus)
                for line, column in prepared.probes:
                    scenario['selection'](workspace, prepared.focus, line, column)
                del snapshot
        # Repeated source-only requests on one unchanged broken buffer.
        broken = '#%RAML 1.0\ntitle: [\n'
        workspace.change(root, broken, 4)
        workspace.collect()
        broken_counts = Counter()

        def broken_compose(*arguments, **kwargs):
            broken_counts['yaml_composes'] += 1
            return real_compose(*arguments, **kwargs)

        with patch.object(yamlnode.yaml, 'compose', broken_compose):
            failed = workspace.snapshot(root)
            assert failed.error is not None
            after_parse = broken_counts['yaml_composes']
            for _ in range(3):
                if hasattr(workspace, 'source'):
                    workspace.source(root)
                else:
                    scenario['folding'](workspace, root)
            counts['broken.snapshot_composes'] = after_parse
            counts['broken.three_source_request_composes'] = broken_counts['yaml_composes'] - after_parse
        print(json.dumps({'phases': phases, 'queries': queries_timed, 'memory': memory, 'dimensions': dimensions,
                          'counts': dict(counts), 'python': sys.version, 'backend': yamlnode.backend_name(),
                          'import': fastraml.__file__}))


def summarize(document):
    result = {'corpus': document['corpus'], 'scale': document['scale'], 'inputs': document['inputs'],
              'detail_timers': document['detail_timers'], 'mixed': document.get('mixed', False),
              'source_first': document.get('source_first', False), 'trees': {}}
    for label, rounds in document['results'].items():
        if 'mixed' in rounds[0]:
            values = [round_['mixed']['seconds'] for round_ in rounds]
            result['trees'][label] = {
                'mixed_best_ms': min(values) * 1000,
                'round_spread_percent': (max(values) / min(values) - 1) * 100,
                'peak_MB': min(round_['mixed']['allocated_bytes'] for round_ in rounds) / 1e6,
                'kept_MB': min(round_['mixed']['retained_bytes'] for round_ in rounds) / 1e6,
            }
            continue
        if 'retention' in rounds[0] or 'ownership_MB' in rounds[0]:
            result['trees'][label] = rounds
            continue
        phase_rows = [row for round_ in rounds for row in round_['phases']]
        best_rounds = [min(round_['phases'], key=lambda row: row['total']) for round_ in rounds]
        totals = [row['total'] for row in best_rounds]
        best = min(best_rounds, key=lambda row: row['total'])
        query_rows = [row for round_ in rounds for row in round_['queries']]
        counts = rounds[0]['counts']
        result['trees'][label] = {
            'best_total_ms': min(totals) * 1000,
            'round_spread_percent': (max(totals) / min(totals) - 1) * 100,
            'same_best_run_phases_ms': {key: value * 1000 for key, value in best.items()},
            'phase_minima_ms': {key: min(row.get(key, 0) for row in phase_rows) * 1000 for key in best},
            'query_minima_ms': {key: min(row[key] for row in query_rows) * 1000 for key in query_rows[0]},
            'memory_MB': {key: {name: min(round_['memory'][key][name] for round_ in rounds) / 1e6
                                for name in ('kept', 'peak')} for key in rounds[0]['memory']},
            'dimensions': rounds[0]['dimensions'],
            'counts': {key: value for key, value in counts.items() if not key.startswith('input.')},
            'identical_input_composition_histogram': dict(Counter(value for key, value in counts.items() if key.startswith('input.'))),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--entry')
    parser.add_argument('--focus')
    parser.add_argument('--no-capture', action='store_true')
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--corpus', choices=('large', 'hover', 'endpoints'), default='large')
    parser.add_argument('--scale', type=float, default=1)
    parser.add_argument('--rss-edits', type=int, default=0)
    parser.add_argument('--ownership', action='store_true')
    parser.add_argument('--mixed', action='store_true')
    parser.add_argument('--source-first', action='store_true')
    parser.add_argument('--tree', action='append', default=[], help='LABEL=PATH; label no-capture enables ablation')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--summarize', type=Path, nargs='+')
    args = parser.parse_args()
    if args.summarize:
        summaries = [summarize(json.loads(path.read_text(encoding='utf-8'))) for path in args.summarize]
        summarized = summaries[0] if len(summaries) == 1 else {'runs': summaries}
        text = json.dumps(summarized, indent=2) + '\n'
        if args.output:
            args.output.write_text(text, encoding='utf-8')
            print(f'wrote {len(summaries)} summarized runs to {args.output}')
        else:
            print(text)
        return
    if args.worker:
        worker(args)
        return
    sys.path.insert(0, str(REPOSITORY))
    from bench import corpus

    results = defaultdict(list)
    trees = [value.split('=', 1) for value in args.tree]
    with tempfile.TemporaryDirectory(prefix='service-costs-') as where:
        root = Path(where)
        if args.corpus == 'large':
            entry = corpus.write_large(root, type_count=max(1, round(7000 * args.scale)), library_count=max(1, round(150 * args.scale)))
            focus = root / 'lib/g0/l0.raml'
        elif args.corpus == 'hover':
            entry = corpus.write_hover(root, family_count=max(1, round(400 * args.scale)))
            focus = entry
        else:
            entry = corpus.write_endpoints(root, resource_count=max(1, round(500 * args.scale)))
            focus = entry
        files = list(root.rglob('*'))
        inputs = {'files': sum(path.is_file() for path in files),
                  'bytes': sum(path.stat().st_size for path in files if path.is_file()),
                  'entry_bytes': entry.stat().st_size, 'focus_bytes': focus.stat().st_size}
        for round_index in range(args.rounds):
            for label, tree in trees:
                command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--entry', str(entry),
                           '--focus', str(focus), '--repeat', str(args.repeat)]
                if label == 'no-capture':
                    command.append('--no-capture')
                if args.detail:
                    command.append('--detail')
                if args.rss_edits:
                    command.extend(['--rss-edits', str(args.rss_edits)])
                if args.ownership:
                    command.append('--ownership')
                if args.mixed:
                    command.append('--mixed')
                if args.source_first:
                    command.append('--source-first')
                completed = subprocess.run(command, cwd=tree, capture_output=True, text=True, check=True)
                result = json.loads(completed.stdout.strip().splitlines()[-1])
                results[label].append(result)
                if args.mixed:
                    print(f'{args.corpus} {label}: {result["mixed"]["seconds"] * 1000:.1f} ms', flush=True)
                elif args.ownership:
                    print(f'{args.corpus} {label}: {result["traced_kept_MB"]:.3f} MB kept', flush=True)
                elif args.rss_edits:
                    print(f'{args.corpus} {label}: {result["retention"][-1]}', flush=True)
                else:
                    minimum = min(row['total'] for row in result['phases'])
                    print(f'{args.corpus} round {round_index + 1} {label}: {minimum * 1000:.1f} ms', flush=True)
    document = {'corpus': args.corpus, 'scale': args.scale, 'inputs': inputs, 'detail_timers': args.detail,
                'mixed': args.mixed, 'source_first': args.source_first,
                'platform': platform.platform(), 'trees': trees, 'results': results}
    if args.output:
        args.output.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summarize(document), indent=2))


if __name__ == '__main__':
    main()
