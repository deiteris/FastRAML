"""Generate a Sphinx site to measure RAML roles and declaration rendering.

Run from this project: ``uv run python bench_sphinx.py --pages 100 --roles 20 --files 32``.
The dummy builder includes document reading, directives and roles without HTML
theme costs. Corpus creation is outside the timed region.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import tracemalloc
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Any

from sphinx.testing.util import SphinxTestApp

from sphinxcontrib.fastraml import apis
from sphinxcontrib.fastraml.catalogue import Catalogue, Declared
from sphinxcontrib.fastraml.domain import RamlXRefRole


@dataclass(slots=True)
class Result:
    seconds: float = 0
    roles_seen: int = 0
    declarations_seen: int = 0
    mtime_calls: int = 0
    mtime_entries: int = 0
    mtime_seconds: float = 0
    warnings: int = 0
    allocated_peak: int = 0


def run_case(  # noqa: PLR0913, PLR0915 - one reproducible workload takes dimensions and installs counters
    root: Path,
    *,
    pages: int,
    roles: int,
    files: int,
    types: int = 0,
    mode: str = 'roles',
    builder: str = 'dummy',
    trace: bool = False,
) -> Result:
    """Build one generated site; return measurements and reach counters."""
    source = root / 'source'
    spec = root / 'spec'
    source.mkdir()
    spec.mkdir()
    (source / 'conf.py').write_text(
        f"extensions = ['sphinxcontrib.fastraml']\n"
        f"raml_apis = {{'probe': {{'path': {str(spec / 'api.raml')!r}, 'workspace_root': {str(spec)!r}}}}}\n"
        'raml_warn_unrendered = False\n',
        encoding='utf-8',
    )
    uses = ''.join(f'  lib{n}: lib{n}.raml\n' for n in range(files - 1))
    declarations = ''.join(f'  Type{n}: string\n' for n in range(types))
    (spec / 'api.raml').write_text(
        '#%RAML 1.0\ntitle: Probe\nuses:\n' + uses + 'types:\n  Thing: string\n' + declarations,
        encoding='utf-8',
    )
    for n in range(files - 1):
        (spec / f'lib{n}.raml').write_text('#%RAML 1.0 Library\ntypes:\n  Other: string\n', encoding='utf-8')
    directive = '.. raml:types::' if mode == 'types' else '.. raml:type:: Thing'
    (source / 'index.rst').write_text(
        'Index\n=====\n\n'
        + directive
        + '\n\n.. toctree::\n   :hidden:\n\n'
        + ''.join(f'   page{n}\n' for n in range(pages)),
        encoding='utf-8',
    )
    text = ':raml:type:`Thing`' if mode == 'roles' else 'Thing Thing Thing Thing'
    body = 'Page\n====\n\n' + ' '.join([text] * roles) + '\n'
    for n in range(pages):
        (source / f'page{n}.rst').write_text(body, encoding='utf-8')

    result = Result()
    original_mtimes = apis._mtimes  # noqa: SLF001 - deliberately measure this lookup path
    original_role = RamlXRefRole.process_link
    original_declaration = Catalogue.declaration

    def measure_mtimes(paths: tuple[Path, ...]) -> tuple[float, ...]:
        result.mtime_calls += 1
        result.mtime_entries += len(paths)
        start = perf_counter()
        try:
            return original_mtimes(paths)
        finally:
            result.mtime_seconds += perf_counter() - start

    def measure_role(self: RamlXRefRole, *args: Any, **kwargs: Any) -> tuple[str, str]:
        result.roles_seen += 1
        return original_role(self, *args, **kwargs)

    def measure_declaration(self: Catalogue, kind: Declared, key: str) -> Any:
        result.declarations_seen += 1
        return original_declaration(self, kind, key)

    apis._mtimes = measure_mtimes  # noqa: SLF001 - restored after the build
    RamlXRefRole.process_link = measure_role
    Catalogue.declaration = measure_declaration
    warning = StringIO()
    if trace:
        tracemalloc.start()
    start = perf_counter()
    try:
        app = SphinxTestApp(
            builder, srcdir=source, builddir=root / 'build', status=StringIO(), warning=warning, freshenv=True
        )
        try:
            app.build()
        finally:
            app.cleanup()
    finally:
        result.seconds = perf_counter() - start
        result.warnings = sum('WARNING' in line for line in warning.getvalue().splitlines())
        if trace:
            _, result.allocated_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        apis._mtimes = original_mtimes  # noqa: SLF001 - restore the instrumented function
        RamlXRefRole.process_link = original_role
        Catalogue.declaration = original_declaration
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages', type=int, default=100)
    parser.add_argument('--roles', type=int, default=20)
    parser.add_argument('--files', type=int, default=32)
    parser.add_argument('--types', type=int, default=0)
    parser.add_argument('--mode', choices=('roles', 'plain', 'types'), default='roles')
    parser.add_argument('--builder', default='dummy')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        result = run_case(
            Path(directory),
            pages=args.pages,
            roles=args.roles,
            files=args.files,
            types=args.types,
            mode=args.mode,
            builder=args.builder,
            trace=args.trace,
        )
    print(json.dumps({'workload': vars(args), 'measurement': asdict(result)}))  # noqa: T201 - CLI output


if __name__ == '__main__':
    main()
