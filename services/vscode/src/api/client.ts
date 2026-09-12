import { request } from "../http";
import type * as T from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); this.name = "ApiError"; }
}

/** Supplied by the token manager (Task 4). The client never stores tokens itself. */
export interface TokenProvider {
  getAccessToken(): Promise<string | undefined>;
  /** Obtain a fresh access token (refresh flow, or re-read an API key). Returns undefined when impossible. */
  refreshAccessToken(): Promise<string | undefined>;
  signOut(): Promise<void>;
}

export interface ClientOptions {
  baseUrl: string; bu: string; tokens: TokenProvider; insecureTls?: boolean;
  trace?: (line: string) => void;
}

function detailToMessage(status: number, text: string): { message: string; detail: unknown } {
  try {
    const j = JSON.parse(text);
    const d = j?.detail;
    if (typeof d === "string") return { message: d, detail: d };
    if (Array.isArray(d)) {
      const msg = d.map((e) => `${(e.loc ?? []).filter((x: unknown) => x !== "body").join(".")}: ${e.msg}`).join("; ");
      return { message: msg || `HTTP ${status}`, detail: d };
    }
    return { message: `HTTP ${status}`, detail: j };
  } catch { return { message: text?.trim() || `HTTP ${status}`, detail: text }; }
}

/** Sign-out coalescing state, shared by a client and every clone withBu() makes of it — a
 *  single auth session (and its listeners) spans all of them, so a 401 seen through any clone
 *  signs the whole session out exactly once. */
interface AuthState { epoch: number; signingOut: Promise<void> | null; listeners: Array<() => void> }

export class TdtClient {
  private refreshing: Promise<string | undefined> | null = null;
  private readonly auth: AuthState;
  constructor(private readonly o: ClientOptions, auth?: AuthState) {
    this.auth = auth ?? { epoch: 0, signingOut: null, listeners: [] };
  }

  get baseUrl() { return this.o.baseUrl; }
  get bu() { return this.o.bu; }
  withBu(bu: string) { return new TdtClient({ ...this.o, bu }, this.auth); }
  onSignedOut(l: () => void) {
    this.auth.listeners.push(l);
    return { dispose: () => { this.auth.listeners = this.auth.listeners.filter((x) => x !== l); } };
  }

