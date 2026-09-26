import * as vscode from "vscode";
import type { Session } from "../session";
import { STATUS_BAR_PROFILE } from "../ids";

/** Compact status-bar item showing the active Terraducktel profile (and BU, once signed in).
 *  Click switches profile. Hidden entirely when no profile is configured. Sits left of the
 *  current-file item (priority 51 > 50 — VS Code places higher-priority items further left). */
export class ProfileStatus implements vscode.Disposable {
  private item: vscode.StatusBarItem;
  private subs: vscode.Disposable[] = [];

  constructor(private readonly s: Session) {
    this.item = vscode.window.createStatusBarItem(STATUS_BAR_PROFILE, vscode.StatusBarAlignment.Left, 51);
    this.item.name = "Terraducktel profile";
    this.item.command = "terraducktel.switchProfile";
    this.item.tooltip = "Terraducktel profile — click to switch";
    this.subs.push(this.item, s.onDidChange(() => this.refresh()));
    this.refresh();
  }

  refresh(): void {
    if (!this.s.hasProfiles) { this.item.hide(); return; }
    const name = this.s.profile?.name ?? "no profile";
    const bu = this.s.tokens?.isSignedIn() && this.s.bu ? ` · ${this.s.bu}` : "";
    this.item.text = `$(server) ${name}${bu}`;
    this.item.show();
  }

  dispose(): void { for (const d of this.subs) d.dispose(); }
}
