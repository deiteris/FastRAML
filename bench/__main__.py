"""The benchmark runner.

```
python -m bench run                  # every bench, every configuration
python -m bench run --bench large --config parse
python -m bench baseline             # record bench/baselines.json
python -m bench compare              # fail on a >25 % regression
python -m bench linearity            # bench_large against a half-size corpus
python -m bench startup              # cold process, package and CLI startup
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

BASELINE_PATH = Path(__file__).parent / 'baselines.json'

#: The six configurations every bench runs (docs/12 § 4). `unwrap+graph` and
#: `unwrap+lint` measure the consumer paths: parse, unwrap, then project or
#: lint. Each runs in a fresh subprocess, so one bench cannot inflate another.
CONFIGS: tuple[str, ...] = ('parse', 'unwrap', 'validate', 'unwrap+validate', 'unwrap+graph', 'unwrap+lint')

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
    Bench('validate', lambda root, scale: corpus.write_validate(root, type_count=_at(1000, scale))),
    Bench('jsonschema', lambda root, scale: corpus.write_jsonschema(root, schema_count=_at(200, scale))),
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

    options = ParseOptions(unwrap='unwrap' in config, validate='validate' in config, retain_source='lint' in config)
    if 'lint' in config:
        from fastraml.views.lint import Config, Linter, builtin_registry  # noqa: PLC0415 - as above

        linter = Linter(builtin_registry(), Config(extends=('all',)))
        return measure(bench, config, lambda: linter.report(parse_from_path(entry, options)), repeat=repeat)
    if 'graph' in config:
        from fastraml.views.graph import build_graph  # noqa: PLC0415 - as above

        return measure(bench, config, lambda: build_graph(parse_from_path(entry, options)), repeat=repeat)
    return measure(bench, config, lambda: parse_from_path(entry, options), repeat=repeat)


# -- the driver ---------------------------------------------------------------


def _spawn(bench: str, config: str, entry: Path, repeat: int) -> Measurement:
    completed = subprocess.run(  # noqa: S603 - fixed argv, sys.executable, no shell
        [sys.executable, '-m', 'bench', 'worker', bench, config, str(entry), str(repeat)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).parent.parent),
    )
    if completed.returncode != 0:
        raise RuntimeError(f'{bench}/{config} failed:\n{completed.stdout}\n{completed.stderr}')
    return Measurement(**json.loads(completed.stdout.strip().splitlines()[-1]))


def run_suite(
    names: Sequence[str], configs: Sequence[str], *, scale: float, repeat: int, keep: Path | None
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


def linearity(repeat: int, scale: float) -> int:
    """The linearity bound of docs/12 § 5, measured rather than asserted."""
    full = run_suite(['large'], ['parse'], scale=scale, repeat=repeat, keep=None)[0]
    half = run_suite(['large'], ['parse'], scale=scale / 2, repeat=repeat, keep=None)[0]
    ratio = full.seconds / (2 * half.seconds)
    deviation = abs(ratio - 1.0)
    print(f'full {full.seconds * 1e3:.1f} ms, half {half.seconds * 1e3:.1f} ms')
    print(f'ratio to linear {ratio:.3f} ({deviation * 100:+.1f} %)')
    if deviation > LINEARITY_TOLERANCE:
        print(f'FAIL: outside {LINEARITY_TOLERANCE * 100:.0f} % of linear')
        return 1
    print('ok')
    return 0


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


# -- entry point --------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='bench', description=__doc__)
    parser.add_argument('command', choices=('run', 'baseline', 'compare', 'linearity', 'startup', 'worker'))
    parser.add_argument('rest', nargs='*')
    parser.add_argument('--bench', action='append', choices=[bench.name for bench in BENCHES])
    parser.add_argument('--config', action='append', choices=CONFIGS)
    parser.add_argument('--scale', type=float, default=1.0)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument('--keep', type=Path, default=None, help='write corpora here instead of a temp dir')
    args = parser.parse_args(argv)

    if args.command == 'worker':
        bench, config, entry, repeat = args.rest
        print(json.dumps(run_one(bench, config, Path(entry), int(repeat)).as_dict()))
        return 0

    if args.command == 'linearity':
        return linearity(args.repeat, args.scale)
    if args.command == 'startup':
        return startup(args.repeat)

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
