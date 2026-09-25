"""A documentation site for the repository's sample API, built with sphinxcontrib-fastraml.

From `contrib/sphinxcontrib-fastraml`:

    uv run sphinx-build -W -b html examples/bookstore examples/bookstore/_build/html

`-W` makes every warning an error: a RAML error, a link to something that no
longer exists, or an endpoint no page documents all fail the build.
"""

from pathlib import Path

project = 'Bookstore API'
extensions = ['sphinxcontrib.fastraml']

#: The repository's shared worked document (`docs/17` § 2). It includes
#: `../shared`, so the workspace root is one level above it.
FIXTURES = Path(__file__).resolve().parents[4] / 'fixtures'
raml_apis = {
    'books': {'path': str(FIXTURES / 'sample' / 'api.raml'), 'workspace_root': str(FIXTURES)},
}

exclude_patterns = ['_build']
html_theme = 'alabaster'
