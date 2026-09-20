"""Read the tree once, into everything the templates need.

A template asks questions; a plan answers them in advance. Keeping the two apart
means the reading of the tree can be tested without rendering anything, and that
no `{% if %}` has to decide what a union is.

Nothing here decides a RAML rule. It decides Python spellings, module layout,
and the two client conventions the README names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from ...naming import Names, class_name, field_name, from_address, module_name
from ...reader import is_recursion, is_ref, properties_of
from .annotate import Annotation, Annotator

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...reader import Declaration, Tree
    from ...targets import Settings
    from ...tree import EntryPoint, Operation, Parameter, SecurityScheme, Shape, ShapeNode

__all__ = ['Package', 'Scheme', 'plan']

_URI_TOKEN = re.compile(r'\{([^}]+)\}')

#: What counts as an answer rather than a refusal.
_SUCCESSFUL = range(200, 300)

#: The facets worth repeating in a docstring. Each constrains a value rather
#: than typing it, so it reaches the reader and not the annotation.
_CONSTRAINTS = (
    ('pattern', 'pattern'),
    ('min_length', 'minLength'),
    ('max_length', 'maxLength'),
    ('minimum', 'minimum'),
    ('maximum', 'maximum'),
    ('multiple_of', 'multipleOf'),
    ('min_items', 'minItems'),
    ('max_items', 'maxItems'),
    ('unique_items', 'uniqueItems'),
)


@dataclass(frozen=True, slots=True)
class Field:
    """One attribute of a generated dataclass."""

    name: str
    wire: str
    annotation: Annotation
    required: bool
    docs: str


@dataclass(frozen=True, slots=True)
class Model:
    """One generated module under `models/`.

    Either a dataclass (`fields`) or a type alias (`alias`). A RAML declaration
    that is not an object with properties — `Isbn: string`, `Prices: Money[]`,
    `Payload: Book[] | Review` — has no attributes to carry, and an empty class
    would hide what the author declared.
    """

    name: str
    module: str
    description: str
    fields: tuple[Field, ...] = ()
    alias: Annotation | None = None
    #: The property a discriminated hierarchy switches on, and this type's value
    #: for it. Both or neither, and only where the document states the value.
    discriminator: tuple[str, object] | None = None

    @property
    def is_alias(self) -> bool:
        return self.alias is not None

    @property
    def annotations(self) -> tuple[Annotation, ...]:
        found = tuple(one.annotation for one in self.fields)
        return (*found, self.alias) if self.alias is not None else found


@dataclass(frozen=True, slots=True)
class Argument:
    """One parameter of a generated function."""

    name: str
    wire: str
    annotation: Annotation
    required: bool
    docs: str


@dataclass(frozen=True, slots=True)
class Body:
    media_type: str
    annotation: Annotation


@dataclass(frozen=True, slots=True)
class Case:
    """One documented response."""

    status: str
    annotation: Annotation | None
    description: str


@dataclass(frozen=True, slots=True)
class Scheme:
    """One `securitySchemes:` entry, as the mechanics of sending a credential.

    The document says where the credential goes. A `Pass Through` or `x-`
    scheme names its own header through `describedBy:`, so a client that always
    sent `Authorization: Bearer` would send it where the API is not reading.
    """

    name: str
    constant: str
    type: str
    header_name: str
    prefix: str
    scopes: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One generated module under `api/`."""

    group: str
    module: str
    method: str
    path: str
    summary: str
    description: str
    uri_arguments: tuple[Argument, ...]
    query_arguments: tuple[Argument, ...]
    header_arguments: tuple[Argument, ...]
    body: Body | None
    cases: tuple[Case, ...]
    #: The lowest documented 2xx. A *client convention*, not a RAML rule: RAML
    #: orders responses and says nothing about which one is the answer.
    success: Case | None
    #: True when every way of calling this needs credentials.
    requires_auth: bool
    #: True when at least one scheme is `null` -- the API says the call may be
    #: made without credentials as well.
    optional_auth: bool
    #: The named schemes this operation accepts, in declaration order.
    scheme_names: tuple[str, ...]

    @property
    def arguments(self) -> tuple[Argument, ...]:
        return (*self.uri_arguments, *self.query_arguments, *self.header_arguments)

    @property
    def annotations(self) -> tuple[Annotation, ...]:
        found = [argument.annotation for argument in self.arguments]
        if self.body is not None:
            found.append(self.body.annotation)
        found.extend(case.annotation for case in self.cases if case.annotation is not None)
        return tuple(found)


