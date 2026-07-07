import { FormEvent, useState } from "react";

import {
  createBusinessUnit,
  updateBusinessUnit,
  type BusinessUnit,
} from "../api/businessUnits";
import { useBusinessUnits } from "../hooks/useBusinessUnit";
import { useCurrentUser } from "../hooks/useAuth";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
  Label,
  SectionHeader,
  Skeleton,
} from "../components/ui";

export default function BusinessUnits() {
  const user = useCurrentUser();
  const { bus, loading, error, refresh } = useBusinessUnits();
  const [showForm, setShowForm] = useState(false);
  const canEdit = !!user?.is_superadmin;

  return (
    <div>
      <SectionHeader
        eyebrow="Tenancy"
        title="Business Units"
        subtitle="Tenants. Each BU owns its own AWS accounts, GitHub integration, and workspaces."
      />

      {user?.is_superadmin && !showForm && (
        <div className="mb-4">
          <Button variant="primary" onClick={() => setShowForm(true)}>
            + New Business Unit
          </Button>
        </div>
      )}

      {user?.is_superadmin && showForm && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle>New Business Unit</CardTitle>
          </CardHeader>
          <CardBody>
            <NewBusinessUnitForm
              onCancel={() => setShowForm(false)}
              onCreated={() => {
                setShowForm(false);
                refresh();
              }}
            />
          </CardBody>
        </Card>
      )}

      {error && (
        <Card className="mb-4 border-red-900/50 bg-red-950/30">
          <CardBody className="text-sm text-red-300">{error}</CardBody>
        </Card>
      )}

      {loading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Card key={i}>
              <CardBody>
                <Skeleton className="h-4 w-1/3" />
              </CardBody>
            </Card>
          ))}
        </div>
      ) : bus.length === 0 ? (
        <EmptyState title="No business units" />
      ) : (
        <div className="space-y-3">
          {bus.map((b) => (
            <BusinessUnitCard key={b.id} bu={b} canEdit={canEdit} onChange={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}

function BusinessUnitCard({
  bu,
  canEdit,
  onChange,
}: {
  bu: BusinessUnit;
  canEdit: boolean;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(bu.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!name.trim() || name.trim() === bu.name) {
      setEditing(false);
      setName(bu.name);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updateBusinessUnit(bu.id, { name: name.trim() });
      setEditing(false);
      onChange();
    } catch (e: unknown) {
      const anyE = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(anyE.response?.data?.detail ?? anyE.message ?? "Rename failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardBody>
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0 flex-1">
            {editing ? (
              <div className="flex items-center gap-2">
                <Input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") save();
                    if (e.key === "Escape") {
                      setEditing(false);
                      setName(bu.name);
                      setError(null);
                    }
                  }}
                  disabled={busy}
                  className="max-w-xs"
                />
                <Button size="sm" variant="primary" onClick={save} disabled={busy}>
                  {busy ? "Saving…" : "Save"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false);
                    setName(bu.name);
                    setError(null);
                  }}
                  disabled={busy}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <h3 className="truncate text-base font-semibold text-brand-text dark:text-brand-100">
                {bu.name}
              </h3>
            )}
            <p className="mt-0.5 text-xs text-brand-muted">
              slug: {bu.slug}{" "}
              <span className="text-brand-muted/70">· immutable</span>
            </p>
            {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {bu.created_at && (
              <p className="text-xs text-brand-muted">
                created {new Date(bu.created_at).toLocaleDateString()}
              </p>
            )}
            {canEdit && !editing && (
              <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                Rename
              </Button>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function NewBusinessUnitForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createBusinessUnit({ slug: slug.trim(), name: name.trim() });
      onCreated();
    } catch (err: unknown) {
      const anyErr = err as { response?: { data?: { detail?: string } }; message?: string };
      setError(anyErr.response?.data?.detail ?? anyErr.message ?? "Create failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <Label htmlFor="bu-slug">Slug (immutable)</Label>
        <Input
          id="bu-slug"
          value={slug}
          onChange={(e) => setSlug(e.target.value.toLowerCase())}
          placeholder="e.g. newbu"
          required
          pattern="[a-z0-9][a-z0-9-]{1,62}[a-z0-9]"
        />
        <p className="mt-1 text-[11px] text-brand-muted">
          Lowercase letters, digits, hyphens. Referenced by config keys — cannot
          be changed after creation.
        </p>
      </div>
      <div>
        <Label htmlFor="bu-name">Display name</Label>
        <Input
          id="bu-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. New BU"
          required
        />
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Creating…" : "Create"}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
