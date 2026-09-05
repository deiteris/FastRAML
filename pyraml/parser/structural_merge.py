"""The spec's merging algorithm, and the provenance overlay that survives it.

Spec section Algorithm of Merging Traits and Methods, restated in
docs/08-templates-and-endpoints.md section 4: applying a trait to a method means
putting the trait's *branch of the document* underneath the method's branch.
Properties only in one side survive; properties in both keep the target's scalar,
union its collections, and recurse into its objects.

The merge is defined on the YAML tree rather than on the model, which is why
stage 1 keeps one. Two rules make it sound rather than merely plausible:

* **neither input is mutated** (invariant I9) — new containers are allocated, but
  **child pointers are reused** (invariant I10), so a node the caller already
  recorded in the overlay keeps its identity and its mark stays reachable;
* **marks are set-if-absent** — a node that already carries one defines its own
  scope domain, and `mark_graft` neither overwrites it nor descends beneath it.
  That single rule is what resolves all three cases of docs/08 section 6.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pyraml.yamlnode import Node, NodeKind, pairs

if TYPE_CHECKING:
    from pyraml.registry import ParseCtx

__all__ = [
    'OPAQUE_DATA_FACETS',
    'ProvenanceOverlay',
    'copy_overlay',
    'mark_graft',
    'merge_structural',
    'node_value_equal',
]

#: Where a node was authored, for the nodes whose scope differs from the
#: enclosing unit's default. Sparse, and keyed by **object identity** — `Node`
#: defines no `__eq__`/`__hash__` for exactly this reason (docs/08 section 6.5).
type ProvenanceOverlay = dict[Node, ParseCtx]

#: Facets holding user data rather than RAML declaration structure. The spec's
#: "object -> recurse" rule does not apply to them: a method's own `example`
#: replaces a trait's wholesale. Without this, example data containing a key
#: literally named `example` is misread, and a partial merge produces a value
#: that validates against nothing. Spec section Merging Rules: "Examples are
#: always Simple Properties despite the capability to have complex YAML samples
#: as values."
OPAQUE_DATA_FACETS: Final = frozenset({'example', 'examples', 'default'})


def merge_structural(
    target: Node | None,
    source: Node | None,
    source_scope: ParseCtx | None = None,
    overlay: ProvenanceOverlay | None = None,
) -> Node | None:
    """Merge the lower-priority `source` branch underneath `target`.

    `target` is the explicit one — the method, the resource — and wins wherever
    the two disagree. With no `overlay` this is the pure structural merge.

    Recursion is bounded by `Raml.max_depth`, which the composer enforces
    before any of this runs.
    """
    if target is None:
        if source is not None:
            mark_graft(overlay, source, source_scope)
        return source
    if source is None:
        return target
    if target.kind is not source.kind:
        # Kind disagreement: the explicit node wins, unchanged.
        return target
    if target.kind is NodeKind.MAPPING:
        return _merge_mappings(target, source, source_scope, overlay)
    if target.kind is NodeKind.SEQUENCE:
        return _merge_sequences(target, source, source_scope, overlay)
    return target


def _container(model: Node, content: list[Node]) -> Node:
    """A fresh container with `model`'s tag and position, holding `content`."""
    return Node(
        model.kind,
        model.tag,
        model.value,
        content,
        model.line,
        model.column,
        model.end_line,
        model.end_column,
    )


def _merge_mappings(
    target: Node,
    source: Node,
    source_scope: ParseCtx | None,
    overlay: ProvenanceOverlay | None,
) -> Node:
    """Target's keys in target order, then source-only keys in source order.

    Deterministic and stable, which is what makes a golden projection of a
    merged tree worth reading.
    """
    source_values = {key.value: value for key, value in pairs(source)}

    merged: list[Node] = []
    for key, value in pairs(target):
        # Removing matches leaves the same dictionary as the source-only key
        # index, avoiding a second set for every mapping merge.
        other = source_values.pop(key.value, None)
        if other is None or key.value in OPAQUE_DATA_FACETS:
            merged.append(key)
            merged.append(value)
            continue
        recursed = merge_structural(value, other, source_scope, overlay)
        merged.append(key)
        merged.append(value if recursed is None else recursed)

    for key, value in pairs(source):
        if key.value not in source_values:
            continue
        mark_graft(overlay, value, source_scope)
        merged.append(key)
        merged.append(value)
    return _container(target, merged)


def _merge_sequences(
    target: Node,
    source: Node,
    source_scope: ParseCtx | None,
    overlay: ProvenanceOverlay | None,
) -> Node:
    """Union by value: every target item, then the source items not already there.

    This is the rule that produces the spec's `[mac, unix, win]`. It is also why
    `is: [{secured: {tokenName: token}}]` and the same trait with a different
    parameter both survive — they are not structurally equal. Traits are
    deduplicated by *name* in a separate step (docs/08 section 5.2).
    """
    merged = list(target.content)
    for item in source.content:
        if any(node_value_equal(existing, item) for existing in merged):
            continue
        mark_graft(overlay, item, source_scope)
        merged.append(item)
    return _container(target, merged)


def mark_graft(overlay: ProvenanceOverlay | None, node: Node | None, scope: ParseCtx | None) -> None:
    """Record a grafted subtree as belonging to `scope`, set-if-absent.

    The **whole** subtree is marked, not only its root: a stage-2 decoder may
    need the scope of a node several levels below a merge-synthesised container
    that carries no mark of its own.

    A node already in the overlay defines its own scope domain — a caller-supplied
    value spliced into a trait body, say — so the walk neither overwrites its mark
    nor descends beneath it, and that value keeps the caller's namespace even
    though it now sits inside a grafted subtree.
    """
    if overlay is None or node is None or scope is None:
        return
    stack = [node]
    while stack:
        current = stack.pop()
        if current in overlay:
            continue
        overlay[current] = scope
        stack += current.content


def copy_overlay(destination: ProvenanceOverlay, source: ProvenanceOverlay) -> None:
    """Merge `source`'s marks into `destination`, set-if-absent.

    Used when one IR unit absorbs another's body: the absorbed unit's marks are
    more specific than anything the absorbing merge is about to record.
    """
    for node, scope in source.items():
        if node not in destination:
            destination[node] = scope


def node_value_equal(left: Node, right: Node) -> bool:
    """Whether two nodes have the same structural value.

    Scalars compare by tag and value, composites element-wise and in order.
    Positions are ignored: two `enum` entries written in different files are the
    same member. Iterative, so a deep sequence item costs no stack.
    """
    if left is right:
        return True
    stack = [(left, right)]
    while stack:
        one, other = stack.pop()
        if one.kind is not other.kind:
            return False
        if one.kind is NodeKind.SCALAR:
            if one.tag != other.tag or one.value != other.value:
                return False
            continue
        if len(one.content) != len(other.content):
            return False
        stack += zip(one.content, other.content, strict=True)
    return True
