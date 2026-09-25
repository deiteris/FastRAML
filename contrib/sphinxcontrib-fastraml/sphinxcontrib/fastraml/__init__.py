"""RAML 1.0 API references as native Sphinx content, read by fastraml.

    extensions = ['sphinxcontrib.fastraml']
    raml_apis = {'books': 'specs/books.raml'}

Adds the `raml` domain: directives that render endpoints, methods, types,
annotation types, security schemes and documentation items as entries of the
page they are written in, and roles that link to any of them from prose. See
`README.md` for the full set.

With `raml_warn_unrendered` (on by default), every item of the root file's
namespace that no page renders is a warning at the end of reading, so an
endpoint added to the RAML cannot go undocumented without the build saying so.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, cast

from sphinx.util import logging

from . import apis
from .directives import DIRECTIVES
from .domain import LABELS, RamlDomain

if TYPE_CHECKING:
    from sphinx.application import Sphinx
    from sphinx.environment import BuildEnvironment

    from .catalogue import Kind

try:
    __version__ = version('sphinxcontrib-fastraml')
except PackageNotFoundError:  # pragma: no cover - a source tree nobody installed
    __version__ = '0.0.0'

logger = logging.getLogger(__name__)


def check_rendered(app: Sphinx, env: BuildEnvironment) -> None:
    """What no page renders, once each (`raml_warn_unrendered`).

    Every item of each API's root namespace, and every other declaration the
    extension linked to -- a library's type used as a body, say.
    """
    if not app.config.raml_warn_unrendered:
        return
    domain = env.get_domain('raml')
    assert isinstance(domain, RamlDomain)  # noqa: S101 - registered in `setup`
    namespaces: dict[str, set[tuple[Kind, str]]] = {}
    for name in apis.names(env):
        loaded = apis.api(env, name)
        if loaded is None:
            continue
        namespaces[name] = set(loaded.catalogue.addressable())
        for kind, key in namespaces[name]:
            if (kind, name, key) not in domain.objects:
                what = f'RAML {LABELS[kind]} {key!r}' if key else 'its overview'
                logger.warning('%s of API %r is rendered on no page', what, name, type='fastraml', subtype='unrendered')
    # What is outside the root namespace -- a library's type, a response --
    # is required only once something links to it: a body of `Page` is a
    # link with nowhere to go until some page renders `Page`.
    for (linked_kind, api, target), docnames in sorted(domain.linked.items()):
        if (linked_kind, api, target) not in domain.objects and (linked_kind, target) not in namespaces.get(api, set()):
            logger.warning(
                'RAML %s %r of API %r is linked from %s but rendered on no page',
                LABELS[cast('Kind', linked_kind)],
                target,
                api,
                min(docnames),
                type='fastraml',
                subtype='unrendered',
            )


def setup(app: Sphinx) -> dict[str, Any]:
    app.add_config_value('raml_apis', default={}, rebuild='env', types=frozenset({dict}))
    app.add_config_value('raml_warn_unrendered', default=True, rebuild='env', types=frozenset({bool}))
    # Assigned here rather than in the class body: the directives read the
    # domain's helpers, so the domain module cannot import them.
    RamlDomain.directives = dict(DIRECTIVES)
    app.add_domain(RamlDomain)
    app.connect('config-inited', apis.resolve)
    app.connect('builder-inited', apis.load_all)
    app.connect('source-read', apis.clear_page_cache)
    app.connect('env-check-consistency', check_rendered)
    return {'version': __version__, 'env_version': 1, 'parallel_read_safe': True, 'parallel_write_safe': True}
