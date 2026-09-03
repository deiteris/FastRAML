"""Name resolution: `Type`, `lib.Type`.

Two functions cover every lookup in the parser. Both are generic over what is
being looked up — a shape, a trait definition, a resource type, a security
scheme — because the lookup rule is the same for all four and the differences
live in the `pick` callback.

See docs/04-fragments-and-namespaces.md section 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pyraml.parser.fragments import Library, LibraryLink

__all__ = [
    'UnresolvedReferenceError',
    'cut_last',
    'resolve_library_reference',
    'resolve_reference',
]


class UnresolvedReferenceError(LookupError):
    """A reference did not resolve.

    Carries the parts rather than an assembled sentence, so the caller — which
    knows the location and the position — can build the diagnostic. `reason` is
    the message key; `name` is what was written.
    """

    __slots__ = ('name', 'reason')

    def __init__(self, reason: str, name: str) -> None:
        super().__init__(f'{reason}: {name}')
        self.reason = reason
        self.name = name


def cut_last(text: str, separator: str) -> tuple[str, str, bool]:
    """Split on the **last** occurrence: `('a.b', 'c', True)` for `a.b.c`.

    RAML type names may contain dots — the RDT `IDENTIFIER` production includes
    `.` — so `a.b.c` means type `c` of library `a.b`. Splitting on the first dot
    would name a library that was never imported.
    """
    before, found, after = text.rpartition(separator)
    return before, after, bool(found)


def resolve_reference[T](
    local: Mapping[str, T] | None,
    uses: Mapping[str, LibraryLink] | None,
    name: str,
    pick: Callable[[Library, str], T | None],
) -> T:
    """Resolve `name` against a fragment's own declarations, then its `uses:`.

    Namespace chaining is not permitted (spec section Applying Libraries:
    "processors MUST NOT allow any composition of namespaces using '.' across
    multiple libraries"). The last-dot split plus the single library hop below
    enforce that without a check of their own: in `files.file-type.File` the
    prefix `files.file-type` is simply not a key in `uses`.
    """
    prefix, suffix, dotted = cut_last(name, '.')

    if not dotted:
        found = local.get(name) if local is not None else None
        if found is None:
            raise UnresolvedReferenceError('reference not found', name)
        return found

    # A fragment may legitimately declare a key that contains a dot, so the
    # local table is consulted for the whole name first.
    if local is not None:
        found = local.get(name)
        if found is not None:
            return found

    return _pick_from_library(uses, prefix, suffix, pick)


def resolve_library_reference[T](
    uses: Mapping[str, LibraryLink] | None,
    name: str,
    pick: Callable[[Library, str], T | None],
) -> T:
    """`resolve_reference` without a local table.

    Used by the fragments that declare nothing of their own — DataType,
    NamedExample, DocumentationItem, Trait, ResourceType, SecurityScheme. An
    unqualified name there cannot resolve, which is what makes a typed fragment
    self-contained: it sees its own `uses:` and nothing of its includer's
    namespace (docs/04 section 4.3).
    """
    prefix, suffix, dotted = cut_last(name, '.')
    if not dotted:
        raise UnresolvedReferenceError('invalid reference', name)
    return _pick_from_library(uses, prefix, suffix, pick)


def _pick_from_library[T](
    uses: Mapping[str, LibraryLink] | None,
    prefix: str,
    suffix: str,
    pick: Callable[[Library, str], T | None],
) -> T:
    link = uses.get(prefix) if uses is not None else None
    if link is None:
        raise UnresolvedReferenceError('library not found', prefix)
    if link.link is None:
        raise UnresolvedReferenceError('library not resolved', prefix)
    found = pick(link.link, suffix)
    if found is None:
        raise UnresolvedReferenceError('reference not found', suffix)
    return found
