"""`bench ab`: a revision lacking a feature is not a broken current tree (docs/12 § 5)."""

from __future__ import annotations

import pytest

from bench import ab
from bench.harness import Measurement


@pytest.mark.parametrize('fail_a', [None, 1, 2])
@pytest.mark.parametrize('fail_b', [None, 1, 2])
def test_a_failing_base_is_not_comparable_and_a_failing_current_tree_fails(
    tmp_path, monkeypatch, capsys, fail_a, fail_b
):
    base, current = tmp_path / 'base', tmp_path / 'current'
    monkeypatch.setattr(ab, '_ROOT', current)
    monkeypatch.setattr(ab, '_checkout', lambda ref: base)
    monkeypatch.setattr(ab, '_imports_from', lambda tree: tree / 'fastraml' / '__init__.py')
    git_calls = []

    def git(*args):
        git_calls.append(args)
        return 'base'

    monkeypatch.setattr(ab, '_git', git)
    counts = {'A': 0, 'B': 0}
    roots = []

    def write(name, root, scale):
        roots.append(root)
        return root / 'api.raml'

    def spawn(name, config, entry, repeat, *, cwd):
        side = 'A' if cwd == base else 'B'
        counts[side] += 1
        if counts[side] == (fail_a if side == 'A' else fail_b):
            raise RuntimeError(f'{side} worker stderr')
        return Measurement(name, config, seconds=1, allocated_bytes=100, max_rss_bytes=None, retained_bytes=50)

    result = ab.run_ab('base', ['feature'], ['unwrap'], scale=0.5, repeat=2, rounds=3, spawn=spawn, write=write)
    output = capsys.readouterr().out
    assert result == (0 if fail_b is None else 1)
    assert output.count('feature/unwrap') == 1
    assert counts['B'] == (3 if fail_b is None else fail_b)
    if fail_b is not None:
        assert 'feature/unwrap FAIL in B (this tree)' in output
        assert 'B worker stderr' in output
    elif fail_a is not None:
        assert 'feature/unwrap fails in A; not comparable (B succeeded)' in output
        assert 'A worker stderr' in output
        assert counts['A'] == fail_a
    else:
        assert 'noise' in output
        assert 'not comparable' not in output
    # The base worktree and the corpus are removed however the run ends.
    assert git_calls[-1] == ('worktree', 'remove', '--force', str(base))
    assert roots
    assert not any(root.exists() for root in roots)
