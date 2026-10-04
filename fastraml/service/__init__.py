"""The language service: a workspace of buffers, snapshots of its parses, and
the queries an editor or an agent asks of them (docs/21).

A composition root, like `cli/`: it imports the model and the views, and
nothing but the protocol adapters imports it. It holds no RAML rule; every
answer is read from a parse and its views.
"""

__all__: list[str] = []