  // ─── core ────────────────────────────────────────────────────────────────
  private url(path: string, query?: Record<string, string | number | string[] | undefined>) {
    const qs = Object.entries(query ?? {})
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(Array.isArray(v) ? v.join(",") : String(v))}`)
      .join("&");
    return `${this.o.baseUrl.replace(/\/+$/, "")}/api/v1${path}${qs ? `?${qs}` : ""}`;
  }

  private async send<R>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, opts: { query?: Record<string, string | number | string[] | undefined>; body?: unknown; auth?: boolean } = {}): Promise<R> {
    const auth = opts.auth !== false;
    // Captured synchronously, before any await: identifies which sign-out cycle this request
    // belongs to, regardless of how long it (or anything else) takes afterwards.
    const epoch = this.auth.epoch;
    const attempt = async (token: string | undefined) => {
      const headers: Record<string, string> = {};
      if (auth) { if (token) headers.authorization = `Bearer ${token}`; if (this.o.bu) headers["x-business-unit"] = this.o.bu; }
      const started = Date.now();
      const res = await request({ method, url: this.url(path, opts.query), headers, body: opts.body, insecureTls: this.o.insecureTls });
      this.o.trace?.(`${method} ${path} → ${res.status} (${Date.now() - started} ms)`);
      return { res, used: token };
    };
    let { res, used } = await attempt(auth ? await this.o.tokens.getAccessToken() : undefined);
    if (auth && res.status === 401) {
      const current = await this.o.tokens.getAccessToken();
      const fresh = current && current !== used ? current : await this.refreshOnce();
      if (fresh) ({ res, used } = await attempt(fresh));
      if (res.status === 401) { await this.signOutOnce(epoch); }
    }
    if (res.status >= 400) { const { message, detail } = detailToMessage(res.status, res.text); throw new ApiError(res.status, message, detail); }
    if (res.status === 204 || !res.text) return undefined as R;
    return JSON.parse(res.text) as R;
  }

  /** Coalesce parallel 401s into a single refresh so the refresh token is used once. */
  private refreshOnce(): Promise<string | undefined> {
    if (!this.refreshing) {
      this.refreshing = this.o.tokens.refreshAccessToken().finally(() => { this.refreshing = null; });
    }
    return this.refreshing;
  }

  /** Coalesce concurrent terminal-401s into one sign-out, scoped to an auth-session epoch rather
   *  than to overlapping traffic or elapsed time. A request that started before the current
   *  sign-out cycle began (its captured `epoch` matches `this.auth.epoch`) triggers — and shares
   *  — that cycle; anything else (including a request from an *earlier* now-superseded cycle, or
   *  one that started after this cycle already advanced the epoch) just rides whatever cycle is
   *  current. The epoch only advances on an actual sign-out, so an unrelated long-lived request
   *  (e.g. a 30s poll) sitting in flight across a re-sign-in can never block or wedge the next
   *  sign-out — there's no counter to fail to drain. */
  private signOutOnce(epoch: number): Promise<void> {
    if (epoch !== this.auth.epoch) return this.auth.signingOut ?? Promise.resolve();
    this.auth.epoch++;
    this.auth.signingOut = this.o.tokens.signOut()
      .then(() => { for (const l of [...this.auth.listeners]) l(); });
    return this.auth.signingOut;
  }

  getJson<R>(path: string, query?: Record<string, string | number | string[] | undefined>) { return this.send<R>("GET", path, { query }); }
  postJson<R>(path: string, body?: unknown) { return this.send<R>("POST", path, { body }); }
  putJson<R>(path: string, body: unknown) { return this.send<R>("PUT", path, { body }); }
  deleteJson<R>(path: string) { return this.send<R>("DELETE", path); }

  // ─── auth (public) ───────────────────────────────────────────────────────
  authConfig() { return this.send<T.AuthConfig>("GET", "/auth/config", { auth: false }); }
  login(email: string, password: string) { return this.send<T.TokenPair>("POST", "/auth/token", { body: { email, password }, auth: false }); }
  refresh(refresh_token: string) { return this.send<T.TokenPair>("POST", "/auth/refresh", { body: { refresh_token }, auth: false }); }
  ssoLoginUrl(port: number, nonce: string) { return this.url("/auth/oidc/login", { cli_port: port, cli_nonce: nonce }); }

  // ─── data ────────────────────────────────────────────────────────────────
  listBusinessUnits() { return this.getJson<T.BusinessUnit[]>("/business-units"); }
  listWorkspaces() { return this.getJson<T.Workspace[]>("/workspaces"); }
  getWorkspace(id: string) { return this.getJson<T.Workspace>(`/workspaces/${enc(id)}`); }
  updateWorkspace(id: string, patch: Partial<Pick<T.Workspace, "repo_ref">>) { return this.putJson<T.Workspace>(`/workspaces/${enc(id)}`, patch); }
  listBranches(id: string) { return this.getJson<T.Branches>(`/workspaces/${enc(id)}/branches`); }
  syncWorkspace(id: string) { return this.postJson<unknown>(`/workspaces/${enc(id)}/sync`); }
  triggerRun(id: string, body: T.TriggerRunBody) { return this.postJson<T.Run>(`/workspaces/${enc(id)}/runs`, body); }
  listRuns(q: { limit?: number; status?: string[]; workspace_id?: string } = {}) { return this.getJson<T.Run[]>("/runs", { limit: q.limit, status: q.status, workspace_id: q.workspace_id }); }
  getRun(id: string) { return this.getJson<T.Run>(`/runs/${enc(id)}`); }
  getSteps(id: string, since?: number, includeOutput = true) { return this.getJson<T.RunStep[]>(`/runs/${enc(id)}/steps`, { since, include_output: includeOutput ? undefined : "false" }); }
  getGraph(id: string) { return this.getJson<T.RunGraph>(`/runs/${enc(id)}/graph`); }
  getPlan(id: string) { return this.getJson<{ plan_output: string | null }>(`/runs/${enc(id)}/plan`); }
  approve(id: string) { return this.postJson<unknown>(`/runs/${enc(id)}/approve`); }
  reject(id: string, reason?: string) { return this.postJson<unknown>(`/runs/${enc(id)}/reject`, reason ? { comment: reason } : undefined); }
  cancel(id: string) { return this.postJson<unknown>(`/runs/${enc(id)}/cancel`); }
}

const enc = encodeURIComponent;
