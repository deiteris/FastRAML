"""What one API makes addressable, and the names an author writes for each.

Only the root file's namespace is addressable, plus any declaration by the file
that declares it:

    endpoint             /books/{isbn}
    method               GET /books/{isbn}           (the verb in any case)
    response             GET /books/{isbn} 404
    type                 Book  or  libs/money.raml#Money
    property             Book.isbn  or  libs/money.raml#Money.amount
    annotation-type      rateLimit  or  common.raml#internal
    security-scheme      oauth2  or  shared/machine.raml#machineToken
    documentation-item   Getting started              (its title)
    base-uri-parameter   region  or  {region}

A bare declaration name is the root file's. A library's names are reached by
file and not through the root's `uses:` key, because a key is the root file's
private spelling and renaming it would break every link in the prose.

Every name is turned into one canonical key before it is stored or looked up,
so `get /books` and `GET /books` are one target. The file part is the tree's
own key for a file -- relative to the workspace root, which defaults to the
root file's directory (docs/16 § 2) -- so the root file is `api.raml` in the
ordinary case.

Endpoints nest by path segment, as the viewer's navigation does: `/books/{isbn}`
is one level under `/books` whether the RAML declared it nested or not
(`walk.py` leaves path nesting to the consumer).
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Literal, cast

from .walk import is_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .tree import (
        DocumentationItem,
        Endpoint,
        EntryPoint,
        HttpMethod,
        Operation,
        Parameter,
        Response,
        SecurityScheme,
        Shape,
        ShapeNode,
    )
    from .walk import Tree

Kind = Literal[
    'api',
    'endpoint',
    'method',
    'response',
    'type',
    'property',
    'annotation-type',
    'security-scheme',
    'documentation-item',
    'base-uri-parameter',
]
Declared = Literal['type', 'annotation-type', 'security-scheme']

#: The declaration kinds, and the section of the tree each is listed in.
SECTIONS: dict[Declared, Literal['types', 'annotation_types', 'security_schemes']] = {
    'type': 'types',
    'annotation-type': 'annotation_types',
    'security-scheme': 'security_schemes',
}

METHODS: frozenset[str] = frozenset(('connect', 'delete', 'get', 'head', 'options', 'patch', 'post', 'put', 'trace'))


class Catalogue:
    """One API's tree, read for what can be named."""

    __slots__ = ('_declared_at', 'root_file', 'tree')

    def __init__(self, tree: Tree, *, root_file: str) -> None:
        self.tree = tree
        self.root_file = root_file
        #: A declaration's address, to its kind and key: what a `$ref` in a
        #: body or a property links to.
        self._declared_at: dict[str, tuple[Declared, str]] = {}
        for kind in SECTIONS:
            for key, node in self.declared(kind).items():
                # An alias is a bare link and has no address of its own, so a
                # reference always lands on what it aliases (docs/16 § 6.1).
                address = None if is_ref(node) else node.get('id')
                if isinstance(address, str):
                    self._declared_at.setdefault(address, (kind, key))

    # -- the API ---------------------------------------------------------------

    @property
    def entry(self) -> EntryPoint:
        entry = self.tree.document['entry_point']
        return entry if entry is not None else cast('EntryPoint', {'kind': 'API'})

    @property
    def title(self) -> str:
        return self.entry.get('title') or 'API'

    def base_uri_parameters(self) -> dict[str, Parameter]:
        return self.entry.get('base_uri_parameters', {})

    def documentation(self) -> list[DocumentationItem]:
        return self.entry.get('documentation', [])

    def documentation_item(self, title: str) -> DocumentationItem | None:
        found = [item for item in self.documentation() if item['title'] == title]
        return found[0] if len(found) == 1 else None

    def duplicate_titles(self) -> set[str]:
        """Titles RAML allowed twice. Neither can be a link target: which one would it be?"""
        counts = Counter(item['title'] for item in self.documentation())
        return {title for title, count in counts.items() if count > 1}

    # -- endpoints -------------------------------------------------------------

    @property
    def endpoints(self) -> dict[str, Endpoint]:
        return self.tree.document['endpoints']

    def under(self, path: str, depth: int) -> list[str]:
        """`path` and the declared endpoints below it, `depth` segments deep at most.

        A negative depth is no limit, as toctree's `:maxdepth:` reads it.
        """
        base = _segments(path)
        return [
            candidate
            for candidate in self.endpoints
            if (candidate == path or candidate.startswith(path.rstrip('/') + '/'))
            and (depth < 0 or _segments(candidate) - base <= depth)
        ]

    def operation(self, key: str) -> tuple[str, HttpMethod, Operation] | None:
        verb, _, path = key.partition(' ')
        endpoint = self.endpoints.get(path)
        method = cast('HttpMethod', verb.lower())
        operation = endpoint['operations'].get(method) if endpoint else None
        return None if operation is None else (path, method, operation)

    def response(self, key: str) -> Response | None:
        method, _, status = key.rpartition(' ')
        found = self.operation(method)
        return None if found is None else found[2]['responses'].get(status)

    # -- declarations ----------------------------------------------------------

    def declared(self, kind: Declared, file: str | None = None) -> dict[str, ShapeNode | SecurityScheme]:
        """Every declaration of one kind by key, in declaration order; one file's if given."""
        section = cast('dict[str, dict[str, ShapeNode | SecurityScheme]]', self.tree.document[SECTIONS[kind]])
        return {
            f'{source}#{name}': node
            for source, declarations in section.items()
            if file is None or source == file
            for name, node in declarations.items()
        }

    def declaration(self, kind: Declared, key: str) -> ShapeNode | SecurityScheme | None:
        file, _, name = key.partition('#')
        section = cast('dict[str, dict[str, ShapeNode | SecurityScheme]]', self.tree.document[SECTIONS[kind]])
        return section.get(file, {}).get(name)

    def shape(self, key: str) -> Shape | None:
        """A declared type's shape, following an alias to what it names."""
        node = self.declaration('type', key)
        found = self.tree.resolve(cast('ShapeNode', node)) if node is not None else None
        return cast('Shape', found) if found is not None and found.get('type') != 'recursive' else None

    def declared_at(self, address: str) -> tuple[Declared, str] | None:
        """The declaration an address is, if it is one."""
        return self._declared_at.get(address)

    def is_root(self, key: str) -> bool:
        return key.partition('#')[0] == self.root_file

    def label(self, key: str) -> str:
        """A declaration as prose names it: bare in the root file, by file otherwise."""
        return key.partition('#')[2] if self.is_root(key) else key

    # -- names -----------------------------------------------------------------

    def normalise(self, kind: Kind, target: str) -> str | None:  # noqa: PLR0911 - one return per kind
        """The canonical key for what an author wrote, or `None` when it is not a name of that kind."""
        target = ' '.join(target.split())
        if kind == 'endpoint':
            return target if target.startswith('/') else None
        if kind == 'method':
            verb, _, path = target.partition(' ')
            return f'{verb.upper()} {path}' if verb.lower() in METHODS and path.startswith('/') else None
        if kind == 'response':
            method, _, status = target.rpartition(' ')
            normal = self.normalise('method', method)
            return f'{normal} {status}' if normal and status else None
        if kind in SECTIONS:
            file, hash_, name = target.rpartition('#')
            return f'{file if hash_ else self.root_file}#{name}' if name else None
        if kind == 'property':
            file, hash_, rest = target.rpartition('#')
            name, dot, field = rest.partition('.')
            return f'{file if hash_ else self.root_file}#{name}.{field}' if name and dot and field else None
        if kind == 'base-uri-parameter':
            return target.removeprefix('{').removesuffix('}') or None
        return target or None

    def exists(self, kind: Kind, key: str) -> bool:  # noqa: PLR0911 - one return per kind
        if kind == 'api':
            return True
        if kind == 'endpoint':
            return key in self.endpoints
        if kind == 'method':
            return self.operation(key) is not None
        if kind == 'response':
            return self.response(key) is not None
        if kind in SECTIONS:
            return self.declaration(cast('Declared', kind), key) is not None
        if kind == 'property':
            type_key, _, field = key.partition('.')
            shape = self.shape(type_key)
            return shape is not None and shape['type'] == 'object' and field in (shape.get('properties') or {})
        if kind == 'documentation-item':
            return self.documentation_item(key) is not None
        return key in self.base_uri_parameters()

    def addressable(self) -> Iterator[tuple[Kind, str]]:
        """What a complete reference renders: the root namespace, and nothing below it.

        Responses and properties are rendered as part of their method and type,
        and a library's declarations only when something links to them, so
        none of those is listed here.
        """
        yield 'api', ''
        for path, endpoint in self.endpoints.items():
            yield 'endpoint', path
            for method in endpoint['operations']:
                yield 'method', f'{method.upper()} {path}'
        for kind in SECTIONS:
            for key in self.declared(kind, self.root_file):
                yield kind, key
        duplicates = self.duplicate_titles()
        for item in self.documentation():
            if item['title'] not in duplicates:
                yield 'documentation-item', item['title']


def _segments(path: str) -> int:
    return len([segment for segment in path.split('/') if segment])
