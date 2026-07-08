import { useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

// Brand-asset cache-buster. nginx sends `Cache-Control: public, immutable` on
// SVGs/PNGs, so without this query string browsers serve the previously-cached
// design forever. Bump this string any time the SVGs in public/td/brand/ change.
const BRAND_VERSION = "v=2026-05-11";
import Dashboard from "./pages/Dashboard";
import Inventory from "./pages/Inventory";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import AuditLog from "./pages/AuditLog";
import Users from "./pages/Users";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import { useCurrentUser, hasMinRole, getValidToken } from "./hooks/useAuth";
import { setToken } from "./api/client";
import { Badge, cx } from "./components/ui";
import BusinessUnitSwitcher from "./components/BusinessUnitSwitcher";
import PresenceStack from "./components/PresenceStack";
import ThemeToggle from "./components/ThemeToggle";
import BusinessUnits from "./pages/BusinessUnits";
import { useBusinessUnitSelection } from "./hooks/useBusinessUnit";

type NavItem = {
  to: string;
  label: string;
  iconId: string;
  roleGate?: "operator" | "admin";
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
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" /></>,
};

function NavIcon({ name, className }: { name: string; className?: string }) {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
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
        "group relative flex items-center rounded-lg text-sm font-medium transition-all",
        collapsed ? "justify-center px-0 py-2.5" : "gap-2.5 px-3 py-2",
        active
          ? "bg-gradient-to-r from-brand-50 to-brand-50/30 text-brand-700 shadow-td-sm ring-1 ring-brand-200/70 dark:from-brand-500/20 dark:to-transparent dark:text-brand-100 dark:ring-brand-500/25"
          : "text-brand-textSoft hover:bg-brand-surface2 hover:text-brand-text dark:hover:bg-white/5",
      )}
    >
      {active && !collapsed && (
        <span className="absolute left-0 top-1/2 h-6 w-[3px] -translate-y-1/2 rounded-r-full bg-brand-500 dark:bg-brand-400" />
      )}
      <NavIcon
        name={icon}
        className={active ? "text-brand-600 dark:text-brand-300" : "text-brand-muted group-hover:text-brand-700 dark:group-hover:text-brand-100"}
      />
      {!collapsed && label}
    </Link>
  );
}

