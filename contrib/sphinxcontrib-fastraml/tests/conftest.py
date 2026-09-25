"""A throwaway Sphinx project per test, over the shared fixture.

`fixtures/sample` is what every consumer reads (docs/17 § 2), with the
workspace root one level up because it includes `../shared`.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sphinx.testing.util import SphinxTestApp

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'
#: How the tree keys the sample's root file under that workspace root.
ROOT = 'sample/api.raml'


def sample(name: str = 'books') -> str:
    """A `raml_apis` entry for the sample, as `conf.py` would write it."""
    return f'{name!r}: {{"path": {str(SAMPLE)!r}, "workspace_root": {str(FIXTURES)!r}}}'


@dataclass
class Built:
    app: SphinxTestApp
    warnings: list[str]

    def html(self, page: str = 'index') -> str:
        return (Path(self.app.outdir) / f'{page}.html').read_text(encoding='utf-8')

    def body(self, page: str = 'index') -> str:
        """The page's own content, without a toctree: `build` appends one to the index."""
        html = self.html(page)
        html = html[html.find('<div class="body"') : html.find('<div class="sphinxsidebar"')]
        toctree = html.find('<div class="toctree-wrapper')
        return html if toctree < 0 else html[:toctree]

    def text(self, page: str = 'index') -> str:
        """The page body as text, one run of whitespace to a space."""
        return ' '.join(re.sub(r'<[^>]+>', ' ', self.body(page)).split())

    def links(self, page: str = 'index') -> list[str]:
        return re.findall(r'<a class="reference internal" href="([^"]+)"', self.body(page))

    def objects(self) -> dict[tuple[str, str, str], object]:
        return self.app.env.get_domain('raml').data['objects']


@pytest.fixture
def build(tmp_path: Path) -> Callable[..., Built]:
    """Write `conf.py` and the pages, build them, and return the result."""

    def run(
        pages: dict[str, str], *, apis: str | None = None, conf: str = '', builder: str = 'html', parallel: int = 0
    ) -> Built:
        source = tmp_path / 'source'
        source.mkdir(exist_ok=True)
        (source / 'conf.py').write_text(
            f"extensions = ['sphinxcontrib.fastraml']\nraml_apis = {{{apis or sample()}}}\n{conf}\n",
            encoding='utf-8',
        )
        toctree = '\n'.join(f'   {name}' for name in pages if name != 'index')
        for name, body in pages.items():
            text = textwrap.dedent(body)
            if name == 'index' and toctree:
                text += f'\n.. toctree::\n\n{toctree}\n'
            (source / f'{name}.rst').write_text(text, encoding='utf-8')
        warning = StringIO()
        app = SphinxTestApp(
            builder, srcdir=source, builddir=tmp_path / 'build', warning=warning, freshenv=True, parallel=parallel
        )
        try:
            app.build()
        finally:
            app.cleanup()
        return Built(app, [line for line in warning.getvalue().splitlines() if 'WARNING' in line])

    return run
