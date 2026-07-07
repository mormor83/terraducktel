import { FormEvent, useEffect, useState } from "react";
import axios from "axios";
import {
  api,
  getSavedCredentials,
  setSavedCredentials,
  setToken,
} from "../api/client";
import { Button, Card, CardBody, Input, Label } from "../components/ui";

function formatLoginError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const d = err.response?.data;
    if (typeof d === "object" && d !== null && "detail" in d) {
      const det = (d as { detail: unknown }).detail;
      if (typeof det === "string") return det;
      if (Array.isArray(det)) return det.map((x) => JSON.stringify(x)).join("; ");
    }
    if (err.response?.status) return `Login failed (${err.response.status})`;
    if (err.message === "Network Error") {
      return "Cannot reach API — check that /api is proxied (nginx) or run Vite dev with the API up.";
    }
  }
  return "Login failed";
}

export default function Login() {
  const saved = getSavedCredentials();
  const initialEmail = saved?.email ?? (import.meta.env.DEV ? "admin@test.com" : "");
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState("");
  // "Remember me" now persists the email only — never the password. The user
  // still types their password on each visit, which is the correct posture
  // for any web app that isn't using a password manager or SSO.
  const [remember, setRemember] = useState(!!saved);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [authMode, setAuthMode] = useState<"local" | "oidc" | "both">("local");
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);

  useEffect(() => {
    api
      .get("/v1/auth/config")
      .then((r) => {
        const mode = r.data?.mode ?? "local";
        const enabled = !!r.data?.oidc_enabled;
        setAuthMode(mode);
        setOidcEnabled(enabled);
        // When SSO is the only path, skip the login UI entirely and send the
        // browser straight to the IdP. Otherwise the password form would flash
        // for a beat before the user could click "Sign in with SSO".
        if (mode === "oidc" && enabled) {
          window.location.href = "/api/v1/auth/oidc/login";
          return;
        }
        setConfigLoaded(true);
      })
      .catch(() => {
        // Stay in local mode if the probe fails — local login still works.
        setConfigLoaded(true);
      });
  }, []);

  async function doLogin(emailValue: string, passwordValue: string) {
    setError(null);
    setSubmitting(true);
    try {
      const r = await api.post("/v1/auth/token", { email: emailValue, password: passwordValue });
      setToken(r.data.access_token);
      if (remember) setSavedCredentials({ email: emailValue });
      else setSavedCredentials(null);
      window.location.href = "/";
    } catch (err) {
      setError(formatLoginError(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Auto-login on page load is gone — we no longer persist the password.
  // The "Remember me" checkbox just pre-fills the email field.

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    void doLogin(email, password);
  }

  if (!configLoaded) {
    return (
      <div
        className="grid min-h-screen w-full place-items-center bg-brand-bg px-4 py-10 dark:bg-brand-ink"
        style={{
          backgroundImage:
            "radial-gradient(1200px 600px at 80% 10%, rgba(94,184,90,0.18), transparent 55%), radial-gradient(900px 500px at 10% 90%, rgba(31,111,108,0.18), transparent 60%)",
        }}
      >
        <div className="flex flex-col items-center gap-3 text-brand-textSoft dark:text-brand-100/70">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-border border-t-brand-500" />
          <p className="text-sm">Signing you in…</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="grid min-h-screen w-full place-items-center bg-brand-bg px-4 py-10 dark:bg-brand-ink"
      style={{
        backgroundImage:
          "radial-gradient(1200px 600px at 80% 10%, rgba(94,184,90,0.18), transparent 55%), radial-gradient(900px 500px at 10% 90%, rgba(31,111,108,0.18), transparent 60%)",
      }}
    >
      <div className="grid w-full max-w-5xl items-center gap-10 md:grid-cols-2">
        {/* Hero — visible on md+ */}
        <div className="hidden md:block">
          <img
            src="/td/brand/terraducktel-logo-full.png?v=2026-05-11"
            alt="Terraducktel"
            className="mx-auto w-full max-w-md object-contain drop-shadow-xl"
          />
          <p className="mx-auto mt-4 max-w-md text-center text-sm text-brand-textSoft dark:text-brand-100/70">
            Automated Infrastructure &amp; Terraform — plan, review, apply.
          </p>
        </div>

        {/* Login card */}
        <Card className="w-full max-w-md justify-self-center border-brand-border bg-brand-surface shadow-td-lg dark:bg-brand-surface dark:border-brand-border">
          <CardBody className="space-y-6 p-8">
            <div className="text-center md:hidden">
              <img
                src="/td/brand/terraducktel-lockup.svg?v=2026-05-11"
                alt="Terraducktel"
                className="mx-auto h-20 w-auto object-contain"
              />
            </div>
            <div className="text-center">
              <h2 className="font-display text-2xl font-semibold text-brand-700 dark:text-brand-100">
                Welcome to Terraducktel
              </h2>
              <p className="mt-1 text-sm text-brand-textSoft dark:text-brand-100/70">
                Sign in to manage Terraform workspaces.
              </p>
            </div>
            {/* Password form shown when mode is `local` (no SSO at all) or
                `both` (SSO available but admin can fall back). When the API
                reports `mode === "oidc"` SSO is the only path, so hide the
                form entirely — otherwise users see two ways to sign in and
                expect the password one to work, which it won't. */}
            {authMode !== "oidc" && (
              <form onSubmit={onSubmit} className="space-y-4">
                <div>
                  <Label htmlFor="username">Email</Label>
                  <Input
                    id="username"
                    data-testid="username"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    autoComplete="username"
                    required
                  />
                </div>
                <div>
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    data-testid="password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                </div>
                <label className="flex select-none items-center gap-2 text-sm text-brand-textSoft dark:text-brand-100/70">
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                    className="h-4 w-4 rounded border-brand-border text-brand-500 focus:ring-brand-500"
                  />
                  Remember me on this device
                </label>
                {error && (
                  <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
                    {error}
                  </div>
                )}
                <Button
                  type="submit"
                  data-testid="login-button"
                  className="w-full"
                  disabled={submitting}
                >
                  {submitting ? "Signing in…" : "Sign in"}
                </Button>
              </form>
            )}
            {/* Divider only when BOTH forms are visible (mode === "both"). */}
            {oidcEnabled && authMode === "both" && (
              <div className="flex items-center gap-3 text-xs uppercase tracking-wider text-brand-muted">
                <span className="h-px flex-1 bg-brand-border" />
                <span>or</span>
                <span className="h-px flex-1 bg-brand-border" />
              </div>
            )}
            {oidcEnabled && (
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                onClick={() => {
                  window.location.href = "/api/v1/auth/oidc/login";
                }}
              >
                Sign in with SSO
              </Button>
            )}
            {authMode === "oidc" && !oidcEnabled && (
              <p className="text-center text-xs text-amber-700 dark:text-amber-300">
                SSO is selected but not configured. Ask an admin to populate
                the <code className="font-mono">/terraducktel-prod/auth-oidc-*</code> SSM parameters.
              </p>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
