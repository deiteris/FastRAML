"""Join several RAML API documents into one (docs/20).

Every definition an input declares is added next to the others'. Two entries
with one identity are identical, and kept once, or a conflict. Nothing is merged
into an existing entry and nothing is renamed, so no reference is rewritten.

Each input is read from its own parse's source trees. On reading, every
`!include` argument is replaced by the absolute URI it names, so a node carries
its meaning wherever it moves: comparing two entries, reading through an
include, and writing paths relative to the output need no record of which file
a node came from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.join.baseuri import CreatedEndpoint, common_segments, plan_created, split_base_uri, uri_variables
from fastraml.join.compare import Difference, IncludeReader, Side, first_difference
from fastraml.join.paths import Rebaser
from fastraml.join.templates import (
    Application,
    is_media_type_map,
    reads_resource_path,
    sets_key,
    template_applications,
    template_bodies_without_media_type,
)
from fastraml.join.writer import write_raml
from fastraml.parser.entry import ParseOptions, parse_from_path, parse_from_string
from fastraml.parser.fragments import APIFragment, FragmentKind
from fastraml.parser.includes import resolve_ref_uri, strip_uri_suffix
from fastraml.parser.source_ir import METHODS
from fastraml.uris import file_uri_to_path, is_file_uri, path_to_file_uri
from fastraml.yamlnode import (
    TAG_INCLUDE,
    TAG_MAP,
    TAG_SEQ,
    TAG_STR,
    Node,
    NodeKind,
    is_null,
    node_error,
    pairs,
    with_content,
    with_value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.registry import Raml

__all__ = ['BaseUriOverride', 'JoinOptions', 'join']

#: The name maps combined by name, and the `kind` a conflict reports for each.
_NAME_MAPS: Final = (
    ('types', 'type'),
    ('annotationTypes', 'annotationType'),
    ('traits', 'trait'),
    ('resourceTypes', 'resourceType'),
    ('securitySchemes', 'securityScheme'),
)
#: Root defaults (docs/20 § 5), in output order.
_DEFAULTS: Final = ('protocols', 'mediaType', 'securedBy')
#: The root values an option may give (docs/20 § 3.1), in output order.
_SINGLE: Final = ('title', 'description', 'version')


@dataclass(frozen=True, slots=True)
class BaseUriOverride:
    """An input's base URI, replacing its own (docs/20 § 6.1)."""

    uri: str
    #: A `baseUriParameters` mapping; `None` keeps the input's own declarations.
    parameters: Node | None = None


@dataclass(frozen=True, slots=True)
class JoinOptions:
    """What `join` needs besides its inputs."""

    title: str | None = None
    version: str | None = None
    #: `''` omits `description`.
    description: str | None = None
    #: Keyed by the input's `file://` URI.
    base_uris: Mapping[str, BaseUriOverride] = field(default_factory=dict)
    #: Where the result will be written; paths are made relative to its
    #: directory. `None` means a file in the current directory.
    output: str | os.PathLike[str] | None = None
    parse: ParseOptions = field(default_factory=ParseOptions)


# -- inputs -------------------------------------------------------------------


@dataclass(slots=True, eq=False)
class _Input:
    path: str
    uri: str
    raml: Raml
    api: APIFragment
    reader: IncludeReader
    #: Root keys to (key, value), values with absolute include arguments.
    fields: dict[str, tuple[Node, Node]] = field(default_factory=dict)
    #: Expanded include targets, by absolute URI.
    expanded: dict[str, Node] = field(default_factory=dict)
    #: Root defaults to write onto this input's methods and bodies.
    push: dict[str, Node] = field(default_factory=dict)
    #: Applications per authored resource, computed once on demand.
    applications: list[Application] | None = None


@dataclass(slots=True, eq=False)
class _Entry:
    """One named thing an input declares, with where it was written."""

    input: _Input
    file: str
    key: Node
    value: Node