@dataclass(frozen=True, slots=True)
class Package:
    """Everything one generated distribution holds."""

    distribution: str
    module: str
    title: str
    version: str
    description: str
    base_uri: str
    models: tuple[Model, ...]
    endpoints: tuple[Endpoint, ...]
    schemes: tuple[Scheme, ...]

    @property
    def requires_auth(self) -> bool:
        return any(endpoint.requires_auth or endpoint.optional_auth for endpoint in self.endpoints)

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(endpoint.group for endpoint in self.endpoints))

    def in_group(self, group: str) -> tuple[Endpoint, ...]:
        return tuple(endpoint for endpoint in self.endpoints if endpoint.group == group)


# -- building ------------------------------------------------------------------


def plan(tree: Tree, settings: Settings) -> Package:
    """Read the whole tree into one plan."""
    entry: EntryPoint = tree.document['entry_point'] or cast('EntryPoint', {})
    title = entry.get('title') or 'API'
    distribution = settings.package or module_name(title).replace('_', '-')
    builder = _Builder(tree)
    # Endpoints first: annotating a response body reaches models that nothing in
    # `types:` declares, and draining the model queue before that happened left
    # them named in a signature and generated nowhere.
    endpoints = builder.endpoints()
    models = builder.models()
    return Package(
        distribution=distribution,
        module=module_name(distribution),
        title=title,
        version=entry.get('version') or '',
        description=entry.get('description') or '',
        base_uri=entry.get('base_uri') or '',
        models=models,
        endpoints=endpoints,
        schemes=builder.schemes(),
    )


