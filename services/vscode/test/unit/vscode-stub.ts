// Minimal stand-in so pure modules that `import * as vscode` for types or
// ThemeColor/EventEmitter can load under vitest. Extend as tests need.
export class EventEmitter<T> {
  private listeners: Array<(e: T) => void> = [];
  event = (l: (e: T) => void) => { this.listeners.push(l); return { dispose: () => { this.listeners = this.listeners.filter((x) => x !== l); } }; };
  fire(e: T) { for (const l of [...this.listeners]) l(e); }
  dispose() { this.listeners = []; }
}
export class ThemeColor { constructor(public id: string) {} }
export class ThemeIcon {
  static readonly Folder = new ThemeIcon("folder");
  static readonly File = new ThemeIcon("file");
  constructor(public id: string, public color?: ThemeColor) {}
}
export enum TreeItemCollapsibleState { None = 0, Collapsed = 1, Expanded = 2 }
export class TreeItem {
  label?: string; description?: string; tooltip?: unknown; contextValue?: string; iconPath?: unknown; command?: unknown; id?: string;
  constructor(label: string, public collapsibleState: TreeItemCollapsibleState = TreeItemCollapsibleState.None) { this.label = label; }
}
export class MarkdownString { value = ""; constructor(v = "") { this.value = v; } appendMarkdown(s: string) { this.value += s; return this; } }
export const Uri = {
  parse: (s: string) => {
    const [head, ...rest] = s.split("?");
    return { toString: () => s, scheme: head.split(":")[0], path: head.split(":").slice(1).join(":"), query: rest.join("?") };
  },
};
export enum StatusBarAlignment { Left = 1, Right = 2 }

/** Every item `createStatusBarItem` has handed out, most recent last — lets a test reach the live
 *  item (text/tooltip/backgroundColor) without EditorStatus needing to expose its private field. */
export const statusBarItems: Array<{ id: string; text: string; tooltip: unknown; backgroundColor: unknown; command: unknown; name: unknown; visible: boolean; show(): void; hide(): void; dispose(): void }> = [];

export const window: Record<string, unknown> = {
  createOutputChannel: () => ({ appendLine() {}, append() {}, show() {}, clear() {}, dispose() {} }),
  createStatusBarItem: (id: string, ..._rest: unknown[]) => {
    const item = {
      id, alignment: StatusBarAlignment.Left, priority: 0,
      text: "", tooltip: undefined as unknown, backgroundColor: undefined as unknown, command: undefined as unknown, name: undefined as unknown,
      visible: false,
      show() { item.visible = true; }, hide() { item.visible = false; }, dispose() {},
    };
    statusBarItems.push(item);
    return item;
  },
  // Settable by tests: `(vscode.window as any).activeTextEditor = { document: ... }`.
  activeTextEditor: undefined as unknown,
  onDidChangeActiveTextEditor: new EventEmitter<unknown>().event,
  showQuickPick: async (..._a: unknown[]) => undefined,
  showInformationMessage: async (..._a: unknown[]) => undefined,
  showErrorMessage: async (..._a: unknown[]) => undefined,
  showWarningMessage: async (..._a: unknown[]) => undefined,
  showInputBox: async (..._a: unknown[]) => undefined,
};
export const languages = { setTextDocumentLanguage: async (d: unknown) => d };

/** Records every `setContext` call so tests can assert on it; other commands are no-ops unless a
 *  test replaces `commands.registerCommand`/`executeCommand` itself. */
export const setContextCalls: Array<{ key: string; value: unknown }> = [];
export const commands = {
  executeCommand: async (cmd: string, ...args: unknown[]) => {
    if (cmd === "setContext") setContextCalls.push({ key: args[0] as string, value: args[1] });
    return undefined;
  },
  registerCommand: (_id: string, _fn: (...a: unknown[]) => unknown) => ({ dispose() {} }),
};
export const env = { openExternal: async () => true, clipboard: { writeText: async () => undefined } };

/** Minimal `vscode.workspace` stand-in: a settable config store plus the event emitters
 *  `status.ts` subscribes to. Extend as tests need more of the surface. */
const configValues: Record<string, unknown> = {};
export const workspace = {
  onDidSaveTextDocument: new EventEmitter<unknown>().event,
  onDidChangeConfiguration: new EventEmitter<{ affectsConfiguration: (s: string) => boolean }>().event,
  getConfiguration: (_section?: string) => ({
    get: <T>(key: string, dflt?: T): T => (key in configValues ? (configValues[key] as T) : (dflt as T)),
  }),
};
