# Type-system history

This archive is not normative. Current behavior is documented in
`docs/05-type-model.md`, `docs/06-type-expressions.md`,
`docs/07-resolution-and-inheritance.md`, and `docs/10-validation.md`.

## Durable rationale

`BaseShape` is separate from the replaceable kind object because a declaration
must have stable identity before P7 can resolve the kind named by its `type:`.
References therefore remain valid when `UnknownShape` becomes a concrete kind.

Aliases intentionally share the referent's mutable kind containers. An alias is
one type under another declaration identity, not an independently inherited
subtype. Inheritance merges, in contrast, must not share mutable parent
containers because a child narrowing would corrupt a parent or sibling subtype.

Union sibling facets are decoded only after P9 settles the member list. A union
has no independent scalar-facet vocabulary; member kinds determine whether a
key is built in, custom, or unknown. Distribution creates fresh subtypes rather
than mutating adopted parent members.

Custom-facet declarations apply to subtypes. The current first-parent-only walk
is a known implementation limitation, not a reinterpretation of that rule.