def _absolutize(node: Node, file_uri: str, reader: IncludeReader) -> Node:
    """`node` with every `!include` argument replaced by its absolute URI."""
    if node.tag == TAG_INCLUDE:
        target = reader.target(node, file_uri)
        return node if target == node.value else with_value(node, target)
    if node.kind is NodeKind.SCALAR:
        return node
    content = [_absolutize(child, file_uri, reader) for child in node.content]
    if all(new is old for new, old in zip(content, node.content, strict=True)):
        return node
    return with_content(node, content)


def _expand(source: _Input, node: Node) -> tuple[Node, str | None]:
    """An include's content, absolutized, and the file it came from; else `node`."""
    if node.tag != TAG_INCLUDE:
        return node, None
    target = node.value
    file_uri = strip_uri_suffix(target)
    cached = source.expanded.get(target)
    if cached is None:
        cached = source.expanded[target] = _absolutize(source.reader.content(target), file_uri, source.reader)
    return cached, file_uri


def _read_inputs(paths: Sequence[str | os.PathLike[str]], options: ParseOptions) -> list[_Input]:
    errors = Accumulator()
    inputs: list[_Input] = []
    parse_options = replace(options, retain_source=True)
    for path in paths:
        try:
            raml = parse_from_path(path, parse_options)
        except RamlError as err:
            errors.add(err)
            continue
        uri = path_to_file_uri(Path(path).absolute())
        kind = raml.extensions[-1].kind if raml.extensions else getattr(raml.entry_point, 'kind', None)
        api = raml.entry_point
        if kind is not FragmentKind.API or not isinstance(api, APIFragment):
            errors.add(
                RamlError.new(
                    'unexpected fragment kind',
                    uri,
                    kind=ErrorKind.PARSING,
                    info={'expected': 'API', 'found': str(kind)},
                )
            )
            continue
        reader = IncludeReader(raml)
        source = _Input(os.fspath(path), uri, raml, api, reader)
        root = raml.source_nodes[uri]
        for key, value in pairs(root):
            name = 'types' if key.value == 'schemas' else key.value
            if key.value == 'uses':
                source.fields[name] = (key, value)
            else:
                source.fields[name] = (key, _absolutize(value, uri, reader))
        inputs.append(source)
    errors.raise_if_any()
    return inputs


# -- identical entries and conflicts (docs/20 § 4) ------------------------------


def _difference(one: _Entry, other: _Entry) -> Difference | None:
    return first_difference(
        Side(one.value, one.file, one.input.reader), Side(other.value, other.file, other.input.reader)
    )


def _where(entry: _Entry) -> str:
    return f'{entry.file}:{entry.key.line}:{entry.key.column}'


def _conflict(kind: str, name: str, earlier: _Entry, later: _Entry, difference: Difference) -> RamlError:
    return node_error(
        'join conflict',
        later.file,
        later.key,
        info={'kind': kind, 'name': name, 'other': _where(earlier), 'at': '/'.join(difference.path)},
    )


class _Names:
    """One name map of the output, in first-declared order."""

    __slots__ = ('_errors', 'entries', 'kind')

    def __init__(self, kind: str, errors: Accumulator) -> None:
        self.kind = kind
        self.entries: dict[str, _Entry] = {}
        self._errors = errors

    def add(self, name: str, entry: _Entry) -> None:
        earlier = self.entries.get(name)
        if earlier is None:
            self.entries[name] = entry
            return
        difference = _difference(earlier, entry)
        if difference is not None:
            self._errors.add(_conflict(self.kind, name, earlier, entry, difference))


def _map_entries(source: _Input, name: str) -> Iterator[tuple[str, _Entry]]:
    """The entries of one root name map."""
    found = source.fields.get(name)
    if found is None or found[1].kind is not NodeKind.MAPPING:
        return
    for key, item in pairs(found[1]):
        yield key.value, _Entry(source, source.uri, key, item)


# -- root defaults (docs/20 § 5) ------------------------------------------------


def _same(one: _Entry, other: _Entry) -> bool:
    return _difference(one, other) is None


