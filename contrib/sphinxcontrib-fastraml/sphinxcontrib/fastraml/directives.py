"""The directives an author writes, each rendering a part of one API.

Every directive takes `:api:` (else the page's `raml:api`, else the only API
configured) and `:no-index:`, which renders without registering targets --
for the copy in a tutorial of an entry whose reference is elsewhere.

A single-item directive's own content is the author's addition to the
generated entry: it is placed after the RAML's description and before the
generated fields, where a note about that item reads first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from docutils import nodes
from docutils.parsers.rst import directives
from sphinx import addnodes
from sphinx.util import logging
from sphinx.util.docutils import SphinxDirective

from . import apis
from .domain import current_api
from .render import DETAILS, Detail, Writer
from .steps import NO_BODY, Ask, Steps

if TYPE_CHECKING:
    from collections.abc import Callable

    from docutils.nodes import Node

    from .apis import Api
    from .catalogue import Declared

logger = logging.getLogger(__name__)


def _detail(argument: str | None) -> str:
    return directives.choice(argument or '', DETAILS)


def _depth(argument: str | None) -> int:
    """`-1` for no limit, as toctree's `:maxdepth:` reads it."""
    value = int(argument or '0')
    if value < -1:
        raise ValueError('depth is -1 (no limit) or a count of path segments')
    return value


def _methods(argument: str | None) -> set[str]:
    return {method.lower() for method in (argument or '').split()}


class CurrentApi(SphinxDirective):
    """`.. raml:api:: books` -- the API the rest of this page means. Renders nothing."""

    required_arguments = 1

    def run(self) -> list[Node]:
        name = self.arguments[0].strip()
        if name not in apis.names(self.env):
            logger.warning('no RAML API %r in raml_apis', name, location=self.get_location(), type='fastraml')
            return []
        self.env.ref_context['raml:api'] = name
        return []


class RamlDirective(SphinxDirective):
    #: What renders this directive's part of the API.
    writer: ClassVar[type[Writer]] = Writer
    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        'api': directives.unchanged_required,
        'no-index': directives.flag,
        'noindex': directives.flag,
    }

    def run(self) -> list[Node]:
        chosen = current_api(self.env, self.options.get('api'))
        if chosen is None:
            return self.warn('which RAML API? Several are configured: name one with :api: or raml:api')
        loaded = apis.api(self.env, chosen)
        if loaded is None:
            return self.warn(f'RAML API {chosen!r} is not configured, or could not be read')
        for path in loaded.files:
            self.env.note_dependency(str(path))
        index = 'no-index' not in self.options and 'noindex' not in self.options
        rendered = self.render(self.writer(self, loaded, index=index), loaded)
        if self.content:
            self.add_content(rendered)
        return rendered

    def render(self, writer: Writer, api: Api) -> list[Node]:
        raise NotImplementedError

    def warn(self, message: str) -> list[Node]:
        logger.warning(message, location=self.get_location(), type='fastraml')
        return []

    def add_content(self, rendered: list[Node]) -> None:
        """The author's text, after the RAML's own prose in the first entry."""
        entry = next((node for node in rendered if isinstance(node, addnodes.desc)), None)
        if entry is None:
            return
        content = next(child for child in entry.children if isinstance(child, addnodes.desc_content))
        added = self.parse_content_to_nodes()
        # The generated parts start at the first field list, rubric or nested
        # entry; everything before them is the RAML's own prose.
        generated = (nodes.field_list, nodes.rubric, addnodes.desc, addnodes.index)
        position = next(
            (at for at, child in enumerate(content.children) if isinstance(child, generated)),
            len(content.children),
        )
        content.children[position:position] = added
        for node in added:
            node.parent = content


class Overview(RamlDirective):
    """`.. raml:overview::` -- title, version, base URI and what applies to every call."""

    def render(self, writer: Writer, _api: Api) -> list[Node]:
        return writer.overview()


