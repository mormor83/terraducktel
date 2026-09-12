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
export const Uri = { parse: (s: string) => ({ toString: () => s, scheme: s.split(":")[0], path: s.split(":").slice(1).join(":") }) };
export const window = { createOutputChannel: () => ({ appendLine() {}, append() {}, show() {}, clear() {}, dispose() {} }) };
export const commands = { executeCommand: async () => undefined };
export const env = { openExternal: async () => true, clipboard: { writeText: async () => undefined } };
