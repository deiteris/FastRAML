"""Rewrite the committed golden record.

    uv run python tests/regenerate_golden.py

Read the diff before committing it. A golden that changed without a template or
a spelling changing is a golden that recorded something it should not have.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# The line above is what makes the next two importable when run as a script.
from conftest import FIXTURE, GOLDEN, WORKSPACE

from raml_codegen import Settings, generate_from_path


def main() -> None:
    generated = generate_from_path(FIXTURE, 'python', Settings(), workspace_root=WORKSPACE)
    if GOLDEN.exists():
        shutil.rmtree(GOLDEN)
    GOLDEN.mkdir(parents=True)
    # Empty files -- `py.typed` -- are left out: git records them and the
    # comparison would have to special-case reading them back as ''.
    written = [name for name, text in generated.files.items() if text]
    for name in written:
        path = GOLDEN / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated.files[name], encoding='utf-8')
    print(f'wrote {len(written)} files to {GOLDEN}')  # noqa: T201


if __name__ == '__main__':
    main()
