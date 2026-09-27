# fastraml-lsp for Claude Code

Connect Claude Code to `fastraml lsp` for RAML 1.0 diagnostics and code
navigation. The plugin is a client configuration; the language server is the
`fastraml[lsp]` extra of the root package (`docs/21-language-service.md` § 5).

## Use

Install the server so `fastraml` is on the `PATH` of the shell that starts
Claude Code:

```bash
uv tool install 'fastraml[lsp]'
```

From this repository's root, load the plugin for a session:

```bash
claude --plugin-dir ./contrib/fastraml-claude-code
```

To use it in another project, pass the absolute path to this plugin directory.
Claude Code starts `fastraml lsp` for `.raml` files in the workspace. The server
provides diagnostics, definitions, references, hover, document and workspace
symbols, links, folding, selection ranges, and type hierarchy. It discovers
API, Overlay, and Extension roots by their headers; no editor settings are
needed for the default behavior (`docs/21` § 2).

For development against this checkout, run `uv sync --extra lsp` at the
repository root and start Claude Code from a shell where the resulting
`.venv/bin` (Windows: `.venv/Scripts`) is on `PATH`.

## Check the plugin

```bash
claude plugin validate --strict ./contrib/fastraml-claude-code
```

The manifest is in `.claude-plugin/plugin.json`; Claude Code discovers the
server from `.lsp.json` at the plugin root. See the
[Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)
for this layout.
