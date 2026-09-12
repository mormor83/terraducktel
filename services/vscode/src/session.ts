import * as vscode from "vscode";
import { TdtClient } from "./api/client";
import type { AuthConfig, TokenPair } from "./api/types";
import { TokenManager } from "./auth/tokenManager";
import { pickActive, readProfiles, uiUrlFor, type Profile } from "./auth/profiles";
import { runLoopbackLogin } from "./auth/sso";
import type { SecretStore } from "./auth/secrets";
import { Store } from "./state/store";
import { CTX_CAN_WRITE, CTX_HAS_PROFILES, CTX_SIGNED_IN, GLOBALSTATE_ACTIVE_PROFILE, GLOBALSTATE_ACTIVE_PROFILE_MIGRATED } from "./ids";

/** vscode.SecretStorage returns Thenables, not Promises — adapt it to the testable SecretStore shape. */
function secretsAdapter(secrets: vscode.SecretStorage): SecretStore {
  return {
    get: (k) => Promise.resolve(secrets.get(k)),
    store: (k, v) => Promise.resolve(secrets.store(k, v)),
    delete: (k) => Promise.resolve(secrets.delete(k)),
  };
}

/** Everything that depends on "which deployment / which BU / who am I". Rebuilt on profile change. */
export class Session implements vscode.Disposable {
  profile: Profile | undefined;
  tokens: TokenManager | undefined;
  client: TdtClient | undefined;
  bu = "";
  /** Mirrors the `terraducktel.hasProfiles` context key; used by `viewsWelcome` to tell "no
   *  profiles yet" from "have profiles, just not signed in". */
  hasProfiles = false;
  readonly store: Store;
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changed.event;
  private disposables: vscode.Disposable[] = [];
  /** Disposed and rebuilt at the top of every `reload()` — listeners tied to the profile/client
   *  of the PREVIOUS cycle (sign-out handler, token-change handler) must not survive into the
   *  next one. */
  private cycle: vscode.Disposable[] = [];
  /** Bumped by every `reload()`. A cycle that finds it has been superseded mid-`await` bails out
   *  rather than publishing its (now wrong) profile/client/contexts over the newer one's. */
  private reloadGen = 0;
  /** Aborts the loopback listener of an SSO sign-in that is still waiting for the browser. */
  private cancelSso: (() => void) | undefined;
  readonly log: vscode.OutputChannel;

