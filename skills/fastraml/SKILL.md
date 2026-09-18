---
name: fastraml
description: Work with a RAML 1.0 API definition using the fastraml CLI, and write RAML 1.0 itself. Use when a .raml file is involved and you need to check it for errors, list the types, endpoints, traits and security schemes it declares, read one type or endpoint with inheritance and traits already merged in, find every operation affected by changing a type, compare two versions for breaking changes, export the API as JSON, a graph, or OpenAPI 3.0.3 (fastraml openapi), lint it for style and security problems (fastraml lint), or author a document, type, endpoint, trait or security scheme. Triggers include "validate this RAML", "lint this API", "what endpoints does this API have", "what breaks if I change this type", "is this API change backwards compatible", "convert this RAML to OpenAPI", "write a RAML spec for this". Use fastraml instead of reading .raml files directly - a RAML file rarely holds the whole API, because types inherit, traits add parameters, and includes pull in other files.
compatibility: Requires the fastraml CLI on PATH (Python 3.12+). `fastraml query` also needs pyoxigraph. The `-r` flag also needs httpx or requests.
license: MIT
allowed-tools: Bash(fastraml:*) Read
---

# fastraml

`fastraml` answers questions about RAML 1.0 API definitions from the command line.

Read the answers from `fastraml`, not from the `.raml` file. A RAML file rarely
holds the whole API: types inherit from other types, traits add headers and
query parameters to operations, resource types add whole methods, and
`!include` pulls in other files and libraries. `fastraml` resolves all of that
first, so it tells you what the API *is* rather than what one file *says*.

The CLI also serves a guide to RAML 1.0 itself, so reach for it when you are
writing a document rather than asking about one.

## Start here

**This file is a discovery stub, not the usage guide.** Load a guide from the
CLI before you run any other `fastraml` command, or before you write any RAML:

```bash
fastraml skills get core          # The CLI: the workflow and every common task
fastraml skills get core --full   # Also print the complete flag reference
fastraml skills get raml          # The language: how to write a .raml file
```

The CLI serves the guide from inside the installed package, so the instructions
always match the version that answers them. This stub cannot change between
releases, which is why it only points at `skills get core`.

## Other guides

Load one of these when the task calls for it:

```bash
fastraml skills get lint      # Check style and security; configure or write rules
fastraml skills get backward  # Check backward compatibility; configure the gate
fastraml skills get sparql    # Write your own fastraml query
```

Run `fastraml skills list` to see everything the installed version ships.

## If `fastraml skills` is not recognised

The installed CLI predates the served guides. Upgrade it, or fall back to the
built-in help:

```bash
fastraml --help
fastraml <command> --help
```

## If `fastraml` is not found at all

fastRAML is not on PyPI yet. Install it from GitHub:

```bash
uv tool install git+https://github.com/deiteris/FastRAML
```

Inside a checkout of that repository you can instead prefix every command with
`uv run`, as in `uv run fastraml skills get core`.
