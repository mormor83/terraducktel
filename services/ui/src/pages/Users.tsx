import { useEffect, useState } from "react";

import { api } from "../api/client";
import { listBusinessUnits, type BusinessUnit } from "../api/businessUnits";
import { useCurrentUser } from "../hooks/useAuth";
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  SectionHeader,
  Skeleton,
} from "../components/ui";

type Membership = {
  business_unit_id: string;
  business_unit_slug: string;
  business_unit_name: string;
  role: "operator" | "viewer";
};

type UserEntry = {
  id: string;
  email: string;
  role: string;
  auth_provider: string;
  is_superadmin: boolean;
  memberships: Membership[];
};

export default function Users() {
  const me = useCurrentUser();
  const [users, setUsers] = useState<UserEntry[]>([]);
  const [bus, setBus] = useState<BusinessUnit[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const [u, b] = await Promise.all([
        api.get<UserEntry[]>("/v1/users"),
        listBusinessUnits(),
      ]);
      setUsers(u.data);
      setBus(b);
      setErr(null);
    } catch (e: unknown) {
      const anyE = e as { response?: { data?: { detail?: string } }; message?: string };
      setErr(anyE.response?.data?.detail ?? anyE.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <SectionHeader
        eyebrow="Access"
        title="Users"
        subtitle="Members, superadmin flag, and per-Business-Unit roles."
      />

      {err && (
        <Card className="mb-4 border-red-900/50 bg-red-950/30">
          <CardBody className="text-sm text-red-300">{err}</CardBody>
        </Card>
      )}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <CardBody>
                <Skeleton className="h-4 w-1/3" />
              </CardBody>
            </Card>
          ))}
        </div>
      ) : users.length === 0 ? (
        <EmptyState title="No users yet" />
      ) : (
        <div className="space-y-3">
          {users.map((u) => (
            <UserCard
              key={u.id}
              user={u}
              bus={bus}
              canEdit={!!me?.is_superadmin}
              onChange={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function UserCard({
  user,
  bus,
  canEdit,
  onChange,
}: {
  user: UserEntry;
  bus: BusinessUnit[];
  canEdit: boolean;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [addingBu, setAddingBu] = useState<string>("");
  const [addingRole, setAddingRole] = useState<"operator" | "viewer">("operator");
  const [error, setError] = useState<string | null>(null);

  const availableBus = bus.filter(
    (b) => !user.memberships.some((m) => m.business_unit_id === b.id),
  );

  async function patch(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/v1/users/${user.id}`, body);
      onChange();
    } catch (e: unknown) {
      const anyE = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(anyE.response?.data?.detail ?? anyE.message ?? "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardBody>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700 dark:bg-brand-800/50 dark:text-brand-100">
              {user.email[0]?.toUpperCase()}
            </div>
            <div>
              <p className="text-sm font-medium text-brand-text dark:text-brand-100">
                {user.email}
              </p>
              <p className="text-xs text-brand-muted">{user.auth_provider}</p>
            </div>
          </div>
          {user.is_superadmin ? (
            <Badge tone="danger">SUPERADMIN</Badge>
          ) : (
            <Badge tone="neutral">{user.role}</Badge>
          )}
        </div>

        {!user.is_superadmin && (
          <div className="mt-4 space-y-2">
            {user.memberships.length === 0 && (
              <p className="text-xs text-brand-muted">No BU memberships.</p>
            )}
            {user.memberships.map((m) => (
              <div
                key={m.business_unit_id}
                className="flex items-center justify-between rounded-md border border-brand-border bg-brand-surface2 px-3 py-1.5 text-sm dark:border-brand-700 dark:bg-brand-800/40"
              >
                <span className="text-brand-text dark:text-brand-100">
                  <strong>{m.business_unit_name}</strong>{" "}
                  <span className="text-xs text-brand-muted">({m.business_unit_slug})</span>
                </span>
                <span className="flex items-center gap-2">
                  {canEdit ? (
                    <select
                      className="rounded border border-brand-border bg-brand-surface px-2 py-0.5 text-xs dark:border-brand-700 dark:bg-brand-900"
                      value={m.role}
                      disabled={busy}
                      onChange={(e) =>
                        patch({
                          add_memberships: [
                            {
                              business_unit_id: m.business_unit_id,
                              role: e.target.value,
                            },
                          ],
                        })
                      }
                    >
                      <option value="operator">operator</option>
                      <option value="viewer">viewer</option>
                    </select>
                  ) : (
                    <Badge tone="neutral">{m.role}</Badge>
                  )}
                  {canEdit && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() =>
                        patch({ remove_memberships: [m.business_unit_id] })
                      }
                    >
                      Remove
                    </Button>
                  )}
                </span>
              </div>
            ))}

            {canEdit && availableBus.length > 0 && (
              <div className="flex items-center gap-2 pt-1">
                <select
                  className="rounded border border-brand-border bg-brand-surface px-2 py-1 text-sm dark:border-brand-700 dark:bg-brand-900"
                  value={addingBu}
                  onChange={(e) => setAddingBu(e.target.value)}
                >
                  <option value="">+ Add to BU…</option>
                  {availableBus.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
                <select
                  className="rounded border border-brand-border bg-brand-surface px-2 py-1 text-sm dark:border-brand-700 dark:bg-brand-900"
                  value={addingRole}
                  onChange={(e) => setAddingRole(e.target.value as "operator" | "viewer")}
                >
                  <option value="operator">operator</option>
                  <option value="viewer">viewer</option>
                </select>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy || !addingBu}
                  onClick={async () => {
                    await patch({
                      add_memberships: [
                        { business_unit_id: addingBu, role: addingRole },
                      ],
                    });
                    setAddingBu("");
                  }}
                >
                  Add
                </Button>
              </div>
            )}
          </div>
        )}

        {canEdit && (
          <div className="mt-3 flex items-center gap-2 border-t border-brand-border pt-3 dark:border-brand-700">
            <span className="text-xs text-brand-muted">Superadmin:</span>
            <Button
              size="sm"
              variant={user.is_superadmin ? "danger" : "secondary"}
              disabled={busy}
              onClick={() =>
                patch({ is_superadmin: !user.is_superadmin })
              }
            >
              {user.is_superadmin ? "Demote" : "Promote"}
            </Button>
          </div>
        )}

        {error && (
          <p className="mt-2 text-sm text-red-400">{error}</p>
        )}
      </CardBody>
    </Card>
  );
}
