"""The `raml` domain: where each rendered item is, and the roles that link to it.

A target is stored under `(kind, api, key)`, with `key` in the canonical form
`catalogue.py` defines, so every spelling an author may write resolves to one
entry. A role picks its API from an `api:` prefix, else from the page's
`raml:api`, else from the only one configured.

Links the extension writes itself -- a body's type, a method's security
scheme -- do not warn when they find no target (`refwarn` false): each is
left as plain text, and the declaration is named once by the
`raml_warn_unrendered` check in `__init__.py` rather than at every place it
is used. A link an author writes warns, as any Sphinx role does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, cast

from docutils import nodes
from sphinx.domains import Domain, Index, IndexEntry, ObjType
from sphinx.locale import _
from sphinx.roles import XRefRole
from sphinx.util import logging
from sphinx.util.docutils import SphinxRole
from sphinx.util.nodes import make_refnode

from . import apis

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Set as AbstractSet

    from docutils.nodes import Element, Node, system_message
    from sphinx.addnodes import pending_xref
    from sphinx.builders import Builder
    from sphinx.environment import BuildEnvironment

    from .catalogue import Kind

logger = logging.getLogger(__name__)

#: How each kind is named in the index and in search results.
LABELS: dict[Kind, str] = {
    'api': 'API',
    'endpoint': 'endpoint',
    'method': 'method',
    'response': 'response',
    'type': 'type',
    'property': 'property',
    'annotation-type': 'annotation type',
    'security-scheme': 'security scheme',
    'documentation-item': 'documentation',
    'base-uri-parameter': 'base URI parameter',
}


class Target(NamedTuple):
    docname: str
    anchor: str
    #: What a link shows when the author gave no title, and search lists.
    display: str


def current_api(env: BuildEnvironment, prefix: str | None = None) -> str | None:
    """The API a name belongs to: its prefix, the page's `raml:api`, or the only one."""
    if prefix is not None:
        return prefix
    chosen = env.ref_context.get('raml:api')
    if isinstance(chosen, str):
        return chosen
    configured = apis.names(env)
    return configured[0] if len(configured) == 1 else None


def split_api(env: BuildEnvironment, text: str) -> tuple[str | None, str]:
    """`books:GET /books` into its API and name; the prefix only when it names an API.

    A colon is a namespace separator only before a configured name, so a path
    that happens to hold one is not read as a prefix.
    """
    head, colon, rest = text.partition(':')
    if colon and head in apis.names(env):
        return head, rest
    return None, text


class RamlXRefRole(XRefRole):
    """A link to one rendered item, by the name an author writes for it."""

    def process_link(
        self,
        env: BuildEnvironment,
        refnode: Element,
        has_explicit_title: bool,  # noqa: FBT001 - Sphinx's signature
        title: str,
        target: str,
    ) -> tuple[str, str]:
        kind = cast('Kind', refnode['reftype'])
        if kind == 'api':
            # The role's text is the API's namespace, and the link shows its title.
            chosen = current_api(env, target.strip() or None)
            refnode['raml:api'] = chosen
            loaded = apis.api(env, chosen) if chosen is not None else None
            if not has_explicit_title and loaded is not None:
                title = loaded.catalogue.title
            return title, ''
        prefix, name = split_api(env, target)
        chosen = current_api(env, prefix)
        refnode['raml:api'] = chosen
        if not has_explicit_title:
            title = split_api(env, title)[1]
        loaded = apis.api(env, chosen) if chosen is not None else None
        key = loaded.catalogue.normalise(kind, name) if loaded is not None else None
        return title, key if key is not None else name


class ValueRole(SphinxRole):
    """One of an API's own values -- its version, say -- written into the prose.

    The text is the API's namespace (`` :raml:version:`books` ``): a value has
    no name of its own to write, and a role cannot be empty.
    """

    def __init__(self, field: str, *, literal: bool = False) -> None:
        super().__init__()
        self.field = field
        self.literal = literal

    def run(self) -> tuple[list[Node], list[system_message]]:
        chosen = current_api(self.env, self.text.strip() or None)
        loaded = apis.api(self.env, chosen) if chosen is not None else None
        if loaded is None:
            logger.warning(
                'no RAML API %r to read %s from', chosen, self.field, location=self.get_location(), type='fastraml'
            )
            return [nodes.inline(self.rawtext, self.rawtext)], []
        for path in loaded.files:
            self.env.note_dependency(str(path))
        value = loaded.catalogue.entry.get(self.field)
        text = ', '.join(value) if isinstance(value, list) else str(value or '')
        if not text:
            logger.warning(
                'RAML API %r declares no %s', chosen, self.field, location=self.get_location(), type='fastraml'
            )
        node: Node = nodes.literal(text, text) if self.literal else nodes.inline(text, text)
        return [node], []