def _place_defaults(inputs: list[_Input]) -> dict[str, _Entry]:
    """The root defaults the output keeps; each input's others go on its `push`."""
    kept: dict[str, _Entry] = {}
    for name in _DEFAULTS:
        values = [
            None if found is None else _Entry(source, source.uri, *found)
            for source in inputs
            for found in (source.fields.get(name),)
        ]
        present = [value for value in values if value is not None]
        if not present:
            continue
        if len(present) == len(values) and all(_same(present[0], value) for value in present[1:]):
            kept[name] = present[0]
            continue
        for value in present:
            value.input.push[name] = value.value
    return kept


def _media_types(node: Node) -> list[str]:
    if node.kind is NodeKind.SEQUENCE:
        return [item.value for item in node.content]
    return [node.value]


def _scalar(value: str) -> Node:
    return Node(NodeKind.SCALAR, TAG_STR, value)


def _mapping(content: list[Node], model: Node | None = None) -> Node:
    if model is not None and model.kind is NodeKind.MAPPING:
        return with_content(model, content)
    return Node(NodeKind.MAPPING, TAG_MAP, '', content)


class _Pusher:
    """Writes one input's root defaults onto its methods and bodies (docs/20 § 5.2)."""

    __slots__ = ('_media', '_push', '_source')

    def __init__(self, source: _Input) -> None:
        self._source = source
        self._push = source.push
        media = source.push.get('mediaType')
        self._media = _media_types(media) if media is not None else []

    def _wrap(self, body: Node) -> Node:
        peek, _ = _expand(self._source, body)
        if is_media_type_map(peek):
            return body
        content: list[Node] = []
        for media_type in self._media:
            content += [_scalar(media_type), body]
        return _mapping(content)

    def _bodies(self, holder: Node) -> Node:
        """`holder` with its `body` wrapped, and with each response's."""
        content: list[Node] = []
        changed = False
        for key, value in pairs(holder):
            new = value
            if key.value == 'body':
                new = self._wrap(value)
            elif key.value == 'responses' and value.kind is NodeKind.MAPPING:
                items: list[Node] = []
                for code, response in pairs(value):
                    items += [code, self._bodies(response) if response.kind is NodeKind.MAPPING else response]
                if any(new_item is not old for new_item, old in zip(items, value.content, strict=True)):
                    new = _mapping(items, value)
            changed = changed or new is not value
            content += [key, new]
        return _mapping(content, holder) if changed else holder

    def method(self, method: Node, *, resource_secured: bool) -> Node:
        if method.kind is not NodeKind.MAPPING and not is_null(method):
            return method
        names = {key.value for key, _ in pairs(method)} if method.kind is NodeKind.MAPPING else set()
        base = method if method.kind is NodeKind.MAPPING else _mapping([])
        if self._media:
            base = self._bodies(base)
        added: list[Node] = []
        for name in ('protocols', 'securedBy'):
            pushed = self._push.get(name)
            if pushed is None or name in names or (name == 'securedBy' and resource_secured):
                continue
            added += [_scalar(name), pushed]
        if not added and (base is method or not base.content):
            return method
        return _mapping([*base.content, *added], base)

    def resource(self, resource: Node) -> Node:
        if resource.kind is not NodeKind.MAPPING:
            return resource
        secured = any(key.value == 'securedBy' for key, _ in pairs(resource))
        content: list[Node] = []
        changed = False
        for key, item in pairs(resource):
            new = item
            if key.value in METHODS:
                new = self.method(item, resource_secured=secured)
            elif key.value.startswith('/'):
                new = self.resource(item)
            changed = changed or new is not item
            content += [key, new]
        return _mapping(content, resource) if changed else resource


# -- templates (docs/20 § 5.3, § 6.4) ---------------------------------------------


def _authored_resources(source: _Input) -> list[Node]:
    """Every resource node the input wrote, nested ones included."""
    found: list[Node] = []
    pending = [value for name, (_, value) in source.fields.items() if name.startswith('/')]
    while pending:
        resource = pending.pop()
        found.append(resource)
        if resource.kind is NodeKind.MAPPING:
            pending.extend(item for key, item in pairs(resource) if key.value.startswith('/'))
    return found


