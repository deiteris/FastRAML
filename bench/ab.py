"""`bench ab REF`: this tree against a git revision, alternated, on one machine.

`baseline` then `compare` measures the two sides minutes apart, and a machine
drifts in minutes: the whole suite moved from -23 % to +17 % between two runs
of unchanged code (docs/12 § 5). Here the two trees take turns, round by round,
over one corpus generated once, so drift lands on both.

Each side reports the best of its rounds, and a *noise* figure: the spread of
its own rounds, the larger of the two sides. A time delta inside the noise is
reported as noise, not as a result.

Memory is compared twice, as `bench/harness.py` measures it: the traced peak
of one build with the collector paused, and what the result retains. These
show a field added to every shape, or a cache kept alive on the model, and
they barely vary between runs; each still gets a noise figure and a verdict.
RSS is shown for information only: it includes the interpreter.

The revision is checked out as a detached worktree, and this tree's `bench/` is
copied into it, so both sides read byte-identical corpora and differ only in
`fastraml/`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bench.harness import Measurement

__all__ = ['run_ab']

_ROOT = Path(__file__).resolve().parent.parent


def _git(*arguments: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git argv, no shell
        ['git', *arguments],  # noqa: S607 - git from PATH, as every other tool here
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _checkout(ref: str) -> Path:
    """A detached worktree of `ref`, carrying this tree's `bench/`."""
    where = Path(tempfile.mkdtemp(prefix='fastraml-ab-'))
    _git('worktree', 'add', '--detach', str(where), ref)
    shutil.rmtree(where / 'bench', ignore_errors=True)
    shutil.copytree(_ROOT / 'bench', where / 'bench', ignore=shutil.ignore_patterns('__pycache__', 'baselines.json'))
    return where


def _imports_from(tree: Path) -> Path:
    """Where a worker started in `tree` imports `fastraml` from."""
    found = subprocess.run(
        [sys.executable, '-c', 'import fastraml; print(fastraml.__file__)'],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(found).resolve()


def _spread(values: Sequence[float]) -> float:
    return (max(values) - min(values)) / min(values)


def run_ab(  # noqa: PLR0913 - the suite's own selectors, plus the revision and the rounds
    ref: str,
    names: Sequence[str],
    configs: Sequence[str],
    *,
    scale: float,
    repeat: int,
    rounds: int,
    spawn: Callable[..., Measurement],
    write: Callable[[str, Path, float], Path],
) -> int:
    tree = _checkout(ref)
    try:
        imported = _imports_from(tree)
        if tree.resolve() not in imported.parents:
            print(f'the worker in {tree} imports fastraml from {imported}; refusing to compare')
            return 1
        label = _git('rev-parse', '--short', ref)
        print(f'A = {label} ({ref}), B = this tree; {rounds} rounds, best of {repeat} per round')
        print(
            f'{"bench/config":<26} {"A ms":>8} {"B ms":>8} {"time":>8} {"noise":>6}  '
            f'{"A peak":>9} {"B peak":>9} {"peak":>8}  {"A kept":>9} {"B kept":>9} {"kept":>8}  '
            f'{"A rss":>7} {"B rss":>7}'
        )
        for name in names:
            corpus_root = Path(tempfile.mkdtemp(prefix=f'fastraml-ab-{name}-'))
            try:
                entry = write(name, corpus_root, scale)
                for config in configs:
                    sides: dict[str, list[Measurement]] = {'A': [], 'B': []}
                    try:
                        for _ in range(rounds):
                            sides['A'].append(spawn(name, config, entry, repeat, cwd=tree))
                            sides['B'].append(spawn(name, config, entry, repeat, cwd=_ROOT))
                    except RuntimeError:
                        # A corpus for a feature the revision lacks can fail
                        # there; that side has no number, which is the result.
                        failed = 'A' if len(sides['A']) == len(sides['B']) else 'B'
                        print(f'{name}/{config:<20} fails in {failed}; not comparable')
                        continue
                    print(_row(f'{name}/{config}', sides['A'], sides['B']))
            finally:
                shutil.rmtree(corpus_root, ignore_errors=True)
    finally:
        _git('worktree', 'remove', '--force', str(tree))
    return 0


def _row(key: str, a: Sequence[Measurement], b: Sequence[Measurement]) -> str:
    a_time, b_time = min(m.seconds for m in a), min(m.seconds for m in b)
    time_noise = max(_spread([m.seconds for m in a]), _spread([m.seconds for m in b]))
    return (
        f'{key:<26} {a_time * 1e3:8.1f} {b_time * 1e3:8.1f} {_verdict(a_time, b_time, time_noise)} '
        f'{time_noise * 100:5.1f}%  {_memory(a, b, "allocated_bytes")}  {_memory(a, b, "retained_bytes")}  '
        f'{_rss(a):>7} {_rss(b):>7}'
    )


def _memory(a: Sequence[Measurement], b: Sequence[Measurement], field: str) -> str:
    """One memory figure for both sides, and its verdict against their spread."""
    a_values = [getattr(m, field) for m in a]
    b_values = [getattr(m, field) for m in b]
    a_bytes, b_bytes = min(a_values), min(b_values)
    noise = max(_spread(a_values), _spread(b_values)) if a_bytes and b_bytes else 0.0
    return f'{a_bytes / 1e6:7.3f}MB {b_bytes / 1e6:7.3f}MB {_verdict(a_bytes, b_bytes, noise)}'


def _verdict(a: float, b: float, noise: float) -> str:
    """The delta, or `noise` when it is no larger than either side's own spread."""
    delta = b / a - 1.0 if a else 0.0
    return f'{delta * 100:+7.1f}%' if abs(delta) > noise else '   noise'


def _rss(side: Sequence[Measurement]) -> str:
    marks = [m.max_rss_bytes for m in side if m.max_rss_bytes is not None]
    return f'{min(marks) / 1e6:5.0f}MB' if marks else 'n/a'
