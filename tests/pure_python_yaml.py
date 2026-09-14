"""Force the pure-Python YAML backend for one CI job.

Loaded with `pytest -p tests.pure_python_yaml`. `yamlnode` picks a backend once,
at import, from whether `yaml.CSafeLoader` exists — so the C extension has to be
unreachable before anything imports `yaml`, and a `-p` plugin is imported before
collection starts.

**Reinstalling PyYAML from source does not do this.** `--no-binary :all:` forces
a source build; that build still links libyaml wherever the headers are present,
which is everywhere CI runs. The job spent a minute compiling and then asserted
on `libyaml` and failed.

Blocking the extension costs nothing and cannot silently stop working: this
module checks the backend itself, so the job fails here rather than passing
while testing the wrong one.
"""

from __future__ import annotations

import sys
from importlib.abc import MetaPathFinder
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType

#: `yaml/__init__.py` reaches the C loaders through `yaml.cyaml`, which imports
#: `yaml._yaml`; refusing that leaves `__with_libyaml__` false and no
#: `CSafeLoader`, which is exactly what a machine without libyaml looks like.
_BLOCKED = frozenset({'_yaml', 'yaml._yaml'})


class _NoLibyaml(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname in _BLOCKED:
            msg = f'{fullname} is blocked: this run must exercise the pure-Python backend'
            raise ImportError(msg)
        return None


def _install() -> None:
    sys.meta_path.insert(0, _NoLibyaml())
    # Anything already imported chose its backend under the old rules.
    for name in [n for n in sys.modules if n in _BLOCKED or n == 'yaml' or n.startswith(('yaml.', 'fastraml'))]:
        del sys.modules[name]

    import fastraml

    if fastraml.backend_name() != 'python':
        msg = f'the pure-Python backend was not selected: {fastraml.backend_name()}'
        raise RuntimeError(msg)


_install()