@dataclass(slots=True)
class _Builder:
    tree: Tree
    classes: Names = field(default_factory=Names)
    modules: Names = field(default_factory=lambda: Names(frozenset({'types', 'client', 'errors'})))
    annotator: Annotator = field(init=False)
    _declared: dict[str, Declaration] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.annotator = Annotator(tree=self.tree, names=self.classes)
        # Claimed before anything is annotated, and in declaration order, so a
        # generated name is a function of the document rather than of the order
        # a walk happened to reach things.
        for declaration in self.tree.types():
            self._declared[declaration.address] = declaration
            self.classes.claim(
                declaration.address,
                class_name(declaration.name),
                # Two files may declare `Page`, and the collision is silent
                # (docs/16 § 3.1). The file is the disambiguation a reader can
                # check; `Page2` is not.
                class_name(f'{_stem(declaration.file)}-{declaration.name}'),
            )

    # -- models ----------------------------------------------------------------

    def models(self) -> tuple[Model, ...]:
        aliases: list[Model] = []
        for declaration in self._declared.values():
            annotation = self.annotator.of(declaration.shape)
            name = self.classes.claim(declaration.address, class_name(declaration.name))
            if annotation.spelling != name:
                # Not an object with properties: `Isbn: string` is `str`, and a
                # class with no attributes would say less than the alias does.
                aliases.append(
                    Model(
                        name=name,
                        module=self.modules.claim(declaration.address, module_name(name)),
                        description=_description(declaration.shape),
                        alias=annotation,
                    )
                )

        # A loop rather than one pass: building a model annotates its
        # properties, and that is the only way an anonymous nested object is
        # ever reached. Each round builds what the last one found.
        built: dict[str, Model] = {}
        while True:
            outstanding = [(address, shape) for address, shape in self.annotator.pending() if address not in built]
            if not outstanding:
                break
            for address, shape in outstanding:
                built[address] = self._dataclass(address, shape)

        ordered = [built[address] for address in self.annotator.wanted if address in built]
        return (*_by_declaration_order(ordered, self._declared, self.classes), *aliases)

    def _dataclass(self, address: str, shape: Shape) -> Model:
        content = self.tree.content_of(shape)
        declared = self._declared.get(address)
        preferred = class_name(declared.name) if declared else _preferred(content, address)
        name = self.classes.claim(address, preferred)
        required: list[Field] = []
        optional: list[Field] = []
        for wire, prop in properties_of(content).items():
            one = Field(
                name=field_name(wire),
                wire=wire,
                annotation=self.annotator.of(prop['type']),
                required=prop['required'],
                docs=_docs(self.tree, prop['type']),
            )
            (required if one.required else optional).append(one)
        return Model(
            name=name,
            module=self.modules.claim(address, module_name(name)),
            description=_description(content),
            fields=(*required, *optional),
            discriminator=_discriminator(content),
        )

    # -- security ---------------------------------------------------------------

    def schemes(self) -> tuple[Scheme, ...]:
        constants = Names()
        return tuple(
            _scheme(name, definition, constants) for name, definition in self.tree.security_schemes() if name != 'null'
        )

    # -- endpoints --------------------------------------------------------------

    def endpoints(self) -> tuple[Endpoint, ...]:
        out: list[Endpoint] = []
        for path, endpoint in self.tree.endpoints():
            uri = self._arguments(endpoint.get('uri_parameters', {}))
            for method, operation in endpoint['operations'].items():
                out.append(self._endpoint(path, method, operation, uri))
        return tuple(out)

    def _endpoint(
        self,
        path: str,
        method: str,
        operation: Operation,
        inherited_uri: tuple[Argument, ...],
    ) -> Endpoint:
        cases = self._cases(operation, path, method)
        secured = operation.get('secured_by', [])
        return Endpoint(
            group=_group(path),
            module=self.modules.claim(f'api:{method}:{path}', module_name(f'{method}-{_slug(path)}')),
            method=method,
            path=path,
            summary=operation.get('display_name', '') or f'{method.upper()} {path}',
            description=operation.get('description', ''),
            uri_arguments=_ordered_for(path, inherited_uri),
            query_arguments=self._query(operation),
            header_arguments=self._arguments(operation.get('headers', {})),
            body=self._body(operation, path, method),
            cases=cases,
            success=_success(cases),
            requires_auth=bool(secured) and not any(one['is_null'] for one in secured),
            optional_auth=any(one['is_null'] for one in secured) and len(secured) > 1,
            scheme_names=tuple(one['name'] for one in secured if not one['is_null']),
        )

    def _query(self, operation: Operation) -> tuple[Argument, ...]:
        """Take the query from `queryParameters:` and `queryString:` alike.

        RAML says the two are alternatives, not that one is the other. Where a
        `queryString:` is an object, its properties are the query, and a caller
        can use them only if they appear as parameters.
        """
        named = self._arguments(operation.get('query_parameters', {}))
        query_string = self.tree.resolve(operation.get('query_string'))
        if query_string is None or is_recursion(query_string):
            return named
        # `is_recursion` narrows only where it is true.
        content = self.tree.content_of(cast('Shape', query_string))
        from_string = tuple(
            Argument(
                name=field_name(wire),
                wire=wire,
                annotation=self.annotator.of(prop['type']),
                required=prop['required'],
                docs=_docs(self.tree, prop['type']),
            )
            for wire, prop in properties_of(content).items()
        )
        return (*named, *from_string)

    def _arguments(self, parameters: dict[str, Parameter]) -> tuple[Argument, ...]:
        return tuple(
            Argument(
                name=field_name(wire),
                wire=wire,
                annotation=self.annotator.of(parameter['type']),
                required=parameter['required'],
                docs=_docs(self.tree, parameter['type']),
            )
            for wire, parameter in parameters.items()
        )

    def _body(self, operation: Operation, path: str, method: str) -> Body | None:
        for media_type, node in operation.get('bodies', {}).items():
            # The first declared media type. RAML orders them and says nothing
            # about preference, so this is a client convention (README).
            return Body(
                media_type=media_type,
                annotation=self.annotator.of(node, f'{method}-{_slug(path)}-body'),
            )
        return None

    def _cases(self, operation: Operation, path: str, method: str) -> tuple[Case, ...]:
        out = []
        for status, response in operation['responses'].items():
            annotation = None
            for node in response.get('bodies', {}).values():
                annotation = self.annotator.of(node, f'{method}-{_slug(path)}-{status}-response')
                break
            out.append(Case(status=status, annotation=annotation, description=response.get('description', '')))
        return tuple(out)


# -- small readings ------------------------------------------------------------


def _by_declaration_order(
    models: Iterable[Model],
    declared: dict[str, Declaration],
    classes: Names,
) -> tuple[Model, ...]:
    """Order declared types by declaration, then whatever they reached."""
    declaration_names = [classes.get(address) for address in declared]
    order = {name: index for index, name in enumerate(declaration_names) if name is not None}
    listed = list(models)
    return tuple(sorted(listed, key=lambda model: order.get(model.name, len(order))))


def _preferred(shape: Shape, address: str) -> str:
    declared = shape.get('name')
    return class_name(declared) if declared else from_address(address)