def _applications(source: _Input) -> list[Application]:
    if source.applications is None:
        source.applications = template_applications(source.api, source.uri, _authored_resources(source))
    return source.applications


def _check_pushed_templates(source: _Input, errors: Accumulator) -> None:
    """`join default reaches template` for each default a template would change."""
    for application in _applications(source):
        resource = application.resource
        secured = resource.kind is NodeKind.MAPPING and any(key.value == 'securedBy' for key, _ in pairs(resource))
        for name in source.push:
            reasons: list[tuple[str, str]] = [(unresolved, 'parameter') for unresolved in application.unresolved]
            for applied in application.applied:
                definition = applied.definition
                if name != 'mediaType' and sets_key(definition, name):
                    reasons.append((applied.name, 'sets'))
                if name == 'mediaType' and template_bodies_without_media_type(definition):
                    reasons.append((applied.name, 'body'))
            if name != 'mediaType' and application.contributed_methods and not (name == 'securedBy' and secured):
                reasons.extend(
                    (applied.name, 'method')
                    for applied in application.applied
                    if applied.definition.source is not None
                    and any(key.value in application.contributed_methods for key, _ in pairs(applied.definition.source))
                )
            for template, reason in dict.fromkeys(reasons):
                errors.add(
                    node_error(
                        'join default reaches template',
                        source.uri,
                        resource,
                        info={'property': name, 'template': template, 'reason': reason},
                    )
                )


def _check_resource_path(source: _Input, errors: Accumulator) -> None:
    for application in _applications(source):
        for applied in application.applied:
            if reads_resource_path(applied.definition):
                errors.add(
                    node_error(
                        'join resource path changes',
                        source.uri,
                        application.resource,
                        info={'input': source.path, 'template': applied.name},
                    )
                )


# -- endpoints (docs/20 § 3.3) ----------------------------------------------------


@dataclass(slots=True, eq=False)
class _Endpoint:
    key: Node
    full: str
    #: The endpoint's own properties, or `None` for a created endpoint without any.
    props: _Entry | None
    methods: dict[str, _Entry] = field(default_factory=dict)
    children: dict[str, _Endpoint] = field(default_factory=dict)


class _Endpoints:
    __slots__ = ('_errors', '_index', 'roots')

    def __init__(self, errors: Accumulator) -> None:
        self.roots: dict[str, _Endpoint] = {}
        self._index: dict[str, _Endpoint] = {}
        self._errors = errors

    def _place(self, parent: _Endpoint | None, key: Node, full: str, props: _Entry | None) -> _Endpoint:
        existing = self._index.get(full)
        if existing is None:
            endpoint = _Endpoint(key, full, props)
            self._index[full] = endpoint
            (parent.children if parent is not None else self.roots)[key.value] = endpoint
            return endpoint
        if props is None:
            return existing
        if existing.props is None:
            existing.props = props
            return existing
        difference = _difference(existing.props, props)
        if difference is not None:
            self._errors.add(_conflict('endpoint', full, existing.props, props, difference))
        return existing

    def created(
        self, source: _Input, chain: list[CreatedEndpoint], parameters: Mapping[str, tuple[Node, Node]]
    ) -> _Endpoint | None:
        """The created endpoints `source`'s resources go under; the last, or `None`."""
        parent: _Endpoint | None = None
        for step in chain:
            declared = [part for name in uri_variables(step.key) if name in parameters for part in parameters[name]]
            props = None
            if declared:
                # Reported, if it conflicts, at the first declaration that moved.
                key_node = declared[0]
                props = _Entry(source, source.uri, key_node, _mapping([_scalar('uriParameters'), _mapping(declared)]))
            parent = self._place(parent, _scalar(step.key), step.full, props)
        return parent

    def add(self, source: _Input, parent: _Endpoint | None, key: Node, resource: Node) -> None:
        """One authored resource, its operations and its nested resources, under `parent`."""
        full = (parent.full if parent is not None else '') + key.value
        own: list[Node] = []
        methods: list[tuple[Node, Node]] = []
        nested: list[tuple[Node, Node]] = []
        if resource.kind is NodeKind.MAPPING:
            for item_key, item in pairs(resource):
                if item_key.value in METHODS:
                    methods.append((item_key, item))
                elif item_key.value.startswith('/'):
                    nested.append((item_key, item))
                else:
                    own += [item_key, item]
        props = _Entry(source, source.uri, key, _mapping(own))
        endpoint = self._place(parent, key, full, props)
        for method_key, method in methods:
            entry = _Entry(source, source.uri, method_key, method)
            earlier = endpoint.methods.get(method_key.value)
            if earlier is None:
                endpoint.methods[method_key.value] = entry
                continue
            difference = _difference(earlier, entry)
            if difference is not None:
                name = f'{method_key.value.upper()} {full}'
                self._errors.add(_conflict('operation', name, earlier, entry, difference))
        for child_key, child in nested:
            self.add(source, endpoint, child_key, child)


