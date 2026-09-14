"""Copy the built `viewer/dist` into the package before a build.

`viewer/dist` is generated and gitignored, so it cannot simply be committed
here. Two situations have to work:

* **A checkout.** `../../viewer/dist` exists once `npm run build` has run.
  Copy it in, every build, so a stale bundle cannot ship.
* **An sdist.** There is no `../../viewer`, because the sdist contains only
  this project. The assets were copied in when the sdist was built, so they are
  already present and the copy is skipped.

Anything else is a build with no bundle to ship, and it fails here rather than
producing a wheel whose `static_dir()` raises at runtime.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ViewerAssets(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = 'viewer-assets'

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:  # noqa: ARG002
        here = Path(self.root)
        target = here / 'fastraml_viewer' / 'static'
        source = here.parent.parent / 'viewer' / 'dist'

        if source.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target)
        elif not (target / 'index.html').is_file():
            message = (
                f'no viewer bundle: neither {source} nor {target} holds one. '
                'Run `npm ci && npm run build` in viewer/ before building this package.'
            )
            raise RuntimeError(message)

        # No `force_include`. Copying into the package directory is the whole
        # job: `artifacts` in pyproject.toml already overrides the gitignore for
        # `static/**`, and naming the files twice makes hatchling refuse the
        # build -- "a second file is being added at the same path".
