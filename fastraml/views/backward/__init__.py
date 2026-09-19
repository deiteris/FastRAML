"""Operation-local backward compatibility over two effective RAML models.

Four modules, because four things happen and only the first two share anything:

    model       what a comparison can say — coordinates, results, grades
    compare     the walk that fills them in, from two parsed models
    markdown    the reading view of a finished comparison
    records     the JSON record, and the project policy that matches it

Nothing depends on `markdown` or `records` except through here, and `compare`
depends on neither: a rule that belongs to the comparison cannot be reached from
a renderer, which is the same argument `views/` makes against the passes one
layer down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastraml.views.backward.compare import backward
from fastraml.views.backward.markdown import render_markdown
from fastraml.views.backward.model import (
    IMPACTS,
    RULE_IDS,
    SUBJECTS,
    ApiChanged,
    ApiSchemaChanged,
    BackwardChange,
    Change,
    ChangeKind,
    Direction,
    Impact,
    ItemsSegment,
    LocatedChange,
    OperationAdded,
    OperationChanged,
    OperationContract,
    OperationId,
    OperationRemoved,
    ParameterLocation,
    PathSegment,
    PatternPropertySegment,
    PropertySegment,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SchemaChanged,
    SecurityLocation,
    Subject,
    TransportLocation,
    UnionMemberSegment,
    impact_of,
    side_of,
)
from fastraml.views.backward.records import configure, record

if TYPE_CHECKING:
    from fastraml.registry import Raml

__all__ = [
    'IMPACTS',
    'RULE_IDS',
    'SUBJECTS',
    'ApiChanged',
    'ApiSchemaChanged',
    'BackwardChange',
    'Change',
    'ChangeKind',
    'Direction',
    'Impact',
    'ItemsSegment',
    'LocatedChange',
    'OperationAdded',
    'OperationChanged',
    'OperationContract',
    'OperationId',
    'OperationRemoved',
    'ParameterLocation',
    'PathSegment',
    'PatternPropertySegment',
    'PropertySegment',
    'RequestBody',
    'ResponseBody',
    'ResponseStatus',
    'SchemaChanged',
    'SecurityLocation',
    'Subject',
    'TransportLocation',
    'UnionMemberSegment',
    'backward',
    'backward_markdown',
    'configure',
    'impact_of',
    'record',
    'render_markdown',
    'side_of',
]


def backward_markdown(old: Raml, new: Raml) -> str:
    """The whole thing, for a caller that wants the document and not the list."""
    return render_markdown(backward(old, new))