# -- base URI (docs/20 § 6) -----------------------------------------------------------


@dataclass(slots=True, eq=False)
class _BaseUriPlan:
    base_uri: str | None = None
    #: The output's `baseUriParameters`, one entry per shared variable.
    parameters: list[_Entry] = field(default_factory=list)
    #: Per input: the created endpoints its resources go under.
    chains: list[list[CreatedEndpoint]] = field(default_factory=list)
    #: Per input: its declarations of the variables that moved.
    moved: list[dict[str, tuple[Node, Node]]] = field(default_factory=list)


def _declarations(source: _Input, parameters: Node | None) -> dict[str, tuple[Node, Node]]:
    if parameters is None:
        found = source.fields.get('baseUriParameters')
        if found is None:
            return {}
        parameters = found[1]
    if parameters.kind is not NodeKind.MAPPING:
        return {}
    return {key.value: (key, value) for key, value in pairs(parameters)}


def _effective_base_uris(
    inputs: list[_Input], options: JoinOptions
) -> tuple[list[str | None], list[dict[str, tuple[Node, Node]]]]:
    """Each input's base URI, override first, and its parameter declarations (docs/20 § 6.1)."""
    texts: list[str | None] = []
    declared: list[dict[str, tuple[Node, Node]]] = []
    for source in inputs:
        override = options.base_uris.get(source.uri)
        own = source.fields.get('baseUri')
        if override is not None:
            texts.append(override.uri)
            kept = _declarations(source, override.parameters)
            if override.parameters is None:
                names = set(uri_variables(override.uri))
                kept = {name: found for name, found in kept.items() if name in names}
            declared.append(kept)
        else:
            texts.append(None if own is None else own[1].value)
            declared.append(_declarations(source, None))
    return texts, declared


def _shared_parameters(
    base_uri: str, inputs: list[_Input], declared: list[dict[str, tuple[Node, Node]]], errors: Accumulator
) -> list[_Entry]:
    """The output's `baseUriParameters`, one entry per variable in the shared part.

    Each such variable must be declared identically, or left undeclared, in
    every input.
    """
    parameters: list[_Entry] = []
    for name in uri_variables(base_uri):
        if name == 'version':
            continue
        entries = [
            None if found is None else _Entry(source, source.uri, *found)
            for source, declarations in zip(inputs, declared, strict=True)
            for found in (declarations.get(name),)
        ]
        present = [entry for entry in entries if entry is not None]
        if not present:
            continue
        first = present[0]
        if len(present) != len(entries):
            # Undeclared is a required string; a declaration is something else.
            undeclared = next(source for source, entry in zip(inputs, entries, strict=True) if entry is None)
            errors.add(
                node_error(
                    'join conflict',
                    first.file,
                    first.key,
                    info={'kind': 'baseUriParameter', 'name': name, 'other': undeclared.uri, 'at': ''},
                )
            )
            continue
        for entry in present[1:]:
            difference = _difference(first, entry)
            if difference is not None:
                errors.add(_conflict('baseUriParameter', name, first, entry, difference))
                break
        parameters.append(first)
    return parameters


