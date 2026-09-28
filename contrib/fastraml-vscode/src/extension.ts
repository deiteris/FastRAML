import * as crypto from 'crypto';
import * as path from 'path';
import {
    ExtensionContext,
    OutputChannel,
    TextEditor,
    Uri,
    ViewColumn,
    Webview,
    WebviewPanel,
    commands,
    window,
    workspace,
} from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';

let client: LanguageClient | undefined;
let output: OutputChannel;
/** One preview per document, by its URI. */
const previews = new Map<string, WebviewPanel>();

/** `fastraml lsp` with the settings' flags. */
function commandLine(): string[] {
    const settings = workspace.getConfiguration('fastraml');
    const [command, ...args] = settings.get<string[]>('server.command', ['fastraml']);
    args.push('lsp');
    const config = settings.get<string>('config', '');
    if (config) {
        args.push('--config', config);
    }
    if (settings.get<boolean>('remote', false)) {
        args.push('--remote');
    }
    return [command, ...args];
}

async function start(): Promise<void> {
    const line = commandLine();
    output.appendLine(`Starting ${line.join(' ')}`);
    const [command, ...args] = line;
    // In the first folder, so that a relative `--config` resolves.
    const cwd = workspace.workspaceFolders?.[0]?.uri.fsPath;
    client = new LanguageClient(
        'fastraml',
        'fastRAML',
        { command, args, options: { cwd } },
        {
            documentSelector: [{ scheme: 'file', language: 'raml' }],
            // The server registers its own watcher for `**/*` (docs/21 § 5).
            initializationOptions: { roots: workspace.getConfiguration('fastraml').get<string[]>('roots', []) },
            outputChannel: output,
        },
    );
    try {
        await client.start();
    } catch (error) {
        client = undefined;
        const choice = await window.showErrorMessage(
            `fastRAML language server did not start: ${error}. It needs \`fastraml[lsp]\`; set "fastraml.server.command" to run it.`,
            'Open Settings',
        );
        if (choice === 'Open Settings') {
            await commands.executeCommand('workbench.action.openSettings', 'fastraml.server');
        }
    }
}

async function restart(): Promise<void> {
    await client?.dispose();
    client = undefined;
    await start();
}

/**
 * The viewer beside the document (`webview/`). The page says when
 * it listens; it then gets the effective model, and again after every save,
 * of any file, since any file may be one the document reads.
 */
function preview(ctx: ExtensionContext, editor: TextEditor): void {
    const uri = editor.document.uri;
    const open = previews.get(uri.toString());
    if (open !== undefined) {
        open.reveal();
        return;
    }
    const media = Uri.joinPath(ctx.extensionUri, 'media');
    const panel = window.createWebviewPanel(
        'fastraml.preview',
        `Preview ${path.basename(uri.fsPath)}`,
        ViewColumn.Beside,
        { enableScripts: true, localResourceRoots: [media], retainContextWhenHidden: true },
    );
    previews.set(uri.toString(), panel);
    panel.onDidDispose(() => previews.delete(uri.toString()));
    panel.webview.onDidReceiveMessage(message => {
        if (message?.ready === true) {
            void send(panel, uri);
        }
    });
    panel.webview.html = page(panel.webview, media);
}

async function send(panel: WebviewPanel, uri: Uri): Promise<void> {
    if (client === undefined) {
        await panel.webview.postMessage({ error: 'The fastRAML language server is not running.' });
        return;
    }
    try {
        const textDocument = { uri: client.code2ProtocolConverter.asUri(uri) };
        const tree = await client.sendRequest<string | null>('fastraml/tree', { textDocument });
        await panel.webview.postMessage(
            tree === null ? { error: 'The document stops parsing before its types resolve: see Problems.' } : { tree },
        );
    } catch (error) {
        await panel.webview.postMessage({ error: `The preview failed: ${error}` });
    }
}

function page(webview: Webview, media: Uri): string {
    const nonce = crypto.randomBytes(16).toString('base64');
    const source = webview.cspSource;
    const script = webview.asWebviewUri(Uri.joinPath(media, 'viewer.js'));
    const style = webview.asWebviewUri(Uri.joinPath(media, 'viewer.css'));
    return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src ${source} 'unsafe-inline'; img-src ${source} https: data:; font-src ${source};">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
      body { margin: 0; padding: 0; }
      body.vscode-light, body.vscode-high-contrast-light { color-scheme: light; }
      body.vscode-dark, body.vscode-high-contrast { color-scheme: dark; }
    </style>
  </head>
  <body>
    <div id="root" data-style="${style}"></div>
    <script type="module" nonce="${nonce}" src="${script}"></script>
  </body>
</html>`;
}

export async function activate(ctx: ExtensionContext): Promise<void> {
    output = window.createOutputChannel('fastRAML');
    ctx.subscriptions.push(
        output,
        commands.registerCommand('fastraml.restart', restart),
        commands.registerTextEditorCommand('fastraml.preview', editor => preview(ctx, editor)),
        workspace.onDidSaveTextDocument(() => {
            for (const [uri, panel] of previews) {
                void send(panel, Uri.parse(uri));
            }
        }),
        // Every setting but tracing is read at start.
        workspace.onDidChangeConfiguration(event => {
            if (event.affectsConfiguration('fastraml') && !event.affectsConfiguration('fastraml.trace')) {
                void restart();
            }
        }),
    );
    await start();
}

export async function deactivate(): Promise<void> {
    await client?.dispose();
    client = undefined;
}