  constructor(private readonly ctx: vscode.ExtensionContext) {
    this.log = vscode.window.createOutputChannel("Terraducktel");
    // Signed out ⇒ no client ⇒ the store polls nothing. Without this the timer would keep
    // issuing credential-less requests at a signed-out user.
    this.store = new Store(() => (this.tokens?.isSignedIn() ? this.client : undefined), () => ({ runsLimit: this.cfg().get<number>("runsLimit", 200) }));
    this.disposables.push(this.log, this.store,
      // Only a profile/active-profile change invalidates the session. `refreshIntervalSeconds`
      // just re-arms the timer; `runsLimit` and `trace` are read live on every use.
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (["terraducktel.profiles", "terraducktel.uiUrls", "terraducktel.insecureTlsProfiles", "terraducktel.activeProfile"].some((k) => e.affectsConfiguration(k))) { void this.reload(); return; }
        if (e.affectsConfiguration("terraducktel.refreshIntervalSeconds") && this.profile) this.store.start(this.pollIntervalMs());
      }));
  }
  private cfg() { return vscode.workspace.getConfiguration("terraducktel"); }
  private pollIntervalMs() { return Math.max(5, this.cfg().get<number>("refreshIntervalSeconds", 30)) * 1000; }
  uiUrl() { return this.profile ? uiUrlFor(this.profile) : undefined; }
  canWrite(): boolean {
    if (!this.tokens?.isSignedIn()) return false;
    const c = this.tokens.claims();
    // No claims and not an API key = a JWT session whose access token has not been minted yet;
    // assume read-only until it is, rather than flashing write actions we may not be allowed.
    if (!c) return this.tokens.kind() === "api_key";   // API key: role unknown; the server enforces
    return c.is_superadmin === true || c.role === "operator" || c.role === "admin";
  }

  async reload(): Promise<void> {
    const gen = ++this.reloadGen;
    for (const d of this.cycle) d.dispose();
    this.cycle = [];
    this.store.stop();
    const profiles = readProfiles(this.cfg().get("profiles"), this.cfg().get("uiUrls"), this.cfg().get("insecureTlsProfiles"));
    this.hasProfiles = profiles.length > 0;
    const next = pickActive(profiles, await this.resolveActiveProfileName(profiles));
    this.profile = next;
    if (!next) { this.tokens = undefined; this.client = undefined; this.bu = ""; this.store.clear(); await this.publishContexts(); return; }
    // Per-folder choice (workspaceState) wins, then the cross-window choice `setBu()` also
    // records in globalState, then whatever the legacy per-profile `bu` setting carried (migrated
    // into globalState by `migrateLegacyBu()` the first time profile settings are rewritten —
    // this fallback stays for a profile that was never touched by either).
    this.bu = this.ctx.workspaceState.get<string>(`bu.${next.name}`) ?? this.ctx.globalState.get<string>(`bu.${next.name}`) ?? next.bu ?? "";
    this.tokens = new TokenManager(secretsAdapter(this.ctx.secrets), next.name);
    await this.tokens.restore();
    if (gen !== this.reloadGen) return;       // a newer reload() took over while we read secrets
    this.client = new TdtClient({ baseUrl: next.url, bu: this.bu, tokens: this.tokens, insecureTls: next.insecureTls, trace: (l) => { if (this.cfg().get<boolean>("trace")) this.log.appendLine(l); } });
    this.tokens.attach(this.client);
    const client = this.client, tokens = this.tokens; // captured so an event from a superseded cycle is ignored
    this.cycle.push(
      client.onSignedOut(() => {
        // `client` itself is expected to change under us — `setBu()` reassigns `this.client` to a
        // `withBu()` clone of the SAME auth session (same shared listeners) as a matter of course,
        // and that must keep firing this toast. What must NOT fire it is an event arriving late from
        // a cycle that a later `reload()` has since replaced wholesale (new profile/tokens/client) —
        // so guard on `tokens`, the identity that is stable across `setBu()` but not across `reload()`.
        if (this.tokens !== tokens) return;
        // Drop the cached snapshot: it belongs to a session that no longer exists, and leaving
        // it on screen makes a signed-out tree look live. The timer stays armed but idles —
        // the store's client getter returns undefined while signed out, so it does no I/O.
        this.store.clear();
        void vscode.window.showWarningMessage("Terraducktel: session expired — sign in again.", "Sign in").then((a) => a && vscode.commands.executeCommand("terraducktel.signIn"));
        void this.publishContexts();
      }),
      tokens.onDidChange(() => void this.publishContexts()),
    );
    await this.publishContexts();
    if (gen !== this.reloadGen) return;
    this.store.start(this.pollIntervalMs());
    void this.store.refresh();
  }
  private async publishContexts() {
    await vscode.commands.executeCommand("setContext", CTX_SIGNED_IN, !!this.tokens?.isSignedIn());
    await vscode.commands.executeCommand("setContext", CTX_CAN_WRITE, this.canWrite());
    await vscode.commands.executeCommand("setContext", CTX_HAS_PROFILES, this.hasProfiles);
    this.changed.fire();
  }

  /** Active profile resolution order: `context.globalState` (set by `setActiveProfile`, i.e. the
   *  "Switch profile" command) when it names an existing profile → else the deprecated
   *  `terraducktel.activeProfile` setting, consulted and migrated into globalState AT MOST ONCE
   *  ever (guarded by `GLOBALSTATE_ACTIVE_PROFILE_MIGRATED`, so a later reload — e.g. after the
   *  migrated profile is removed — falls straight through to "first by name" instead of
   *  re-reading a setting that may still hold the old value) → else the first profile by name. */
  private async resolveActiveProfileName(profiles: Profile[]): Promise<string | undefined> {
    const stored = this.ctx.globalState.get<string>(GLOBALSTATE_ACTIVE_PROFILE);
    if (stored && profiles.some((p) => p.name === stored)) return stored;
    if (this.ctx.globalState.get<boolean>(GLOBALSTATE_ACTIVE_PROFILE_MIGRATED)) return undefined;
    const legacy = this.cfg().get<string>("activeProfile");
    await this.ctx.globalState.update(GLOBALSTATE_ACTIVE_PROFILE_MIGRATED, true);
    if (legacy) { await this.ctx.globalState.update(GLOBALSTATE_ACTIVE_PROFILE, legacy); return legacy; }
    return undefined;
  }

  /** Writes the active profile to `context.globalState` (the "Switch profile" command / the
   *  profile status-bar item's click target) and reloads the session around it. */
  async setActiveProfile(name: string): Promise<void> {
    await this.ctx.globalState.update(GLOBALSTATE_ACTIVE_PROFILE, name);
    await this.reload();
  }

  async setBu(slug: string) {
    if (!this.profile || !this.client) return;
    this.bu = slug;
    // workspaceState is the per-folder choice; globalState mirrors it so the choice also survives
    // in a NEW window that has never opened this folder (or opened no folder at all).
    await this.ctx.workspaceState.update(`bu.${this.profile.name}`, slug);
    await this.ctx.globalState.update(`bu.${this.profile.name}`, slug);
    this.client = this.client.withBu(slug); this.tokens?.attach(this.client);
    this.changed.fire(); await this.store.refresh();
  }

  /** Throws a friendly error when there is no profile / no session. */
  requireClient(): TdtClient {
    if (!this.profile) throw new Error("No Terraducktel profile configured. Add one under Settings → Terraducktel → Profiles.");
    if (!this.client || !this.tokens?.isSignedIn()) throw new Error("Not signed in to Terraducktel. Run “Terraducktel: Sign in”.");
    return this.client;
  }

  async signIn(): Promise<void> {
    // A second sign-in supersedes the first: abort any loopback listener the previous attempt
    // left waiting, so it releases its port instead of lingering for the full SSO timeout.
    this.cancelSso?.();
    if (!this.profile || !this.client || !this.tokens) throw new Error("Add a profile under Settings → Terraducktel → Profiles first.");
    const cfg = await this.client.authConfig().catch((): AuthConfig => ({ mode: "local", oidc_enabled: false, oidc_issuer: undefined, cli_loopback: false }));
    const items: Array<vscode.QuickPickItem & { mode: "sso" | "password" | "api_key" }> = [];
    if (cfg.oidc_enabled && cfg.cli_loopback) items.push({ label: "$(globe) Sign in with SSO", description: cfg.oidc_issuer ?? "", mode: "sso" });
    if (cfg.mode !== "oidc") items.push({ label: "$(key) Email + password", mode: "password" });
    items.push({ label: "$(lock) API key (tdt_…)", description: "long-lived, for automation", mode: "api_key" });
    const pick = items.length === 1 ? items[0] : await vscode.window.showQuickPick(items, { placeHolder: `Sign in to ${this.profile.name} (${this.profile.url})` });
    if (!pick) return;
    if (pick.mode === "password") {
      const email = await vscode.window.showInputBox({ prompt: "Email", ignoreFocusOut: true }); if (!email) return;
      const pw = await vscode.window.showInputBox({ prompt: "Password", password: true, ignoreFocusOut: true }); if (pw === undefined) return;
      await this.tokens.signInWithPassword(email, pw);
    } else if (pick.mode === "api_key") {
      const key = await vscode.window.showInputBox({ prompt: "API key (tdt_…)", password: true, ignoreFocusOut: true });
      if (key === undefined) return;
      if (!key.trim()) throw new Error("API key is required");
      await this.tokens.signInWithApiKey(key);
    } else {
      if (vscode.env.remoteName) throw new Error("SSO sign-in needs a browser on this machine; in a remote session use an API key instead.");
      const client = this.client;
      let mine: (() => void) | undefined;
      let pair: TokenPair;
      try {
        pair = await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: "Terraducktel: complete sign-in in your browser…", cancellable: true },
          (_progress, token) => runLoopbackLogin({
            buildUrl: (port, nonce) => client.ssoLoginUrl(port, nonce),
            openUrl: (u) => vscode.env.openExternal(vscode.Uri.parse(u)) as Promise<boolean>,
            onCancel: (cancel) => { mine = cancel; this.cancelSso = cancel; token.onCancellationRequested(cancel); },
          }));
      } catch (e) {
        // Clear only our own handle: a sign-in that superseded this one already installed its.
        if (this.cancelSso === mine) this.cancelSso = undefined;
        // Cancelling is a choice, not a failure — whether the user hit the notification's cancel
        // button or started a second sign-in that superseded this one. Leave quietly; an error
        // toast here would be reporting the thing they just asked for.
        if (e instanceof Error && /cancelled/i.test(e.message)) return;
        throw e;
      }
      if (this.cancelSso === mine) this.cancelSso = undefined;
      await this.tokens.signInWithTokenPair(pair, "sso");
    }
    await this.publishContexts();
    await this.store.refresh();
    const who = this.tokens.claims()?.email ?? (this.tokens.kind() === "api_key" ? "API key" : "user");
    void vscode.window.showInformationMessage(`Terraducktel: signed in to ${this.profile.name} as ${who}.`);
  }
  async signOut() { await this.tokens?.signOut(); this.store.clear(); await this.publishContexts(); }
  dispose() { this.cancelSso?.(); for (const d of this.cycle) d.dispose(); for (const d of this.disposables) d.dispose(); this.changed.dispose(); }
}
