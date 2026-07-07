import axios, { type AxiosInstance } from "axios";

const TOKEN_KEY = "terraducktel_token";
const REMEMBER_KEY = "terraducktel_remember"; // "1" if user opted in
const SAVED_EMAIL_KEY = "terraducktel_saved_email";

// Legacy keys — purged on every load. Old builds wrote the cleartext password
// to localStorage; if anyone is still carrying that around, blow it away.
const LEGACY_PASSWORD_KEY = "terraducktel_saved_password";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** "Remember me" — email-only.
 *
 * We used to also stash the cleartext password so we could auto-replay login
 * on the next visit. That's an XSS-to-account-takeover footgun and is now
 * removed; we only remember the email so the form pre-fills.
 *
 * The proper "stay signed in" path is the refresh-token flow (or OIDC SSO),
 * not credential replay.
 */
export type SavedCredentials = { email: string };

export function getSavedCredentials(): SavedCredentials | null {
  // Defence in depth: scrub any leftover password from prior builds.
  if (localStorage.getItem(LEGACY_PASSWORD_KEY) !== null) {
    localStorage.removeItem(LEGACY_PASSWORD_KEY);
  }
  if (localStorage.getItem(REMEMBER_KEY) !== "1") return null;
  const email = localStorage.getItem(SAVED_EMAIL_KEY);
  if (!email) return null;
  return { email };
}

export function setSavedCredentials(creds: SavedCredentials | null): void {
  // Always clear the legacy password slot.
  localStorage.removeItem(LEGACY_PASSWORD_KEY);
  if (creds) {
    localStorage.setItem(REMEMBER_KEY, "1");
    localStorage.setItem(SAVED_EMAIL_KEY, creds.email);
  } else {
    localStorage.removeItem(REMEMBER_KEY);
    localStorage.removeItem(SAVED_EMAIL_KEY);
  }
}

// Persisted Business Unit selection. Read by the request interceptor so every
// API call carries the user's current BU scope; written by the sidebar
// switcher. The empty string ("") means "all BUs" (superadmin only).
const BU_KEY = "terraducktel_business_unit";

export function getCurrentBusinessUnit(): string | null {
  return localStorage.getItem(BU_KEY);
}

export function setCurrentBusinessUnit(slug: string | null): void {
  if (slug === null) localStorage.removeItem(BU_KEY);
  else localStorage.setItem(BU_KEY, slug);
  // Notify any open tabs (and this tab's listeners) that the scope changed.
  // Cheaper than a Context provider for a single global selection.
  window.dispatchEvent(new Event("terraducktel:bu-changed"));
}

export function createApiClient(): AxiosInstance {
  const c = axios.create({
    baseURL: "/api",
    headers: { "Content-Type": "application/json" },
  });
  c.interceptors.request.use((config) => {
    const t = getToken();
    if (t) {
      config.headers.Authorization = `Bearer ${t}`;
    }
    // Don't clobber an explicit per-call header (some callers set
    // X-Business-Unit: "" to request "all BUs" or "no filter").
    const hasExplicitBu =
      config.headers != null && "X-Business-Unit" in config.headers;
    if (!hasExplicitBu) {
      const bu = getCurrentBusinessUnit();
      if (bu !== null && bu !== "") {
        config.headers["X-Business-Unit"] = bu;
      } else if (bu === "") {
        config.headers["X-Business-Unit"] = "all";
      }
    }
    return config;
  });
  return c;
}

export const api = createApiClient();
