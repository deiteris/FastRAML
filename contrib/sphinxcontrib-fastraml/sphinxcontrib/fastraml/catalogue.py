"""What one API makes addressable, and the names an author writes for each.

Only the root file's namespace is addressable, plus any declaration by the file
that declares it:

    endpoint             /books/{isbn}
    method               GET /books/{isbn}           (the verb in any case)
    response             GET /books/{isbn} 404
    type                 Book  or  libs/money.raml#Money
    property             Book.isbn  or  libs/money.raml#Money.amount
    annotation-type      rateLimit  or  libs/common.raml#internal
    security-scheme      oauth2  or  libs/auth.raml#token
    documentation-item   Getting started              (its title)
    base-uri-parameter   region  or  {region}

A bare declaration name is the root file's. A library's names are reached by
file and not through the root's `uses:` key, because a key is the root file's
private spelling and renaming it would break every link in the prose. A
declaration included from a fragment (`machineToken: !include machine.raml`)
is named where it is declared, so it is the root file's.

Every name is turned into one canonical key before it is stored or looked up,
so `get /books` and `GET /books` are one target. The file part is relative to
the workspace root, which defaults to the root file's directory, as every
fastraml view names files (docs/16 § 2); the root file is `api.raml` in the
ordinary case.

Endpoints nest by path segment, as the viewer's navigation does: `/books/{isbn}`
is one level under `/books` whether the RAML declared it nested or not.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import unquote

from fastraml import APIFragment, BaseShape, ObjectShape, bound_base_uri

from .model import target, text, texts

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml import (
        DocumentationItem,
        EndPoint,
        Operation,
        Parameter,
        Raml,
        Response,
        SecurityScheme,
        SecuritySchemeDefinition,
    )

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
DECLARED: tuple[Declared, ...] = ('type', 'annotation-type', 'security-scheme')

METHODS: frozenset[str] = frozenset(('connect', 'delete', 'get', 'head', 'options', 'patch', 'post', 'put', 'trace'))


class Catalogue:
    """One API's effective model, read for what can be named."""

    __slots__ = ('_declarations', '_declared_at', '_workspace', 'raml', 'root_file')

    def __init__(self, raml: Raml) -> None:
        self.raml = raml
        # The workspace root, which defaults to the entry file's directory. For
        # an Overlay or Extension entry `location` is the root API's, which is
        # the namespace its names are in (docs/13 § 1).
        root = raml.workspace_root_uri or raml.location.rsplit('/', 1)[0]
        self._workspace = root if root.endswith('/') else root + '/'
        self.root_file = self.file(raml.location)
        #: A declaration's model id, to its kind and key: what a use of a
        #: declared type, scheme or annotation links to. Ids, as every fastraml
        #: view keys identity (docs/16 § 2): an unwrapped copy keeps its id.
        self._declarations = {kind: self._collect(kind) for kind in DECLARED}
        self._declared_at: dict[int, tuple[Declared, str]] = {}
        for kind in DECLARED:
            for key, node in self._declarations[kind].items():
                self._declared_at.setdefault(node.id, (kind, key))

    def file(self, uri: str) -> str:
        """A file as the views name it: relative to the workspace root, as a path.

        Decoded, so an author writes `my api.raml` for the file on disk rather
        than its URI spelling. Every file a parse can read is beneath the root,
        which is the boundary the loader enforces; a URI outside it is returned
        whole.
        """
        return unquote(uri.removeprefix(self._workspace)) if uri.startswith(self._workspace) else uri

    # -- the API ---------------------------------------------------------------

    @property
    def entry(self) -> APIFragment | None:
        entry = self.raml.entry_point
        return entry if isinstance(entry, APIFragment) else None

    @property
    def title(self) -> str:
        return text(self.entry.title if self.entry else None) or 'API'

    def value(self, field: str) -> list[str]:
        """One of the API's own values: `version`, `base_uri`, `protocols`, ...; empty when absent.

        `base_uri` is the one a caller sends requests to, `{version}` bound,
        which fastraml's `bound_base_uri` owns; `written_base_uri` is the text.
        """
        if field == 'base_uri':
            bound = bound_base_uri(self.entry) if self.entry is not None else None
            return [bound] if bound else []
        if field == 'written_base_uri':
            field = 'base_uri'
        found = getattr(self.entry, field, None)
        return texts(found) if isinstance(found, list) else [value] if (value := text(found)) else []

    def secured_by(self) -> list[SecurityScheme]:
        """The API's own `securedBy:`: the default its resources and methods started from.

        Each method already carries its effective list (docs/09 § A4); this is
        only what the root declared, which is what an overview reports.
        """
        return self.raml.global_secured_by

    def base_uri_parameters(self) -> dict[str, Parameter]:
        return self.entry.base_uri_parameters if self.entry else {}

    def documentation(self) -> list[DocumentationItem]:
        return self.entry.documentation if self.entry else []

    def documentation_item(self, title: str) -> DocumentationItem | None:
        found = [item for item in self.documentation() if text(item.title) == title]
        return found[0] if len(found) == 1 else None

    def duplicate_titles(self) -> set[str]:
        """Titles RAML allowed twice. Neither can be a link target: which one would it be?"""
        counts = Counter(text(item.title) for item in self.documentation())
        return {title for title, count in counts.items() if title is not None and count > 1}

    # -- endpoints -------------------------------------------------------------

    @property
    def endpoints(self) -> dict[str, EndPoint]:
        return self.raml.endpoints

    def under(self, path: str, depth: int) -> list[str]:
        """`path` and the endpoints below it, `depth` segments deep at most.

        A negative depth is no limit, as toctree's `:maxdepth:` reads it.
        """
        base = _segments(path)
        return [
            candidate
            for candidate in self.endpoints
            if (candidate == path or candidate.startswith(path.rstrip('/') + '/'))
            and (depth < 0 or _segments(candidate) - base <= depth)
        ]

    def operation(self, key: str) -> tuple[str, str, Operation] | None:
        verb, _, path = key.partition(' ')
        endpoint = self.endpoints.get(path)
        method = verb.lower()
        operation = endpoint.operations.get(method) if endpoint else None
        return None if operation is None else (path, method, operation)

    def response(self, key: str) -> Response | None:
        method, _, status = key.rpartition(' ')
        found = self.operation(method)
        if found is None:
            return None
        return {str(code): response for code, response in found[2].responses.items()}.get(status)

    # -- declarations ----------------------------------------------------------

    def declared(self, kind: Declared, file: str | None = None) -> dict[str, BaseShape | SecuritySchemeDefinition]:
        """Every declaration of one kind by key, in declaration order; one file's if given."""
        declarations = self._declarations[kind]
        return (
            dict(declarations)
            if file is None
            else {key: node for key, node in declarations.items() if key.partition('#')[0] == file}
        )

    def _collect(self, kind: Declared) -> dict[str, BaseShape | SecuritySchemeDefinition]:
        """Build the per-kind index once; a single declaration lookup must not rebuild its file."""
        out: dict[str, BaseShape | SecuritySchemeDefinition] = {}
        for uri, declarations in self._sections(kind):
            source = self.file(uri)
            out.update({f'{source}#{name}': node for name, node in declarations.items()})
        return out

    def _sections(self, kind: Declared) -> Iterator[tuple[str, dict[str, BaseShape | SecuritySchemeDefinition]]]:
        if kind == 'type':
            yield from cast(
                'dict[str, dict[str, BaseShape | SecuritySchemeDefinition]]', self.raml.fragment_types
            ).items()
        elif kind == 'annotation-type':
            yield from cast(
                'dict[str, dict[str, BaseShape | SecuritySchemeDefinition]]', self.raml.fragment_annotations
            ).items()
        else:
            for uri, fragment in self.raml.fragments.items():
                schemes = getattr(fragment, 'security_schemes', None)
                if schemes:
                    yield uri, schemes

    def declaration(self, kind: Declared, key: str) -> BaseShape | SecuritySchemeDefinition | None:
        return self._declarations[kind].get(key)

    def declared_at(self, entity_id: int) -> tuple[Declared, str] | None:
        """The declaration a model entity is, if it is one."""
        return self._declared_at.get(entity_id)

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
        if kind in DECLARED:
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
        if kind in DECLARED:
            return self.declaration(cast('Declared', kind), key) is not None
        if kind == 'property':
            type_key, _, field = key.partition('.')
            declared = self.declaration('type', type_key)
            shape = target(declared).shape if isinstance(declared, BaseShape) else None
            return isinstance(shape, ObjectShape) and field in (shape.properties or {})
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
            for method in endpoint.operations:
                yield 'method', f'{method.upper()} {path}'
        for kind in DECLARED:
            for key in self.declared(kind, self.root_file):
                yield kind, key
        duplicates = self.duplicate_titles()
        for item in self.documentation():
            title = text(item.title)
            if title is not None and title not in duplicates:
                yield 'documentation-item', title


def _segments(path: str) -> int:
    return len([segment for segment in path.split('/') if segment])