class RamlIndex(Index):
    """Every endpoint and its methods, by API: HTTP routing, the RAML way."""

    name = 'index'
    localname = _('RAML API Index')
    shortname = _('RAML API')

    def generate(self, docnames: Iterable[str] | None = None) -> tuple[list[tuple[str, list[IndexEntry]]], bool]:
        domain = cast('RamlDomain', self.domain)
        wanted = None if docnames is None else set(docnames)
        content: dict[str, list[IndexEntry]] = {}
        for (kind, api, _key), target in sorted(domain.objects.items(), key=lambda item: (item[0][1], _route(item[0]))):
            if kind not in {'endpoint', 'method'} or (wanted is not None and target.docname not in wanted):
                continue
            subtype = 2 if kind == 'method' else 1
            content.setdefault(api, []).append(
                IndexEntry(target.display, subtype, target.docname, target.anchor, '', '', LABELS[cast('Kind', kind)])
            )
        return sorted(content.items()), True


def _route(key: tuple[str, str, str]) -> tuple[str, int, str]:
    """A method sorts right after its endpoint, so it reads as a sub-entry of it."""
    kind, _api, name = key
    if kind == 'method':
        verb, _, path = name.partition(' ')
        return path, 1, verb
    return name, 0, ''


class RamlDomain(Domain):
    name = 'raml'
    label = 'RAML'
    object_types = {kind: ObjType(_(label), kind) for kind, label in LABELS.items()}  # noqa: RUF012 - Sphinx's attribute
    roles = {  # noqa: RUF012 - Sphinx's attribute
        **{kind: RamlXRefRole(warn_dangling=True) for kind in LABELS},
        'version': ValueRole('version'),
        'title': ValueRole('title'),
        'base-uri': ValueRole('base_uri', literal=True),
        'protocols': ValueRole('protocols'),
        'media-type': ValueRole('media_types', literal=True),
    }
    indices = [RamlIndex]  # noqa: RUF012 - Sphinx's attribute
    initial_data = {'objects': {}, 'linked': {}}  # noqa: RUF012 - Sphinx's attribute
    data_version = 2

    @property
    def objects(self) -> dict[tuple[str, str, str], Target]:
        return cast('dict[tuple[str, str, str], Target]', self.data.setdefault('objects', {}))

    @property
    def linked(self) -> dict[tuple[str, str, str], str]:
        """What the extension's own links point at, to the first page linking to each."""
        return cast('dict[tuple[str, str, str], str]', self.data.setdefault('linked', {}))

    def note_link(self, kind: Kind, api: str, key: str, docname: str) -> None:
        self.linked.setdefault((kind, api, key), docname)

    def note_object(self, kind: Kind, api: str, key: str, target: Target, location: Any = None) -> None:
        """Record where one item is rendered; a second place is a warning, and loses."""
        existing = self.objects.get((kind, api, key))
        if existing is not None and existing.docname != target.docname:
            logger.warning(
                'RAML %s %r of API %r is rendered twice; this one is also in %s -- use :no-index: on one',
                LABELS[kind],
                key or api,
                api,
                existing.docname,
                location=location,
                type='fastraml',
                subtype='duplicate',
            )
            return
        self.objects[kind, api, key] = target

    def clear_doc(self, docname: str) -> None:
        for name, target in list(self.objects.items()):
            if target.docname == docname:
                del self.objects[name]
        for name, linking in list(self.linked.items()):
            if linking == docname:
                del self.linked[name]

    def merge_domaindata(self, docnames: AbstractSet[str], otherdata: dict[str, Any]) -> None:
        for key, target in otherdata['objects'].items():
            if target.docname in docnames:
                self.objects[key] = target
        for key, linking in otherdata['linked'].items():
            if linking in docnames:
                self.linked.setdefault(key, linking)

    def resolve_xref(
        self,
        env: BuildEnvironment,
        fromdocname: str,
        builder: Builder,
        typ: str,
        target: str,
        node: pending_xref,
        contnode: Element,
    ) -> nodes.reference | None:
        found = self.objects.get((typ, node.get('raml:api') or '', target))
        if found is None:
            return None
        return make_refnode(builder, fromdocname, found.docname, found.anchor, contnode, found.display)

    def resolve_any_xref(
        self,
        env: BuildEnvironment,
        fromdocname: str,
        builder: Builder,
        target: str,
        node: pending_xref,
        contnode: Element,
    ) -> list[tuple[str, nodes.reference]]:
        prefix, name = split_api(env, target)
        chosen = current_api(env, prefix) or ''
        loaded = apis.api(env, chosen) if chosen else None
        out: list[tuple[str, nodes.reference]] = []
        for kind in LABELS:
            key = loaded.catalogue.normalise(kind, name) if loaded is not None else name
            found = self.objects.get((kind, chosen, key or name))
            if found is not None and kind != 'api':
                ref = make_refnode(builder, fromdocname, found.docname, found.anchor, contnode, found.display)
                out.append((f'raml:{kind}', ref))
        return out

    def get_objects(self) -> Iterable[tuple[str, str, str, str, str, int]]:
        for (kind, api, key), target in self.objects.items():
            yield f'{api}:{key}' if key else api, target.display, kind, target.docname, target.anchor, 1
