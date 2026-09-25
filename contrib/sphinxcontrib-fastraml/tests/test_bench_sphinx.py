"""A generated Sphinx workload must exercise the paths it claims to measure."""

from __future__ import annotations

from bench_sphinx import run_case


def test_role_workload_reaches_the_parser_cache_and_roles(tmp_path):
    result = run_case(tmp_path, pages=2, roles=3, files=2)
    assert result.warnings == 0
    assert result.roles_seen == 6
    assert result.mtime_calls >= 1
    assert result.mtime_entries >= 2
    # The file check belongs to each page, not each of its three roles.
    assert result.mtime_calls <= 4


def test_type_workload_reaches_declaration_rendering(tmp_path):
    result = run_case(tmp_path, pages=0, roles=0, files=1, types=2, mode='types')
    assert result.warnings == 0
    assert result.roles_seen == 0
    assert result.declarations_seen >= 2
