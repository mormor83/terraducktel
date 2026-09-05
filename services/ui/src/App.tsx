import { useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

// Brand-asset cache-buster. nginx sends `Cache-Control: public, immutable` on
// SVGs/PNGs, so without this query string browsers serve the previously-cached
// design forever. Bump this string any time the assets in public/td/brand/ change.
const BRAND_VERSION = "v=2026-09-01";
import Dashboard from "./pages/Dashboard";
import Inventory from "./pages/Inventory";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import AuditLog from "./pages/AuditLog";
import Users from "./pages/Users";
import Settings from "./pages/Settings";
import Policies from "./pages/Policies";
import Login from "./pages/Login";
import { useCurrentUser, hasMinRole, getValidToken } from "./hooks/useAuth";
import { setToken } from "./api/client";
import { cx } from "./components/ui";
import BusinessUnitSwitcher from "./components/BusinessUnitSwitcher";
import PresenceStack from "./components/PresenceStack";
import ThemeToggle from "./components/ThemeToggle";
import SplashScreen from "./components/SplashScreen";
import BusinessUnits from "./pages/BusinessUnits";
import { useBusinessUnitSelection } from "./hooks/useBusinessUnit";

type NavItem = {
  to: string;
  label: string;
  iconId: string;
  roleGate?: "operator" | "admin";
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

// Distinct, modern stroke icons per nav item (lucide-flavored). Inline so each
// item gets its own glyph instead of reusing the sprite's duplicated symbols.
const NAV_ICONS: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  runs: <><circle cx="12" cy="12" r="9" /><polygon points="10 8.5 16 12 10 15.5 10 8.5" fill="currentColor" stroke="none" /></>,
  inventory: <><path d="M12 2 2 7l10 5 10-5-10-5Z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" /></>,
  audit: <><path d="M8 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-2" /><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M8 11h8M8 15h6" /></>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11" /></>,
  building: <><rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M9 8h.01M15 8h.01M9 12h.01M15 12h.01M9 16h6" /></>,
  policies: <path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm0 5a2.5 2.5 0 0 1 2.5 2.5c0 1-.6 1.7-1.2 2.2-.5.4-.8.7-.8 1.3h-1c0-1 .5-1.6 1.1-2.1.5-.4.9-.7.9-1.4A1.5 1.5 0 0 0 12 7a1.5 1.5 0 0 0-1.5 1.5h-1A2.5 2.5 0 0 1 12 6Zm-.5 8h1v1h-1v-1Z" fill="currentColor" stroke="none" />,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" /></>,
};

// Nav sorted into the design's mono section headers. Every route, label,
// roleGate and icon is preserved from the previous flat list — grouping only.
const NAV_GROUPS: NavGroup[] = [
  {
    title: "PLATFORM",
    items: [
      { to: "/", label: "Dashboard", iconId: "dashboard" },
      { to: "/runs", label: "Runs", iconId: "runs" },
      { to: "/inventory", label: "Inventory", iconId: "inventory" },
    ],
  },
  {
    title: "GOVERNANCE",
    items: [
      { to: "/policies", label: "Policies", iconId: "policies", roleGate: "admin" },
      { to: "/audit", label: "Audit log", iconId: "audit", roleGate: "admin" },
    ],
  },
  {
    title: "ORGANISATION",
    items: [
      { to: "/users", label: "Users", iconId: "users", roleGate: "admin" },
      { to: "/business-units", label: "Business Units", iconId: "building", roleGate: "admin" },
      { to: "/settings", label: "Settings", iconId: "settings" },
    ],
  },
];

function NavIcon({ name, className }: { name: string; className?: string }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className={cx("shrink-0", className)} aria-hidden>
      {NAV_ICONS[name] ?? NAV_ICONS.dashboard}
    </svg>
  );
}

function NavLinkItem({
  to,
  label,
  icon,
  active,
  collapsed,
}: {
  to: string;
  label: string;
  icon: string;
  active: boolean;
  collapsed: boolean;
}) {
  return (
    <Link
      to={to}
      title={collapsed ? label : undefined}
      className={cx(
        "group relative flex items-center rounded-lg text-[13px] font-medium transition-colors",
        collapsed ? "justify-center px-0 py-2.5" : "gap-2.5 px-2.5 py-2",
        active
          ? "bg-gradient-to-r from-[rgba(var(--td-glow-rgb),0.14)] to-[rgba(var(--td-glow-rgb),0.02)] text-[var(--td-nav-ink)]"
          : "text-brand-textSoft hover:bg-[rgba(var(--td-edge-rgb),0.06)] hover:text-brand-text",
      )}
    >
      {/* lime rail with glow on the active item */}
      {active && (
        <span
          aria-hidden
          className="absolute bottom-[7px] left-0 top-[7px] w-[2px] rounded-[2px] bg-[var(--td-rail)] shadow-[var(--td-rail-glow)]"
        />
      )}
      <NavIcon
        name={icon}
        className={active ? "text-[var(--td-accent-ink)]" : "text-brand-muted opacity-85 group-hover:text-brand-text"}
      />
      {!collapsed && label}
    </Link>
  );
}