class Endpoints(RamlDirective):
    """`.. raml:endpoints::` -- every endpoint, or those a path pattern picks."""

    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        **RamlDirective.option_spec,
        'include': directives.unchanged_required,
        'exclude': directives.unchanged_required,
        'detail': _detail,
        'methods': _methods,
    }

    def render(self, writer: Writer, api: Api) -> list[Node]:
        include = re.compile(self.options.get('include', ''))
        exclude = re.compile(self.options['exclude']) if 'exclude' in self.options else None
        paths = [
            path
            for path in api.catalogue.endpoints
            if include.search(path) and not (exclude is not None and exclude.search(path))
        ]
        return self.endpoints(writer, paths)

    def endpoints(self, writer: Writer, paths: list[str]) -> list[Node]:
        detail = cast('Detail', self.options.get('detail', 'full'))
        return writer.endpoints(paths, detail, self.options.get('methods'))


class Endpoint(Endpoints):
    """`.. raml:endpoint:: /books` -- one endpoint, and `:depth:` levels below it."""

    required_arguments = 1
    final_argument_whitespace = True
    has_content = True
    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        **RamlDirective.option_spec,
        'depth': _depth,
        'detail': _detail,
        'methods': _methods,
    }

    def render(self, writer: Writer, api: Api) -> list[Node]:
        path = api.catalogue.normalise('endpoint', self.arguments[0])
        if path is None or not api.catalogue.exists('endpoint', path):
            return self.warn(f'no endpoint {self.arguments[0]!r} in RAML API {api.name!r}')
        return self.endpoints(writer, api.catalogue.under(path, self.options.get('depth', 0)))


class Method(RamlDirective):
    """`.. raml:method:: GET /books` -- one method; `:detail: request` leaves out responses."""

    required_arguments = 1
    final_argument_whitespace = True
    has_content = True
    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        **RamlDirective.option_spec,
        'detail': _detail,
    }

    def render(self, writer: Writer, api: Api) -> list[Node]:
        key = api.catalogue.normalise('method', self.arguments[0])
        found = api.catalogue.operation(key) if key is not None else None
        if found is None:
            return self.warn(f'no method {self.arguments[0]!r} in RAML API {api.name!r}')
        path, method, operation = found
        detail = cast('Detail', self.options.get('detail', 'full'))
        if detail == 'summary':
            return self.warn('a method has no summary of its own: use :detail: request or full')
        return writer.method(path, method, operation, detail)


class Declarations(RamlDirective):
    """`.. raml:types::` and its kin -- every declaration of one kind in one file."""

    kind: ClassVar[Declared]
    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        **RamlDirective.option_spec,
        'file': directives.unchanged_required,
        'detail': _detail,
    }

    def render(self, writer: Writer, api: Api) -> list[Node]:
        file = self.options.get('file', api.catalogue.root_file)
        keys = list(api.catalogue.declared(self.kind, file))
        if not keys:
            return self.warn(f'no {self.kind} declared in {file!r} of RAML API {api.name!r}')
        return writer.declarations(self.kind, keys, cast('Detail', self.options.get('detail', 'full')))


class Declaration(RamlDirective):
    """`.. raml:type:: Book` and its kin -- one declaration, by name or by `file#Name`."""

    kind: ClassVar[Declared]
    required_arguments = 1
    final_argument_whitespace = True
    has_content = True

    def render(self, writer: Writer, api: Api) -> list[Node]:
        key = api.catalogue.normalise(self.kind, self.arguments[0])
        if key is None or not api.catalogue.exists(self.kind, key):
            return self.warn(f'no {self.kind} {self.arguments[0]!r} in RAML API {api.name!r}')
        return writer.declaration(self.kind, key)


class Documentation(RamlDirective):
    """`.. raml:documentation::` -- the API's documentation items, as sections."""

    def render(self, writer: Writer, _api: Api) -> list[Node]:
        return writer.documentation()


class DocumentationItem(RamlDirective):
    """`.. raml:documentation-item:: Getting started` -- one of them, by title."""

    required_arguments = 1
    final_argument_whitespace = True

    def render(self, writer: Writer, api: Api) -> list[Node]:
        title = ' '.join(self.arguments[0].split())
        if api.catalogue.documentation_item(title) is None:
            return self.warn(f'no documentation item titled {title!r} in RAML API {api.name!r}, or two of them')
        return writer.documentation([title])


