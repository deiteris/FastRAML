"""Domain extensions — the model behind `(annotation)` keys.

The name comes from AMF by way of go-raml; "annotation" collides with Python's
own vocabulary.

Decoders build and register extensions; P8 (`resolve_domain_extensions`) binds
each to the annotation type it names, after P7. Every extension is appended to
the flat `Raml.domain_extensions` list, so P8 and P10 are single loops rather
than model traversals.

See docs/09-security-and-annotations.md § B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.parser.references import UnresolvedReferenceError
from fastraml.parser.substitutions import substituted_site
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import node_error, with_value

if TYPE_CHECKING:
    from fastraml.datanode import DataNode
    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.registry import ParseCtx, Raml
    from fastraml.yamlnode import Node

    BaseShape = Any

__all__ = [
    'AnnotationSites',
    'DomainExtension',
    'add_domain_extension',
    'annotation_declaration_key',
    'is_annotation_key',
    'resolve_domain_extensions',
    'retain_annotation_sites',
    'unmarshal_domain_extension',
]


@dataclass(slots=True, eq=False)
class DomainExtension:
    """One application of an annotation, e.g. `(oas-deprecated): true`."""

    id: int
    name: str
    value: DataNode
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: The scope the annotation *name* resolves in, captured at creation time.
    anchor: ReferenceResolver | None = None
    #: Where it was applied, for `allowedTargets` enforcement in P10. Captured
    #: at creation time for the same reason the anchor is: the decoder that
    #: finds the key is the only thing that knows.
    target: DomainLocation = DomainLocation.API
    #: The annotation type this application was bound to. Filled by P8.
    defined_by: BaseShape | None = None
    #: Where the name was written, when a template substituted it: the file
    #: and position of the caller's value (docs/08 § 5.1). P8 reports there.
    name_site: tuple[str, Position] | None = None

    def __repr__(self) -> str:
        return f'DomainExtension({self.name!r})'


def is_annotation_key(name: str) -> bool:
    """Whether a mapping key is an annotation application: `(name)`."""
    return len(name) > 1 and name[0] == '(' and name[-1] == ')'


@dataclass(slots=True, eq=False)
class _AnnotationSite:
    target: DomainLocation
    value: Node | None = None
    extension: DomainExtension | None = None
    applications: dict[Node, DomainExtension] | None = None


class AnnotationSites:
    """Retained IR keys keep their authored target and share unchanged applications.

    This is decoder state, created only when a template has root annotations and
    released after P4 materializes its source (docs/09 § B4).
    """

    __slots__ = ('_sites',)

    def __init__(self) -> None:
        self._sites: dict[Node, _AnnotationSite | DomainLocation] = {}

    def retain(self, key: Node, target: DomainLocation, *, declaration: bool) -> None:
        if key not in self._sites:
            self._sites[key] = _AnnotationSite(target) if declaration else target

    def has_key(self, key: Node) -> bool:
        return key in self._sites

    def decode(self, raml: Raml, location: str, key: Node, value: Node) -> DomainExtension:
        site = self._sites.get(key)
        if site is None:
            return _unmarshal_domain_extension(raml, location, key, value)
        if isinstance(site, DomainLocation):
            # A substituted key is unique to one compiled root. P4 materializes
            # that root once, so only its target needs retaining, not a cache.
            with raml.target_scope(site):
                return _unmarshal_domain_extension(raml, location, key, value)
        if site.extension is not None and site.value is value:
            return site.extension
        applications = site.applications
        extension = None if applications is None else applications.get(value)
        if extension is None:
            with raml.target_scope(site.target):
                extension = _unmarshal_domain_extension(raml, location, key, value)
            # Most keys have one application. Allocate a map only when the same
            # source key receives a second distinct value through substitution.
            if site.extension is None:
                site.value = value
                site.extension = extension
            else:
                if applications is None:
                    applications = site.applications = {}
                applications[value] = extension
        return extension


def annotation_declaration_key(raml: Raml, key: Node) -> Node:
    """A repeated literal include is a new declaration, not an application.

    Its root key must distinguish the includer's namespace before the
    template is materialized and starts sharing its applications.
    """
    sites = raml.annotation_sites
    if sites is None or not sites.has_key(key):
        return key
    retained = with_value(key, key.value)
    author = raml.document_anchor(key)
    if author is not None:
        raml.mark_authored(retained, author)
    return retained


def retain_annotation_sites(raml: Raml, node: Node, scope: ParseCtx, *, declaration: bool = False) -> bool:
    """Record root applications and report whether this template has any."""
    sites = raml.annotation_sites
    retained = False
    content = node.content
    for index in range(0, len(content), 2):
        key = content[index]
        if not is_annotation_key(key.value):
            continue
        if sites is None:
            sites = raml.annotation_sites = AnnotationSites()
        sites.retain(key, scope.target, declaration=declaration)
        retained = True
    return retained


def unmarshal_domain_extension(raml: Raml, location: str, key_node: Node, value_node: Node) -> DomainExtension:
    """Build one extension from a `(name): value` pair and register it.

    The caller attaches the returned object wherever the annotation was written;
    registration in `Raml.domain_extensions` has already happened.
    """
    sites = raml.annotation_sites
    if sites is None:
        return _unmarshal_domain_extension(raml, location, key_node, value_node)
    return sites.decode(raml, location, key_node, value_node)


def _unmarshal_domain_extension(raml: Raml, location: str, key_node: Node, value_node: Node) -> DomainExtension:
    name = key_node.value[1:-1]
    if not name:
        raise node_error('annotation name must not be empty', location, key_node)

    # An application an extension document wrote names its annotation type in
    # that document's namespace; the target is still the enclosing site's.
    location, ctx = raml.document_site(value_node, location, raml.current_ctx())
    name_scope = raml.reference_scope(key_node, ctx)
    extension = DomainExtension(
        id=raml.next_id(),
        name=name,
        value=make_data_node(raml, key_node, value_node, location),
        location=location,
        key_pos=key_node.position,
        value_pos=value_node.full_position,
        anchor=name_scope.anchor,
        target=ctx.target,
        name_site=substituted_site(raml.substitutions, key_node, 1, len(name) + 1),
    )
    raml.domain_extensions.append(extension)
    return extension


def add_domain_extension(
    raml: Raml, into: dict[str, DomainExtension], location: str, key_node: Node, value_node: Node
) -> None:
    """`unmarshal_domain_extension`, attached under its name to `into`."""
    extension = unmarshal_domain_extension(raml, location, key_node, value_node)
    into[extension.name] = extension


def resolve_domain_extensions(raml: Raml) -> None:
    """P8 — bind every application to the annotation type it names.

    One loop over the flat `Raml.domain_extensions` list (docs/09 § B3). Spec
    section Annotations: "All annotations used in an
    API specification MUST be declared in its annotationTypes node", so a name
    that resolves nowhere is an error rather than a shrug.

    Errors accumulate: a document with three undeclared annotations reports
    three. This pass never rewrites a value, only fills `defined_by`.
    """
    accumulator = Accumulator()
    for extension in raml.domain_extensions:
        # An extension built outside a fragment decode — programmatic
        # construction, or a test — has no anchor, so fall back to the index
        # the decoder fills, exactly as P7 does for a shape.
        anchor = extension.anchor or raml.resolver_at(extension.location)
        # One that names nothing stays in the model with `defined_by is None`,
        # marked (docs/13 § 1).
        try:
            with raml.marking(extension):
                if anchor is None:
                    raise _unresolved(extension, 'annotation type not found')
                try:
                    extension.defined_by = anchor.reference_annotation_type(extension.name)
                except UnresolvedReferenceError as err:
                    raise _unresolved(extension, err.reason) from err
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()


def _unresolved(extension: DomainExtension, reason: str) -> RamlError:
    location, at = extension.name_site or (extension.location, extension.key_pos)
    return RamlError.new(
        reason,
        location,
        at,
        kind=ErrorKind.RESOLVING,
        info={'annotation': extension.name},
    )