const SIDEBAR_KEY = "terraducktel_sidebar_collapsed";

/** OIDC users carry a `name` claim; local users get a prettified email local part. */
function displayNameFor(user: { name: string | null; email: string }): string {
  if (user.name) return user.name;
  const local = user.email.split("@")[0] ?? "";
  const pretty = local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => (w[0]?.toUpperCase() ?? "") + w.slice(1))
    .join(" ");
  return pretty || user.email;
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

function Sidebar() {
  const loc = useLocation();
  const user = useCurrentUser();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(SIDEBAR_KEY) === "1");

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
      return next;
    });
  }

  function logout() {
    setToken(null);
    window.location.href = "/";
  }

  const displayName = user ? displayNameFor(user) : "";

  return (
    <aside
      className={cx(
        "hidden md:sticky md:top-0 md:flex md:h-screen md:shrink-0 md:flex-col md:overflow-hidden md:border-r md:border-brand-border md:bg-gradient-to-b md:from-[var(--td-shell-from)] md:to-[var(--td-shell-to)] md:transition-[width] md:duration-200",
        collapsed ? "md:w-[68px]" : "md:w-[236px]",
      )}
    >
      {/* Brand lockup. The mockup puts a 130px concentric ring ornament behind
          this, but at the sidebar's width its arcs escape the lockup entirely
          and sweep down across the divider and the nav — on a real screen they
          read as a stray hair rather than as decoration. Removed deliberately;
          don't reinstate it without confining it to the lockup's own bounds. */}
      <div
        className={cx(
          "flex items-center border-b border-brand-border",
          collapsed ? "justify-center px-0 py-4" : "gap-[11px] px-[18px] pb-4 pt-[18px]",
        )}
      >
        <Link to="/" className="flex min-w-0 items-center gap-[11px]">
          <img
            src={`/td/brand/terraducktel-duck.png?${BRAND_VERSION}`}
            alt="TerraDuckTel"
            className="h-[38px] w-[38px] shrink-0 rounded-full shadow-[var(--td-mark-shadow)]"
          />
          {!collapsed && (
            <span className="min-w-0">
              <b className="block font-display text-[15px] font-bold leading-[1.1] tracking-[0.4px] text-brand-text">
                TerraDuckTel
              </b>
              <span className="mt-[3px] block whitespace-nowrap font-mono text-[8.5px] tracking-[1.4px] text-brand-muted">
                AUTOMATED INFRASTRUCTURE
              </span>
            </span>
          )}
        </Link>
      </div>

      {/* Grouped nav */}
      <nav className={cx("flex-1 overflow-y-auto overflow-x-hidden py-4", collapsed ? "px-2" : "px-3")}>
        {NAV_GROUPS.map((group, gi) => {
          const visible = group.items.filter(
            (item) => !item.roleGate || hasMinRole(user, item.roleGate),
          );
          if (visible.length === 0) return null;
          return (
            <div key={group.title} className="mb-5">
              {collapsed ? (
                gi > 0 && <div aria-hidden className="mx-2 mb-2 h-px bg-brand-border" />
              ) : (
                <h6 className="mb-[7px] px-2.5 font-mono text-[9.5px] font-medium tracking-[1.6px] text-brand-muted">
                  {group.title}
                </h6>
              )}
              <div className="flex flex-col gap-0.5">
                {visible.map((item) => {
                  const active =
                    item.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(item.to);
                  return (
                    <NavLinkItem
                      key={item.to}
                      to={item.to}
                      label={item.label}
                      icon={item.iconId}
                      active={active}
                      collapsed={collapsed}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Collapse / expand toggle */}
      <div className={cx("pb-1.5", collapsed ? "px-2" : "px-3")}>
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cx(
            "grid h-8 place-items-center rounded-lg text-brand-muted transition-colors hover:bg-[rgba(var(--td-edge-rgb),0.06)] hover:text-brand-text",
            collapsed ? "w-full" : "ml-auto w-8",
          )}
        >
          {collapsed ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /><path d="m9 9 3 3-3 3" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /><path d="m15 9-3 3 3 3" />
            </svg>
          )}
        </button>
      </div>

      {/* Footer user block */}
      {user && (
        <div className={cx("border-t border-brand-border", collapsed ? "p-2" : "p-3")}>
          {collapsed ? (
            <div className="flex flex-col items-center gap-2 py-1">
              <div
                title={user.email}
                className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-gradient-to-br from-[var(--td-avatar-from)] to-[var(--td-avatar-to)] text-xs font-bold text-[var(--td-avatar-ink)]"
              >
                {initialsFor(displayName)}
              </div>
              <button
                onClick={logout}
                title="Sign out"
                className="grid h-7 w-7 place-items-center rounded-md text-brand-muted transition-colors hover:bg-[rgba(var(--td-edge-rgb),0.06)] hover:text-brand-text"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M16 17l5-5-5-5M21 12H9M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                </svg>
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2.5 rounded-lg p-2 transition-colors hover:bg-[rgba(var(--td-edge-rgb),0.06)]">
              <div
                title={user.email}
                className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-gradient-to-br from-[var(--td-avatar-from)] to-[var(--td-avatar-to)] text-xs font-bold text-[var(--td-avatar-ink)]"
              >
                {initialsFor(displayName)}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[12.5px] font-semibold text-brand-text" title={user.email}>
                  {displayName}
                </p>
                <p className="truncate font-mono text-[10.5px] text-brand-muted">
                  {user.is_superadmin ? `${user.role} · superadmin` : user.role}
                </p>
              </div>
              <button
                onClick={logout}
                title="Sign out"
                className="rounded-md p-1.5 text-brand-muted transition-colors hover:bg-[rgba(var(--td-edge-rgb),0.06)] hover:text-brand-text"
              >
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M16 17l5-5-5-5M21 12H9M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                </svg>
              </button>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function MobileNav() {
  const loc = useLocation();
  return (
    <header className="border-b border-brand-border bg-[var(--td-chrome)] px-4 py-3 backdrop-blur md:hidden">
      <div className="flex items-center justify-between gap-3">
        <Link to="/" className="flex min-w-0 items-center gap-2.5">
          <img
            src={`/td/brand/terraducktel-duck.png?${BRAND_VERSION}`}
            alt="TerraDuckTel"
            className="h-8 w-8 shrink-0 rounded-full shadow-[var(--td-mark-shadow)]"
          />
          <span className="min-w-0">
            <b className="block font-display text-[14px] font-bold leading-[1.1] tracking-[0.4px] text-brand-text">
              TerraDuckTel
            </b>
            <span className="mt-px block truncate font-mono text-[8px] tracking-[1.4px] text-brand-muted">
              AUTOMATED INFRASTRUCTURE
            </span>
          </span>
        </Link>
        <span className="shrink-0 font-mono text-[11px] text-brand-muted">{loc.pathname}</span>
      </div>
    </header>
  );
}

const SECTION_TITLES: Array<[string, string]> = [
  ["/runs", "Runs"],
  ["/inventory", "Cloud inventory"],
  ["/policies", "Policies"],
  ["/audit", "Audit log"],
  ["/users", "Users"],
  ["/business-units", "Business Units"],
  ["/settings", "Settings"],
];

function sectionTitle(pathname: string): string {
  if (pathname === "/") return "Dashboard";
  const hit = SECTION_TITLES.find(([prefix]) => pathname.startsWith(prefix));
  return hit ? hit[1] : "Terraducktel";
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!getValidToken()) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function OidcFinish() {
  const loc = useLocation();
  const params = new URLSearchParams(loc.search);
  const access = params.get("access_token");
  if (access) {
    setToken(access);
    // Wipe the URL to avoid leaking the token via referrer/history.
    window.history.replaceState({}, "", "/");
    window.location.href = "/";
    return null;
  }
  return <Navigate to="/" replace />;
}

function Topbar() {
  const loc = useLocation();
  return (
    <div className="sticky top-0 z-30 flex h-[60px] items-center gap-4 border-b border-brand-border bg-[var(--td-chrome)] px-4 backdrop-blur-[14px] sm:px-6 lg:px-[26px]">
      {/* Breadcrumb */}
      <div className="flex min-w-0 items-center gap-2 font-mono text-[13px] text-brand-muted">
        <span className="hidden sm:inline">terraducktel</span>
        <span className="hidden opacity-40 sm:inline">/</span>
        <b className="truncate font-medium text-brand-text">{sectionTitle(loc.pathname).toLowerCase()}</b>
      </div>

      <div className="ml-auto flex items-center gap-3">
        {/* Search box — presentational only: no global search exists in the app
            yet, so this is intentionally wired to nothing (per the v2 design). */}
        <div className="relative hidden items-center md:flex">
          <svg
            className="pointer-events-none absolute left-[11px] h-[15px] w-[15px] text-brand-muted"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden
          >
            <circle cx="11" cy="11" r="6.5" /><path d="M16 16l5 5" />
          </svg>
          <input
            placeholder="Search workspaces, runs…"
            aria-label="Search (coming soon)"
            className="h-[34px] w-[268px] rounded-lg border border-brand-border bg-brand-surface pl-[34px] pr-[44px] text-[13px] text-brand-text outline-none transition-[border-color,box-shadow] placeholder:text-brand-muted focus:border-brand-borderStrong focus:shadow-[0_0_0_3px_rgba(var(--td-glow-rgb),0.08)]"
          />
          <span className="pointer-events-none absolute right-[9px] rounded border border-brand-border px-[5px] py-px font-mono text-[9.5px] text-brand-muted">
            ⌘K
          </span>
        </div>

        <BusinessUnitSwitcher />

        {/* Status pill with pulsing dot */}
        <div className="hidden h-[30px] items-center gap-[7px] rounded-full border border-[rgba(var(--td-ok-rgb),0.2)] bg-[rgba(var(--td-ok-rgb),0.09)] px-3 font-mono text-[11px] text-[var(--td-green-ink)] lg:inline-flex">
          <span
            aria-hidden
            className="h-[6px] w-[6px] rounded-full bg-current shadow-[0_0_8px_currentColor]"
            style={{ animation: "bp 1.8s ease-in-out infinite" }}
          />
          ALL SYSTEMS NOMINAL
        </div>

        <PresenceStack />
        <ThemeToggle />
      </div>
    </div>
  );
}

function AuthedApp() {
  // Re-mount the route tree whenever the current Business Unit changes. Pages
  // fetch on mount and don't subscribe to BU changes individually; keying the
  // <Routes> on the BU slug tears each page down and rebuilds it so the new
  // scope's data is fetched. The trade-off is that in-progress form state on
  // the current page is discarded — which is the right behavior for a scope
  // switch.
  const [buSlug] = useBusinessUnitSelection();
  return (
    <div className="min-h-screen bg-brand-bg text-brand-text">
      {/* Keyframes Tailwind can't express (the status-dot pulse). Namespaced
          `td-` to avoid colliding with theme-level CSS. */}
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNav />
          <Topbar />
          <main className="flex-1 px-4 pb-[60px] pt-7 sm:px-6 lg:px-[26px]">
            <div className="mx-auto w-full max-w-[1420px]">
              <Routes key={buSlug ?? "__no_bu__"}>
                <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
                <Route path="/runs" element={<RequireAuth><Runs /></RequireAuth>} />
                <Route path="/runs/:id" element={<RequireAuth><RunDetail /></RequireAuth>} />
                <Route path="/inventory" element={<RequireAuth><Inventory /></RequireAuth>} />
                <Route path="/drift" element={<Navigate to="/inventory" replace />} />
                <Route path="/approvals" element={<Navigate to="/runs?status=awaiting_approval" replace />} />
                <Route path="/policies" element={<RequireAuth><Policies /></RequireAuth>} />
                <Route path="/audit" element={<RequireAuth><AuditLog /></RequireAuth>} />
                <Route path="/aws" element={<Navigate to="/settings#cloud" replace />} />
                <Route path="/users" element={<RequireAuth><Users /></RequireAuth>} />
                <Route path="/business-units" element={<RequireAuth><BusinessUnits /></RequireAuth>} />
                <Route path="/clusters" element={<Navigate to="/settings#cloud" replace />} />
                <Route path="/gcp" element={<Navigate to="/settings#cloud" replace />} />
                <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  // SSO redirect path: capture token before the auth check below. No splash
  // here — this route is a bounce-through that already renders its own
  // "Signing you in…" state.
  if (window.location.pathname === "/auth/oidc-finish") {
    return <OidcFinish />;
  }
  // The splash is a sibling, not a wrapper: the real tree mounts and fetches
  // underneath it while the curtain holds, and the curtain is
  // pointer-events:none so it never blocks that tree.
  return (
    <>
      <SplashScreen />
      {getValidToken() ? <AuthedApp /> : <Login />}
    </>
  );
}