def _values(argument: str | None) -> dict[str, str]:
    """`name = value`, one per line: the value is text, read as the input takes it."""
    out: dict[str, str] = {}
    for line in (argument or '').splitlines():
        if not line.strip():
            continue
        name, equals, value = line.partition('=')
        if not equals or not name.strip():
            raise ValueError(f'expected "name = value", got {line.strip()!r}')
        out[name.strip()] = value.strip()
    return out


def _fields(argument: str | None) -> str:
    return directives.choice(argument or '', ('none', 'required', 'all'))


class Step(RamlDirective):
    """What `raml:send` and `raml:expect` share: the options, and where the author's text goes.

    The author's text comes first -- it is the step's instruction -- and the
    generated part follows it. Neither is ever a link target, so `:no-index:`
    changes nothing.
    """

    writer = Steps
    required_arguments = 1
    final_argument_whitespace = True
    has_content = True
    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        'api': directives.unchanged_required,
        'media': directives.unchanged_required,
        'with': directives.unchanged_required,
        'fields': _fields,
        'values': _values,
        'body': directives.path,
    }

    def ask(self) -> Ask | None:
        body = NO_BODY
        if 'body' in self.options:
            # Relative to the page, as `literalinclude` reads its file.
            relative, absolute = self.env.relfn2path(self.options['body'])
            self.env.note_dependency(relative)
            try:
                body = json.loads(Path(absolute).read_text(encoding='utf-8'))
            except (OSError, ValueError) as err:
                self.warn(f'the body file {self.options["body"]!r} could not be read as JSON: {err}')
                return None
        return Ask(
            security=self.options.get('security'),
            media=self.options.get('media'),
            optional=frozenset(self.options.get('with', '').split()),
            fields=self.options.get('fields'),
            values=self.options.get('values', {}),
            body=body,
        )

    def add_content(self, rendered: list[Node]) -> None:
        rendered[0:0] = self.parse_content_to_nodes()


class Send(Step):
    """`.. raml:send:: POST /books` -- what to send for one method, spelled out in place."""

    option_spec: ClassVar[dict[str, Callable[[str], object]]] = {
        **Step.option_spec,
        'security': directives.unchanged_required,
    }

    def render(self, writer: Writer, api: Api) -> list[Node]:
        key = api.catalogue.normalise('method', self.arguments[0])
        found = api.catalogue.operation(key) if key is not None else None
        if found is None:
            return self.warn(f'no method {self.arguments[0]!r} in RAML API {api.name!r}')
        ask = self.ask()
        if ask is None:
            return []
        path, method, operation = found
        return cast('Steps', writer).send(path, method, operation, ask)


class Expect(Step):
    """`.. raml:expect:: POST /books 201` -- what comes back, spelled out in place."""

    def render(self, writer: Writer, api: Api) -> list[Node]:
        key = api.catalogue.normalise('response', self.arguments[0])
        response = api.catalogue.response(key) if key is not None else None
        if key is None or response is None:
            return self.warn(f'no response {self.arguments[0]!r} in RAML API {api.name!r}')
        ask = self.ask()
        if ask is None:
            return []
        method, _, status = key.rpartition(' ')
        return cast('Steps', writer).expect(method, status, response, ask)


def _declarations(kind: Declared) -> type[Declarations]:
    return type(f'{kind.title().replace("-", "")}s', (Declarations,), {'kind': kind})


def _declaration(kind: Declared) -> type[Declaration]:
    return type(kind.title().replace('-', ''), (Declaration,), {'kind': kind})


DIRECTIVES: dict[str, type[SphinxDirective]] = {
    'api': CurrentApi,
    'overview': Overview,
    'endpoints': Endpoints,
    'endpoint': Endpoint,
    'method': Method,
    'types': _declarations('type'),
    'type': _declaration('type'),
    'annotation-types': _declarations('annotation-type'),
    'annotation-type': _declaration('annotation-type'),
    'security-schemes': _declarations('security-scheme'),
    'security-scheme': _declaration('security-scheme'),
    'documentation': Documentation,
    'documentation-item': DocumentationItem,
    'send': Send,
    'expect': Expect,
}
