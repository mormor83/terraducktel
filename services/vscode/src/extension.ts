import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.signIn", () => {
      void vscode.window.showInformationMessage("Terraducktel: not wired yet");
    }),
  );
}

export function deactivate(): void {}
