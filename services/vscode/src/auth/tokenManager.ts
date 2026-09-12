import type { TdtClient, TokenProvider } from "../api/client";
import type { TokenPair } from "../api/types";
import { decodeJwtPayload, type AccessClaims } from "./jwt";
import type { SecretStore } from "./secrets";

export type CredentialKind = "password" | "api_key" | "sso";
interface StoredCredential { kind: CredentialKind; refresh_token?: string; api_key?: string }

/** One per profile. Persists ONLY the long-lived secret (refresh token or API key);
 *  the access token lives in memory and is re-minted on demand. */
export class TokenManager implements TokenProvider {
  private client: TdtClient | undefined;
  private access: string | undefined;
  private cred: StoredCredential | undefined;
  private changeListeners: Array<() => void> = [];
  /** In-flight (or completed) load of `cred` from the secret store. Cached as a promise, not a
   *  boolean, so concurrent first callers (e.g. two `getAccessToken()` calls racing at
   *  activation, before anyone has called `restore()`) await the SAME read instead of the
   *  second one observing "loaded" before `cred` is actually populated. An explicit `restore()`
   *  call and a lazy `ensureLoaded()` call share this same promise. */
  private loadPromise: Promise<void> | undefined;
  constructor(private readonly secrets: SecretStore, private readonly profileName: string) {}

  private get key() { return `terraducktel.cred.${this.profileName}`; }
  attach(client: TdtClient) { this.client = client; }
  onDidChange(l: () => void) { this.changeListeners.push(l); return { dispose: () => { this.changeListeners = this.changeListeners.filter((x) => x !== l); } }; }
  private fire() { for (const l of [...this.changeListeners]) l(); }

  private async load(): Promise<void> {
    const raw = await this.secrets.get(this.key);
    this.cred = raw ? (JSON.parse(raw) as StoredCredential) : undefined;
    this.access = this.cred?.kind === "api_key" ? this.cred.api_key : undefined;
  }
  /** Idempotent: concurrent callers (explicit `restore()` and lazy `ensureLoaded()` alike) await
   *  the same in-flight load; a completed load is not repeated. */
  restore(): Promise<void> { return (this.loadPromise ??= this.load()); }
  private ensureLoaded(): Promise<void> { return this.restore(); }
  private async persist(c: StoredCredential | undefined) {
    this.loadPromise = Promise.resolve();
    this.cred = c;
    if (c) await this.secrets.store(this.key, JSON.stringify(c)); else await this.secrets.delete(this.key);
    this.fire();
  }

  isSignedIn() { return !!this.cred; }
  kind(): CredentialKind | undefined { return this.cred?.kind; }
  /** Claims from the current access token (undefined for API keys or when signed out). */
  claims(): AccessClaims | undefined { return this.access && this.cred?.kind !== "api_key" ? decodeJwtPayload(this.access) : undefined; }

  async signInWithPassword(email: string, password: string) {
    if (!this.client) throw new Error("TokenManager not attached to a client");
    const pair = await this.client.login(email, password);
    await this.signInWithTokenPair(pair, "password");
  }
  async signInWithTokenPair(pair: TokenPair, kind: "password" | "sso" = "sso") {
    this.access = pair.access_token;
    await this.persist({ kind, refresh_token: pair.refresh_token });
  }
  async signInWithApiKey(key: string) {
    const k = key.trim();
    if (!k.startsWith("tdt_")) throw new Error("That doesn't look like a TDT API key (expected tdt_…)");
    this.access = k;
    await this.persist({ kind: "api_key", api_key: k });
  }
  async signOut() { this.access = undefined; await this.persist(undefined); }

  // ─── TokenProvider ───────────────────────────────────────────────────────
  async getAccessToken() {
    await this.ensureLoaded();
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    if (!this.access) return this.refreshAccessToken();
    return this.access;
  }
  async refreshAccessToken() {
    await this.ensureLoaded();
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    if (!this.client || !this.cred.refresh_token) return undefined;
    try {
      const pair = await this.client.refresh(this.cred.refresh_token);
      this.access = pair.access_token;
      await this.persist({ ...this.cred, refresh_token: pair.refresh_token });
      return this.access;
    } catch { return undefined; }
  }
}