def _plan_base_uri(
    inputs: list[_Input], options: JoinOptions, version: str | None, errors: Accumulator
) -> _BaseUriPlan:
    plan = _BaseUriPlan(chains=[[] for _ in inputs], moved=[{} for _ in inputs])
    texts, declared = _effective_base_uris(inputs, options)
    if all(text is None for text in texts):
        return plan
    missing = [source.path for source, text in zip(inputs, texts, strict=True) if text is None]
    if missing:
        errors.add(
            RamlError.new('join missing base uri', inputs[0].uri, kind=ErrorKind.PARSING, info={'inputs': missing})
        )
        return plan
    written = [text for text in texts if text is not None]

    # `{version}` stays a variable only if it means the output's version for
    # every input that writes it (docs/20 § 6.1).
    versions = [_version_text(source) for source in inputs]
    keep = all(
        '{version}' not in text or input_version == version
        for text, input_version in zip(written, versions, strict=True)
    )
    if not keep:
        written = [
            text if input_version is None else text.replace('{version}', input_version)
            for text, input_version in zip(written, versions, strict=True)
        ]

    uris = [split_base_uri(text) for text in written]
    if any(uri.authority != uris[0].authority for uri in uris[1:]):
        errors.add(
            RamlError.new(
                'join base uri conflict',
                inputs[0].uri,
                kind=ErrorKind.PARSING,
                info={'values': [uri.authority for uri in uris]},
            )
        )
        return plan
    common = common_segments(uris)
    plan.base_uri = uris[0].authority + ''.join('/' + segment for segment in common)

    plan.parameters = _shared_parameters(plan.base_uri, inputs, declared, errors)
    # Under a created endpoint `{version}` would be an ordinary URI parameter,
    # so a remainder always carries the input's own version.
    remainders = [
        tuple(segment if input_version is None else segment.replace('{version}', input_version) for segment in rest)
        for rest, input_version in zip((uri.segments[len(common) :] for uri in uris), versions, strict=True)
    ]
    # An input with no resources needs no endpoint to hold them.
    plan.chains = plan_created(
        [
            rest if any(name.startswith('/') for name in source.fields) else ()
            for rest, source in zip(remainders, inputs, strict=True)
        ]
    )
    for index, (chain, declarations) in enumerate(zip(plan.chains, declared, strict=True)):
        names = {name for step in chain for name in uri_variables(step.key)}
        plan.moved[index] = {name: found for name, found in declarations.items() if name in names}
    return plan


def _version_text(source: _Input) -> str | None:
    found = source.fields.get('version')
    return None if found is None else found[1].value


# -- the output ------------------------------------------------------------------------


def _single_values(inputs: list[_Input], options: JoinOptions, errors: Accumulator) -> dict[str, Node]:
    """`title`, `description` and `version`: the option, else the shared value (docs/20 § 3.1)."""
    given = {'title': options.title, 'description': options.description, 'version': options.version}
    out: dict[str, Node] = {}
    for name in _SINGLE:
        option = given[name]
        if option is not None:
            if option:
                out[name] = _scalar(option)
            continue
        entries = [_Entry(source, source.uri, *source.fields[name]) for source in inputs if name in source.fields]
        if not entries:
            continue
        if len(entries) == len(inputs) and all(_same(entries[0], entry) for entry in entries[1:]):
            out[name] = entries[0].value
            continue
        errors.add(
            RamlError.new(
                'join root value differs',
                inputs[0].uri,
                kind=ErrorKind.PARSING,
                info={'property': name, 'inputs': [source.path for source in inputs]},
            )
        )
    return out


class _Writer:
    """Builds the output tree with paths relative to the output (docs/20 § 7.2)."""

    __slots__ = ('_errors', '_rebaser')

    def __init__(self, rebaser: Rebaser, errors: Accumulator) -> None:
        self._rebaser = rebaser
        self._errors = errors

    def value(self, node: Node, source: _Input) -> Node:
        try:
            return self._rebaser.rebase(node, source.uri, source.reader)
        except RamlError as err:
            self._errors.add(err)
            return node

    def library(self, target: str, entry: _Entry) -> Node:
        try:
            return with_value(entry.value, self._rebaser.relative(target, entry.value, entry.file))
        except RamlError as err:
            self._errors.add(err)
            return entry.value

    def endpoint(self, endpoint: _Endpoint) -> Node:
        content: list[Node] = []
        if endpoint.props is not None:
            content += [
                self.value(item, endpoint.props.input) if index % 2 else item
                for index, item in enumerate(endpoint.props.value.content)
            ]
        for entry in endpoint.methods.values():
            content += [entry.key, self.value(entry.value, entry.input)]
        for child in endpoint.children.values():
            content += [child.key, self.endpoint(child)]
        return _mapping(content)