const SIDEBAR_KEY = "terraducktel_sidebar_collapsed";

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

  const items: NavItem[] = [
    { to: "/", label: "Dashboard", iconId: "dashboard" },
    { to: "/runs", label: "Runs", iconId: "runs" },
    { to: "/inventory", label: "Inventory", iconId: "inventory" },
    { to: "/audit", label: "Audit log", iconId: "audit", roleGate: "admin" },
    { to: "/users", label: "Users", iconId: "users", roleGate: "admin" },
    { to: "/business-units", label: "Business Units", iconId: "building", roleGate: "admin" },
    { to: "/settings", label: "Settings", iconId: "settings" },
  ];

  function logout() {
    setToken(null);
    window.location.href = "/";
  }

  return (
    <aside
      className={cx(
        "hidden md:sticky md:top-0 md:flex md:h-screen md:shrink-0 md:flex-col md:overflow-y-auto md:overflow-x-hidden md:border-r md:py-5 md:transition-[width] md:duration-200 md:border-brand-border md:bg-gradient-to-b md:from-brand-surface md:to-brand-surface2/40 dark:md:to-brand-ink/40",
        collapsed ? "md:w-[68px] md:px-2" : "md:w-60 md:px-3",
      )}
    >
      <div className={cx("mb-5 flex items-center", collapsed ? "justify-center px-0" : "justify-between px-2")}>
        <Link to="/" className="flex items-center gap-2.5 overflow-hidden">
          <img src={`/td/brand/terraducktel-mark.svg?${BRAND_VERSION}`} alt="Terraducktel" className="h-9 w-9 shrink-0 drop-shadow-sm" />
          {!collapsed && (
            <span className="font-display text-lg font-semibold tracking-tight text-brand-700 dark:text-brand-100">Terraducktel</span>
          )}
        </Link>
        {!collapsed && (
          <button
            onClick={toggleCollapsed}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
            className="grid h-7 w-7 place-items-center rounded-md text-brand-muted transition-colors hover:bg-brand-surface2 hover:text-brand-text dark:hover:bg-white/5"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /><path d="m15 9-3 3 3 3" />
            </svg>
          </button>
        )}
      </div>

      {!collapsed && <BusinessUnitSwitcher />}

      {!collapsed && (
        <p className="mb-1.5 mt-2 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-brand-muted">
          Navigate
        </p>
      )}
      <nav className="flex flex-1 flex-col gap-1">
        {items.map((item) => {
          if (item.roleGate && !hasMinRole(user, item.roleGate)) return null;
          const active = item.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(item.to);
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
      </nav>

      {collapsed && (
        <button
          onClick={toggleCollapsed}
          title="Expand sidebar"
          aria-label="Expand sidebar"
          className="mb-2 grid h-9 w-full place-items-center rounded-lg text-brand-muted transition-colors hover:bg-brand-surface2 hover:text-brand-text dark:hover:bg-white/5"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /><path d="m9 9 3 3-3 3" />
          </svg>
        </button>
      )}

      {user && (
        <div
          className={cx(
            "mt-2 rounded-lg border border-brand-border bg-brand-surface2/70",
            collapsed ? "flex flex-col items-center gap-2 p-2" : "p-3",
          )}
        >
          {collapsed ? (
            <>
              <div title={user.email} className="grid h-8 w-8 place-items-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700 dark:bg-brand-500/20 dark:text-brand-200">
                {user.email[0]?.toUpperCase() ?? "?"}
              </div>
              <button onClick={logout} title="Sign out" className="grid h-7 w-7 place-items-center rounded-md text-brand-muted hover:bg-brand-surface hover:text-brand-text dark:hover:bg-white/5">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M16 17l5-5-5-5M21 12H9M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                </svg>
              </button>
            </>
          ) : (
            <div className="flex items-center gap-2">
              <div className="grid h-8 w-8 place-items-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700 dark:bg-brand-500/20 dark:text-brand-200">
                {user.email[0]?.toUpperCase() ?? "?"}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-brand-text">{user.email}</p>
                <Badge tone="info" className="mt-0.5">{user.role}</Badge>
              </div>
              <button onClick={logout} title="Sign out" className="rounded p-1.5 text-brand-muted hover:bg-brand-surface hover:text-brand-text dark:hover:bg-white/5">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
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
    <header className="border-b px-4 py-3 backdrop-blur md:hidden border-brand-border bg-brand-surface/90 dark:border-brand-border dark:bg-brand-surface/90">
      <div className="flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2">
          <img src={`/td/brand/terraducktel-mark.svg?${BRAND_VERSION}`} alt="Terraducktel" className="h-7 w-7" />
          <span className="font-display text-base font-semibold text-brand-700 dark:text-brand-100">Terraducktel</span>
        </Link>
        <span className="text-xs text-brand-muted">{loc.pathname}</span>
      </div>
    </header>
  );
}

const SECTION_TITLES: Array<[string, string]> = [
  ["/runs", "Runs"],
  ["/inventory", "Cloud inventory"],
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


function AuthedApp() {
  // Re-mount the route tree whenever the current Business Unit changes. Pages
  // fetch on mount and don't subscribe to BU changes individually; keying the
  // <Routes> on the BU slug tears each page down and rebuilds it so the new
  // scope's data is fetched. The trade-off is that in-progress form state on
  // the current page is discarded — which is the right behavior for a scope
  // switch.
  const [buSlug] = useBusinessUnitSelection();
  const loc = useLocation();
  return (
    <div className="min-h-screen bg-brand-bg text-brand-text dark:bg-brand-ink dark:text-brand-100">
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNav />
          <div className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-brand-border bg-brand-surface/70 px-4 py-2 backdrop-blur-md sm:px-8 lg:px-10 dark:border-brand-border dark:bg-brand-surface/60">
            <span className="text-sm font-medium text-brand-textSoft dark:text-slate-400">{sectionTitle(loc.pathname)}</span>
            <div className="flex items-center gap-3">
              <PresenceStack />
              <ThemeToggle />
            </div>
          </div>
          <main className="flex-1 px-4 py-6 sm:px-8 sm:py-8 lg:px-10">
            <div className="mx-auto max-w-6xl">
              <Routes key={buSlug ?? "__no_bu__"}>
                <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
                <Route path="/runs" element={<RequireAuth><Runs /></RequireAuth>} />
                <Route path="/runs/:id" element={<RequireAuth><RunDetail /></RequireAuth>} />
                <Route path="/inventory" element={<RequireAuth><Inventory /></RequireAuth>} />
                <Route path="/drift" element={<Navigate to="/inventory" replace />} />
                <Route path="/approvals" element={<Navigate to="/runs?status=awaiting_approval" replace />} />
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
  // SSO redirect path: capture token before the auth check below.
  if (window.location.pathname === "/auth/oidc-finish") {
    return <OidcFinish />;
  }
  if (!getValidToken()) return <Login />;
  return <AuthedApp />;
}
