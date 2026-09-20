"""Rewrite the committed golden record, for every target.

    uv run python tests/regenerate_golden.py

Reads `tests/api.json`, the committed tree. To refresh that instead, run
`fastraml tree fixtures/sample/api.raml -w fixtures` from the repository root;
the root's `tests/unit/test_bindings.py` fails while it is stale.

Read the diff before committing. A golden that changed without a template or a
spelling changing is a golden that recorded something it should not have.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# The line above is what makes the next two importable when run as a script.
from conftest import GOLDEN, TARGETS, TREE

from raml_codegen import generate


def main() -> None:
    document = json.loads(TREE.read_text(encoding='utf-8'))
    for target, settings in TARGETS.items():
        generated = generate(document, target, settings)
        destination = GOLDEN / target
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        # Empty files -- `py.typed` -- are left out: git records them and the
        # comparison would have to special-case reading them back as ''.
        written = [name for name, text in generated.files.items() if text]
        for name in written:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated.files[name], encoding='utf-8')
        print(f'{target}: wrote {len(written)} files to {destination}')  # noqa: T201


if __name__ == '__main__':
    main()
