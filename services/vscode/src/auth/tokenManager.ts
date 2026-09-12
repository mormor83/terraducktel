import { ApiError, type TdtClient, type TokenProvider } from "../api/client";
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
  /** In-flight `POST /auth/refresh`. Refresh tokens rotate, so two callers redeeming the same
   *  one in parallel would race: the loser's rotated token is already dead by the time it is
   *  persisted. The client coalesces the 401s it sees, but `getAccessToken()` also refreshes
   *  lazily (no access token in memory after a reload), and those callers never pass through
   *  the client's coalescing — so the single flight has to live here too. */
  private refreshing: Promise<string | undefined> | null = null;
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
  async signOut() { this.access = undefined; this.refreshing = null; await this.persist(undefined); }

  // ─── TokenProvider ───────────────────────────────────────────────────────
  async getAccessToken() {
    await this.ensureLoaded();
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    if (!this.access) return this.refreshAccessToken();
    return this.access;
  }
  /** Resolves a fresh access token, or `undefined` when the credential is definitively dead
   *  (the caller then signs out). Anything transient — the API unreachable, a timeout, a 5xx —
   *  is RETHROWN so the original request fails without destroying a credential that is very
   *  probably still valid. */
  async refreshAccessToken(): Promise<string | undefined> {
    await this.ensureLoaded();
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    const client = this.client, cred = this.cred;
    if (!client || !cred.refresh_token) return undefined;
    // `refreshing` is set synchronously after the await above, so a second caller resuming
    // later always observes the first caller's in-flight redemption.
    return (this.refreshing ??= this.redeem(client, cred).finally(() => { this.refreshing = null; }));
  }
  private async redeem(client: TdtClient, cred: StoredCredential): Promise<string | undefined> {
    try {
      const pair = await client.refresh(cred.refresh_token!);
      this.access = pair.access_token;
      await this.persist({ ...cred, refresh_token: pair.refresh_token });
      return this.access;
    } catch (e) {
      // 4xx = the server rejected this refresh token (expired / revoked / wrong): the credential
      // is dead, so resolve undefined and let the caller sign out. Everything else is transient.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) return undefined;
      throw e;
    }
  }
}
