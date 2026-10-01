// Settings → State store: key pair for the fallback S3 state bucket.
//
// The fallback bucket holds state for every workspace without a linked AWS
// account (Azure / GCP / Proxmox …). Its endpoint is the S3_ENDPOINT_URL env
// var (not secret, shown read-only here); the key pair is a secret and lives
// encrypted in the config table. The API only ever returns `configured` and a
// masked tail of each half. Platform-global, not per Business Unit — admins
// can see it, only a superadmin can change it.

import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import { extractError } from "../../api/envLinks";
import { useCurrentUser } from "../../hooks/useAuth";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Input, Label } from "../ui";

export type StateStoreStatus = {
  configured: boolean;
  partial: boolean;
  access_key_id_tail: string | null;
  secret_access_key_tail: string | null;
  bucket: string;
  endpoint_url: string | null;
  use_localstack: boolean;
  insecure_endpoint: boolean;
  require_tls: boolean;
};

const URL = "/v1/integrations/state-store";

function endpointLabel(s: StateStoreStatus): string {
  if (s.endpoint_url) return s.endpoint_url;
  return s.use_localstack ? "LocalStack (bundled)" : "AWS S3 (default)";
}

export default function StateStoreSection() {
  const isSuperadmin = !!useCurrentUser()?.is_superadmin;
  const [state, setState] = useState<StateStoreStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setState((await api.get<StateStoreStatus>(URL)).data);
      setError(null);
    } catch (e) {
      setError(extractError(e, "Failed to load state store settings"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await api.put<StateStoreStatus>(URL, {
        access_key_id: accessKeyId.trim(),
        secret_access_key: secretAccessKey.trim(),
      });
      setState(res.data);
      setAccessKeyId("");
      setSecretAccessKey("");
    } catch (e) {
      setError(extractError(e, "Save failed"));
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.delete(URL);
      await load();
    } catch (e) {
      setError(extractError(e, "Remove failed"));
    } finally {
      setSaving(false);
    }
  };

  const setRequireTls = async (value: boolean) => {
    setSaving(true);
    setError(null);
    try {
      setState((await api.put<StateStoreStatus>(URL, { require_tls: value })).data);
    } catch (e) {
      setError(extractError(e, "Save failed"));
    } finally {
      setSaving(false);
    }
  };

  const canSave = !!accessKeyId.trim() && !!secretAccessKey.trim() && !saving;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Fallback state store (S3)</CardTitle>
        {state && (
          <Badge tone={state.configured ? "success" : state.partial ? "danger" : "neutral"}>
            {state.configured ? "configured" : state.partial ? "incomplete" : "not configured"}
          </Badge>
        )}
      </CardHeader>
      <CardBody>
        <p className="mb-4 max-w-3xl text-[13px] text-brand-muted">
          Terraform state for every workspace without a linked AWS account is kept in this bucket. The
          endpoint and bucket come from the API's environment (<code>S3_ENDPOINT_URL</code>,{" "}
          <code>S3_STATE_BUCKET</code>); the key pair is stored encrypted and never shown again after
          saving. Platform-wide — applies to every Business Unit. Changes apply within about a minute.
        </p>

        {!state ? (
          error ? (
            <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>
          ) : (
            <p className="text-[13px] text-brand-muted">Loading…</p>
          )
        ) : (
          <div className="space-y-5">
            <dl className="grid max-w-3xl grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[13px]">
              <dt className="text-brand-muted">Endpoint</dt>
              <dd className="font-mono text-brand-text">{endpointLabel(state)}</dd>
              <dt className="text-brand-muted">Bucket</dt>
              <dd className="font-mono text-brand-text">{state.bucket}</dd>
              <dt className="text-brand-muted">Access key ID</dt>
              <dd className="font-mono text-brand-text">{state.access_key_id_tail ?? "—"}</dd>
              <dt className="text-brand-muted">Secret access key</dt>
              <dd className="font-mono text-brand-text">{state.secret_access_key_tail ?? "—"}</dd>
            </dl>

            {state.insecure_endpoint && (
              <p className="max-w-3xl text-[13px] text-[var(--td-warn-ink)]">
                The endpoint uses plaintext http:// to a non-local host: state (which can contain secrets)
                travels unencrypted. Switch S3_ENDPOINT_URL to https://.
              </p>
            )}
            <label className="flex max-w-3xl items-start gap-2 text-[13px] text-brand-text">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!!state.require_tls}
                disabled={!isSuperadmin || saving}
                onChange={(e) => void setRequireTls(e.target.checked)}
              />
              <span>
                Require TLS
                <span className="block text-brand-muted">
                  Refuse (503) to read or write state while the endpoint is unencrypted and not on a
                  local host, instead of only logging a warning.
                </span>
              </span>
            </label>
            {state.partial && (
              <p className="max-w-3xl text-[13px] text-[var(--td-err-ink)]">
                Only one half of the key pair is stored — state requests for non-AWS workspaces fail until
                both are set (or both removed).
              </p>
            )}
            {!state.configured && !state.partial && (
              <p className="max-w-3xl text-[13px] text-brand-muted">
                No key pair stored: the API uses its default AWS credential chain
                {state.use_localstack && !state.endpoint_url ? " (LocalStack's test credentials)" : ""}.
              </p>
            )}

            {isSuperadmin ? (
              <div className="grid max-w-3xl grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="state-store-ak">Access key ID</Label>
                  <Input id="state-store-ak" autoComplete="off" value={accessKeyId}
                    onChange={(e) => setAccessKeyId(e.target.value)}
                    placeholder={state.configured ? "Enter both halves to replace" : ""} />
                </div>
                <div>
                  <Label htmlFor="state-store-sk">Secret access key</Label>
                  <Input id="state-store-sk" type="password" autoComplete="off" value={secretAccessKey}
                    onChange={(e) => setSecretAccessKey(e.target.value)}
                    placeholder={state.configured ? "Enter both halves to replace" : ""} />
                </div>
                <div className="flex flex-wrap gap-2 sm:col-span-2">
                  <Button disabled={!canSave} onClick={save}>Save key pair</Button>
                  {(state.configured || state.partial) && (
                    <Button variant="danger" disabled={saving} onClick={clear}>Remove key pair</Button>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-[13px] text-brand-muted">Only a superadmin can change the state store credentials.</p>
            )}

            {error && <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
