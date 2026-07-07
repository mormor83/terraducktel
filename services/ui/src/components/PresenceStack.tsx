import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useCurrentUser } from "../hooks/useAuth";
import { useBusinessUnitSelection } from "../hooks/useBusinessUnit";

type PresenceUser = {
  user_id: string;
  email: string;
  display_name: string | null;
  bu_slug: string | null;
  last_seen_at: string;
};

const HEARTBEAT_MS = 30_000;

function initialFromUser(u: PresenceUser): string {
  const name = (u.display_name || u.email).trim();
  return name[0]?.toUpperCase() ?? "?";
}

// Deterministic-but-distinct background colour per user, so repeated avatars
// are recognisable at a glance. Hash on user_id (stable across renames).
function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++) {
    h = (h * 31 + userId.charCodeAt(i)) >>> 0;
  }
  const palette = [
    "bg-sky-500", "bg-emerald-500", "bg-violet-500", "bg-amber-500",
    "bg-rose-500", "bg-cyan-500", "bg-fuchsia-500", "bg-lime-500",
  ];
  return palette[h % palette.length];
}

/**
 * Cross-BU presence indicator rendered at the top of the page.
 *
 *  - Heartbeats `POST /v1/presence` every 30s with the currently selected BU slug.
 *  - Polls `GET  /v1/presence` every 30s to refresh the avatar stack.
 *  - Highlights users in a *different* BU from the viewer so it's obvious at a
 *    glance that "someone else is working over there" — the whole point of the
 *    feature.
 */
export default function PresenceStack() {
  const user = useCurrentUser();
  const [selectedSlug] = useBusinessUnitSelection();
  const [users, setUsers] = useState<PresenceUser[]>([]);
  const [hover, setHover] = useState(false);

  useEffect(() => {
    if (!user) return;
    let alive = true;

    async function ping() {
      try {
        await api.post("/v1/presence", { bu_slug: selectedSlug ?? null });
      } catch {
        // Best-effort — ignore network blips; next tick retries.
      }
    }
    async function refresh() {
      try {
        const r = await api.get("/v1/presence");
        if (alive) setUsers(r.data.users ?? []);
      } catch {
        // ignore
      }
    }

    void ping().then(refresh);
    const t = window.setInterval(async () => {
      await ping();
      if (alive) await refresh();
    }, HEARTBEAT_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [user?.id, selectedSlug]);

  if (!user || users.length === 0) return null;

  // Show up to 5 avatars; surface the rest as "+N" with the full list in the
  // expanded panel on hover.
  const visible = users.slice(0, 5);
  const extra = users.length - visible.length;

  return (
    <div
      className="relative flex items-center gap-2"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span className="hidden text-[11px] text-slate-500 sm:inline" title="Users active in the last 60s, across all BUs">
        {users.length} online
      </span>
      <div className="flex -space-x-1.5">
        {visible.map((u) => {
          const sameBu = (u.bu_slug ?? null) === (selectedSlug ?? null);
          const isSelf = u.user_id === user.id;
          return (
            <div
              key={u.user_id}
              title={`${u.display_name || u.email}${u.bu_slug ? ` · ${u.bu_slug}` : " · all BUs"}${isSelf ? " (you)" : ""}`}
              className={`grid h-6 w-6 place-items-center rounded-full text-[10px] font-semibold text-white ring-2 ${
                sameBu
                  ? "ring-white dark:ring-slate-900"
                  : "ring-amber-400 dark:ring-amber-500"
              } ${colorFor(u.user_id)}`}
            >
              {initialFromUser(u)}
            </div>
          );
        })}
        {extra > 0 && (
          <div className="grid h-6 w-6 place-items-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-700 ring-2 ring-white dark:bg-slate-700 dark:text-slate-200 dark:ring-slate-900">
            +{extra}
          </div>
        )}
      </div>

      {hover && (
        <div className="absolute right-0 top-full z-40 mt-2 w-64 rounded-lg border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900">
          <p className="px-2 pb-1 pt-0.5 text-[10px] uppercase tracking-wider text-slate-500">
            Active now ({users.length})
          </p>
          <ul className="max-h-72 overflow-auto">
            {users.map((u) => {
              const sameBu = (u.bu_slug ?? null) === (selectedSlug ?? null);
              const isSelf = u.user_id === user.id;
              return (
                <li key={u.user_id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-slate-50 dark:hover:bg-slate-800">
                  <div className={`grid h-5 w-5 place-items-center rounded-full text-[9px] font-semibold text-white ${colorFor(u.user_id)}`}>
                    {initialFromUser(u)}
                  </div>
                  <span className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-200">
                    {u.display_name || u.email}
                    {isSelf && <span className="ml-1 text-slate-400">(you)</span>}
                  </span>
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${
                      sameBu
                        ? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                        : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                    }`}
                  >
                    {u.bu_slug ?? "all"}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