@dataclass(slots=True, eq=False)
class _Combined:
    """Everything the output holds, before paths are made relative to it."""

    singles: dict[str, Node]
    defaults: dict[str, _Entry]
    plan: _BaseUriPlan
    libraries: dict[str, tuple[str, _Entry]] = field(default_factory=dict)
    maps: dict[str, _Names] = field(default_factory=dict)
    documentation: _Names | None = None
    annotations: _Names | None = None
    endpoints: _Endpoints | None = None


def _libraries(inputs: list[_Input], errors: Accumulator) -> dict[str, tuple[str, _Entry]]:
    """`uses` aliases, each with the library URI it names (docs/20 § 3.2)."""
    libraries: dict[str, tuple[str, _Entry]] = {}
    for source in inputs:
        for name, entry in _map_entries(source, 'uses'):
            target = resolve_ref_uri(source.raml, entry.value.value, entry.file, entry.value.position)
            earlier = libraries.get(name)
            if earlier is None:
                libraries[name] = (target, entry)
            elif earlier[0] != target:
                errors.add(
                    node_error(
                        'library namespace conflict',
                        entry.file,
                        entry.key,
                        info={'library': name, 'uri': target, 'other': earlier[0]},
                    )
                )
    return libraries


def _documentation(source: _Input, names: _Names) -> None:
    found = source.fields.get('documentation')
    if found is None:
        return
    if found[1].kind is not NodeKind.SEQUENCE:
        return
    for item in found[1].content:
        # An item may be a DocumentationItem fragment.
        expanded, item_file = _expand(source, item)
        title = next((value for key, value in pairs(expanded) if key.value == 'title'), None)
        if title is not None:
            names.add(title.value, _Entry(source, item_file or source.uri, title, expanded))


def _collect(inputs: list[_Input], options: JoinOptions, errors: Accumulator) -> _Combined:
    version = options.version if options.version is not None else _version_text(inputs[0])
    combined = _Combined(
        singles=_single_values(inputs, options, errors),
        defaults=_place_defaults(inputs),
        plan=_plan_base_uri(inputs, options, version, errors),
    )
    for source, chain in zip(inputs, combined.plan.chains, strict=True):
        if source.push:
            _check_pushed_templates(source, errors)
        if chain:
            _check_resource_path(source, errors)

    combined.libraries = _libraries(inputs, errors)
    combined.maps = {name: _Names(kind, errors) for name, kind in _NAME_MAPS}
    combined.documentation = documentation = _Names('documentation', errors)
    combined.annotations = annotations = _Names('annotation', errors)
    for source in inputs:
        for name, names in combined.maps.items():
            for declared_name, entry in _map_entries(source, name):
                names.add(declared_name, entry)
        _documentation(source, documentation)
        for name, (key, value) in source.fields.items():
            if name.startswith('('):
                annotations.add(name, _Entry(source, source.uri, key, value))

    combined.endpoints = endpoints = _Endpoints(errors)
    for source, chain, moved in zip(inputs, combined.plan.chains, combined.plan.moved, strict=True):
        parent = endpoints.created(source, chain, moved)
        pusher = _Pusher(source) if source.push else None
        for name, (key, value) in source.fields.items():
            if name.startswith('/'):
                resource = pusher.resource(value) if pusher is not None else value
                endpoints.add(source, parent, key, resource)
    return combined


def _entries_mapping(entries: Iterable[_Entry], writer: _Writer) -> Node:
    content: list[Node] = []
    for entry in entries:
        content += [entry.key, writer.value(entry.value, entry.input)]
    return _mapping(content)


