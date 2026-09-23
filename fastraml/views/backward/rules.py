"""Every named judgement this view can make, and what each does to a caller.

One table, because a grade is a policy and a policy written out at each use is a
policy that drifts. `compare` chooses a rule id from what moved; nothing in the
walk decides an impact.

`because` is not decoration: a report that says a change is breaking is read by
someone deciding whether to ship it, and the reason is what they are actually
after. It is one sentence per rule, in the caller's terms, and it belongs beside
the grade so the two cannot disagree.

Nothing here decides a RAML rule, and nothing here knows how the change it
grades was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from fastraml.views.backward.model import Impact

__all__ = ['RULES', 'Rule']


@dataclass(frozen=True, slots=True)
class Rule:
    """One named judgement. Named so it can be cited, and suppressed, by name."""

    name: str
    impact: Impact
    because: str


#: Every rule the comparison can return, so a consumer can enumerate them
#: without reading the walk (docs/16 § 5). `model.RULE_IDS` is its keys, which
#: a project override is validated against.
RULES: Final[dict[str, Rule]] = {
    rule.name: rule
    for rule in (
        Rule('entity-removed', 'breaking', 'a resource, method or status code callers may still be using'),
        Rule('entity-added', 'compatible', 'new surface; nothing that worked stops working'),
        Rule('request-property-required', 'breaking', 'a request that omitted it is now rejected'),
        Rule('request-property-optional', 'compatible', 'a request that supplied it still works'),
        Rule('request-property-added-required', 'breaking', 'a request that omits the new field is now rejected'),
        Rule('request-property-added', 'compatible', 'a new optional field; a request that omits it still works'),
        Rule('request-property-removed', 'review', 'the server stops reading a value callers still send'),
        Rule('request-constraint-tightened', 'breaking', 'a value that was accepted is now rejected'),
        Rule('request-constraint-loosened', 'compatible', 'strictly more input is accepted'),
        Rule('request-enum-value-removed', 'breaking', 'callers may still send it'),
        Rule('request-enum-value-added', 'compatible', 'the server accepts more than it did'),
        Rule('response-property-removed', 'breaking', 'callers read a field that has gone'),
        Rule('response-property-optional', 'breaking', 'callers relied on it always being present'),
        Rule(
            'response-property-required',
            'compatible',
            'a field callers already accept as absent is now guaranteed present',
        ),
        Rule('response-property-added', 'compatible', 'a caller that ignores unknown fields is unaffected'),
        Rule('response-constraint-loosened', 'breaking', 'a value arrives that callers cannot handle'),
        Rule('response-constraint-tightened', 'compatible', 'strictly less variety arrives'),
        Rule('response-enum-value-added', 'review', 'a caller that switches exhaustively has no branch for it'),
        Rule('response-enum-value-removed', 'compatible', 'one fewer case to handle'),
        Rule('security-added', 'breaking', 'an unauthenticated caller is now refused'),
        Rule('security-removed', 'compatible', 'a credential that was required is merely ignored'),
        Rule('security-alternative-added', 'compatible', 'existing authentication alternatives remain accepted'),
        Rule('security-alternative-removed', 'breaking', 'callers using that authentication alternative are refused'),
        Rule('base-uri-changed', 'breaking', 'callers send requests to the old API address'),
        Rule('protocol-removed', 'breaking', 'callers using that transport can no longer connect'),
        Rule('protocol-added', 'compatible', 'existing transports remain available'),
        Rule('type-changed', 'breaking', 'the wire format is not the one either side agreed'),
        Rule('format-changed', 'breaking', 'the value is now spelled in a representation callers do not parse'),
        Rule('documentation-changed', 'cosmetic', 'nothing on the wire changed'),
        Rule('reference-retargeted', 'review', 'it now names something else; the two may not agree'),
        Rule('other', 'review', 'not covered by a rule; read it yourself'),
    )
}
