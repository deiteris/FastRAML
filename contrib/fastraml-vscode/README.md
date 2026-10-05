# fastraml-vscode

RAML 1.0 in VS Code through `fastraml lsp` (`docs/21-language-service.md` § 5):
diagnostics with lint quick fixes, definition, references, highlight, hover,
outline, workspace symbols, links, folding, selection and type hierarchy. It
also has a preview, the viewer (`viewer/`) showing the document's effective
model beside it.

The extension holds no RAML rule: it starts the server, passes it the
settings, and hosts the preview. The syntax grammar and language
configuration are those of go-raml's `external/raml-lsp-vscode`
(https://github.com/acronis/go-raml).

## Server

The server is the `lsp` extra of the root package, `fastraml[lsp]`:

```bash
uv sync --extra lsp      # in the repository root
```

| Setting | Default | |
|---|---|---|
| `fastraml.server.command` | `["fastraml"]` | the executable and any arguments before `lsp`, e.g. `["/path/to/pyRAML/.venv/bin/fastraml"]` or `["uv", "run", "--project", "/path/to/pyRAML", "fastraml"]` |
| `fastraml.config` | | `--config FILE`, relative to the first workspace folder |
| `fastraml.remote` | `false` | `--remote`: allow `http(s)` includes |
| `fastraml.roots` | `[]` | globs naming the root documents (`docs/21` § 2); empty finds them by header |
| `fastraml.trace.server` | `off` | the protocol trace, in the `fastRAML` output |

A change to any of them but tracing restarts the server. **fastRAML:
Restart Language Server** does so by hand.

## Preview

**fastRAML: Preview**, or the editor title button, opens the viewer beside
a RAML file. A root document shows itself; any other file shows the first
root that reads it. The page asks for the model once it has loaded, through the
server's `fastraml/tree` request, and again after every save. A parse that
stops before its types resolve has no effective model: the page keeps the
last one, under a message.

The preview renders in a shadow root, with `normalize.css` and the bundled
viewer stylesheet loaded inside it. VS Code's webview element styles cannot
override the viewer's code tags or other components. The preview follows VS
Code's live light, dark, and high-contrast themes; it does not show the
standalone viewer's theme switch or use its saved theme preference.
`webview/src/theme.css` maps VS Code's webview CSS variables (inherited into
the shadow root) onto the viewer tokens: editor colors for the page and text,
sidebar and input colors for surfaces, borders and contrast borders for
dividers, link and error colors for markers, and editor font settings for code.
Navigation hover and selection use VS Code's list colors; code blocks and inline
code use its text-code colors; the search opener uses its input colors; keyboard
focus uses `focusBorder`. Each role falls back to the viewer's base palette
when a theme does not supply the corresponding VS Code color.
Quiet labels use `descriptionForeground` rather than `disabledForeground`:
they are information to read, not disabled controls. Secondary text blends
that color with `editor.foreground` to keep it legible across themes.
The viewer keeps its own syntax-highlight palette: VS Code exposes workbench
color variables to webviews, but not the editor's TextMate token colors as CSS
variables.
See the [webview theming guide](https://code.visualstudio.com/api/extension-guides/webview#theming-webview-content)
and [theme color reference](https://code.visualstudio.com/api/references/theme-color).

## Build

Packaging needs Node.js 22 or later, which `@vscode/vsce` 4 requires.

```bash
npm install
npm install --prefix webview
npm run webview    # the preview page into media/
npm run compile    # src/ into dist/
npm run package    # fastraml-vscode-0.1.0.vsix, running both first
```

`webview/` is its own package: the preview page, a small host around the
viewer's `App`. It depends on the viewer as the package `fastraml-viewer`
(`file:../../../viewer`), installed as a copy rather than a link
(`install-links` in its `.npmrc`), so React and the viewer's other
dependencies are installed once, there. After a change to `viewer/`, run
`npm ci --prefix webview` to refresh the copied sources. Install the extension with
`code --install-extension fastraml-vscode-0.1.0.vsix`, or open this directory
in VS Code and run **Run extension** (F5).

`npm run check` type-checks `src/` and the webview, with the viewer's
sources it imports. There is no extension test suite: the server's behaviour is
`tests/unit/test_lsp.py`'s.