def _discriminator(shape: Shape) -> tuple[str, object] | None:
    """Return what `kind` holds for this type, where the document says.

    Generated models are flat, because RAML inheritance has no Python subclass
    form (README). This is what survives of a discriminated hierarchy, and it is
    the part a caller needs: the property to switch on, and the value that means
    this type.

    Only where the tree states the value. RAML defaults an unstated one to the
    type name, and applying that default here would put a rule of the language
    inside this package (docs/17 § 2).
    """
    if shape['type'] != 'object':
        return None
    marker = shape.get('discriminator')
    value = shape.get('discriminator_value')
    return (marker, value) if marker and value is not None else None


def _description(shape: Shape) -> str:
    return shape.get('description', '') or shape.get('display_name', '') or ''


def _docs(tree: Tree, node: ShapeNode | None) -> str:
    """Describe a type in one line: its description, then its constraints.

    Constraints are documented and not enforced. A client that validated would
    state the language's rules a second time, and it still could not answer the
    question that matters: whether the server agrees with them.
    """
    if node is None or is_recursion(node):
        return ''
    shape = tree.at(node['$ref']) if is_ref(node) else cast('Shape', node)
    if shape is None:
        return ''
    content = tree.content_of(shape)
    parts = []
    described = content.get('description', '')
    if described:
        parts.append(' '.join(described.split()))
    limits = [f'{label}: {content[key]}' for key, label in _CONSTRAINTS if content.get(key) is not None]  # type: ignore[literal-required]
    if limits:
        parts.append(f'({", ".join(limits)})')
    default = content.get('default')
    if default is not None:
        parts.append(f'Default: {default!r}.')
    return ' '.join(parts)


#: What precedes the credential for each scheme type, where `describedBy:` does
#: not say. Only OAuth 2.0 fixes a spelling; the rest are conventions, and a
#: caller can override any of them on the client.
_PREFIX_OF = {
    'Basic Authentication': 'Basic',
    'Digest Authentication': 'Digest',
    'OAuth 1.0': 'OAuth',
    'OAuth 2.0': 'Bearer',
}


def _scheme(name: str, definition: SecurityScheme, constants: Names) -> Scheme:
    described = definition.get('described_by') or {}
    headers = list(described.get('headers', {}))
    # The header the scheme itself describes wins. `Authorization` is only the
    # default because it is what the four named types use.
    header = headers[0] if headers else 'Authorization'
    kind = definition['type']
    settings = definition.get('settings') or {}
    scopes = settings.get('scopes', ())
    return Scheme(
        name=name,
        constant=constants.claim(name, module_name(name).upper()),
        type=kind,
        header_name=header,
        prefix=_prefix_for(kind, header),
        scopes=tuple(scopes) if isinstance(scopes, list) else (),
        description=_one_sentence(definition.get('description', '')),
    )


def _prefix_for(kind: str, header: str) -> str:
    """What goes before the credential in its header.

    A scheme that names a header of its own carries the credential bare, unless
    its type is one of the four the spec spells a prefix for.
    """
    if header != 'Authorization':
        return _PREFIX_OF.get(kind, '')
    return _PREFIX_OF.get(kind, 'Bearer')


def _one_sentence(text: str) -> str:
    return ' '.join(text.split())


def _group(path: str) -> str:
    """Group by the first literal segment, so `/books/{isbn}` joins `/books`."""
    for segment in path.strip('/').split('/'):
        if segment and not segment.startswith('{'):
            return module_name(segment)
    return 'root'


def _slug(path: str) -> str:
    return '-'.join(part.strip('{}') for part in path.strip('/').split('/') if part) or 'root'


def _ordered_for(path: str, arguments: tuple[Argument, ...]) -> tuple[Argument, ...]:
    """Order URI parameters the way the path uses them, and drop the rest.

    The tree carries every parameter an endpoint inherited, including ones its
    own path never mentions. An argument that goes nowhere is worse than a
    missing one.
    """
    used = _URI_TOKEN.findall(path)
    by_wire = {argument.wire: argument for argument in arguments}
    return tuple(by_wire[token] for token in used if token in by_wire)


def _success(cases: tuple[Case, ...]) -> Case | None:
    """Pick the lowest documented 2xx. A client convention; see the README."""
    successes = sorted(
        (case for case in cases if case.status.isdigit() and int(case.status) in _SUCCESSFUL),
        key=lambda case: int(case.status),
    )
    return successes[0] if successes else None


def _stem(file: str) -> str:
    return file.rsplit('/', 1)[-1].rsplit('.', 1)[0]