def _root_values(combined: _Combined, writer: _Writer) -> list[Node]:
    """The single values, the base URI and the root defaults, in output order."""
    content: list[Node] = []
    for name in _SINGLE:
        if name in combined.singles:
            content += [_scalar(name), combined.singles[name]]
    plan = combined.plan
    if plan.base_uri is not None:
        content += [_scalar('baseUri'), _scalar(plan.base_uri)]
        if plan.parameters:
            content += [_scalar('baseUriParameters'), _entries_mapping(plan.parameters, writer)]
    for name in _DEFAULTS:
        kept = combined.defaults.get(name)
        if kept is not None:
            content += [_scalar(name), kept.value]
    return content


def _output(combined: _Combined, writer: _Writer) -> Node:
    """The output root, keys in the order of docs/20 § 7.1."""
    content = _root_values(combined, writer)
    if combined.documentation is not None and combined.documentation.entries:
        items = [writer.value(entry.value, entry.input) for entry in combined.documentation.entries.values()]
        content += [_scalar('documentation'), Node(NodeKind.SEQUENCE, TAG_SEQ, '', items)]
    if combined.libraries:
        uses: list[Node] = []
        for target, entry in combined.libraries.values():
            uses += [entry.key, writer.library(target, entry)]
        content += [_scalar('uses'), _mapping(uses)]
    for name, names in combined.maps.items():
        if names.entries:
            content += [_scalar(name), _entries_mapping(names.entries.values(), writer)]
    if combined.annotations is not None:
        for entry in combined.annotations.entries.values():
            content += [entry.key, writer.value(entry.value, entry.input)]
    if combined.endpoints is not None:
        for endpoint in combined.endpoints.roots.values():
            content += [endpoint.key, writer.endpoint(endpoint)]
    return _mapping(content)


def _combine(inputs: list[_Input], options: JoinOptions, output_uri: str) -> Node:
    errors = Accumulator()
    combined = _collect(inputs, options, errors)
    errors.raise_if_any()
    root = _output(combined, _Writer(Rebaser(output_uri), errors))
    errors.raise_if_any()
    return root


def _output_uri(output: str | os.PathLike[str] | None) -> str:
    path = Path(output) if output is not None else Path.cwd() / 'joined.raml'
    if not path.is_absolute():
        path = Path.cwd() / path
    return path_to_file_uri(path)


def _check_output(text: str, output_uri: str, inputs: list[_Input], options: ParseOptions) -> None:
    """Parse the result again; a diagnostic here is a defect in the join (docs/20 § 7.3)."""
    output_path = Path(file_uri_to_path(output_uri))
    roots = [str(output_path.parent)]
    roots += [
        file_uri_to_path(source.raml.workspace_root_uri)
        for source in inputs
        if is_file_uri(source.raml.workspace_root_uri)
    ]
    try:
        workspace = os.path.commonpath(roots)
    except ValueError:
        workspace = roots[0]
    check_options = replace(options, workspace_root=workspace, retain_source=False)
    try:
        parse_from_string(text, file_name=output_path.name, base_dir=output_path.parent, options=check_options)
    except RamlError as err:
        raise RamlError.wrap('join output', err, output_uri) from err


def join(paths: Sequence[str | os.PathLike[str]], options: JoinOptions | None = None) -> str:
    """The RAML text of the API documents at `paths`, joined (docs/20).

    The first path is the primary input. Raises `RamlError` carrying every
    problem found; nothing is returned unless the result parsed again cleanly.
    """
    options = options or JoinOptions()
    inputs = _read_inputs(paths, options.parse)
    known = {source.uri for source in inputs}
    # A mistyped path would otherwise be ignored and the join run without it.
    strays = [uri for uri in options.base_uris if uri not in known]
    if strays:
        raise RamlError.new('join override names no input', strays[0], kind=ErrorKind.PARSING, info={'inputs': strays})
    output_uri = _output_uri(options.output)
    text = write_raml(_combine(inputs, options, output_uri))
    _check_output(text, output_uri, inputs, options.parse)
    return text
