"""Join several RAML API documents into one (docs/20-join.md).

Runs on the inputs' source trees before anything is decoded, so it is neither a
parser pass nor a view. Nothing imports this package except `fastraml.cli`.
"""

from fastraml.join.combine import BaseUriOverride, JoinOptions, join

__all__ = ['BaseUriOverride', 'JoinOptions', 'join']
