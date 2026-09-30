// Settings → GitHub → Git write access (environment promotions).
//
// Per Business Unit and off by default: a promotion commits to the target
// stack's pinned branch, so a BU has to opt in explicitly. Only this BU's own
// credentials are used — a dedicated write token, or else the BU's GitHub
// token — never a global one. The token itself never comes back from the API
// (masked tail only). Editing is BU-admin only; everyone else sees the state.

import { useCallback, useEffect, useState } from "react";

import {
  extractError,
  getGitWrite,
  putGitWrite,
  testGitWrite,
  type GitWriteState,
} from "../../api/envLinks";
import { useBusinessUnitSelection } from "../../hooks/useBusinessUnit";
import { useCurrentUser } from "../../hooks/useAuth";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Input, Label } from "../ui";

type TestResult = Awaited<ReturnType<typeof testGitWrite>>;

export default function GitWriteSection() {
  const [buSlug] = useBusinessUnitSelection();
  // Mirrors the API's require_bu_admin (superadmin until per-BU admin lands);
  // pages/Environments.tsx has the same check as useIsBuAdmin.
  const isBuAdmin = !!useCurrentUser()?.is_superadmin;
  const [state, setState] = useState<GitWriteState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [botName, setBotName] = useState("");
  const [botEmail, setBotEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);

  const apply = (s: GitWriteState) => {
    setState(s);
    setUsername(s.username ?? "");
    setBotName(s.bot_name ?? "");
    setBotEmail(s.bot_email ?? "");
  };

  const load = useCallback(async () => {
    try {
      apply(await getGitWrite());
      setError(null);
    } catch (e) {
      setError(extractError(e, "Failed to load git write settings"));
    }
  }, []);

  useEffect(() => {
    if (buSlug) void load();
  }, [buSlug, load]);

  const save = async (body: Parameters<typeof putGitWrite>[0]) => {
    setSaving(true);
    setError(null);
    setTest(null);
    try {
      apply(await putGitWrite(body));
      setToken("");
    } catch (e) {
      setError(extractError(e, "Save failed"));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setError(null);
    try {
      setTest(await testGitWrite());
    } catch (e) {
      setError(extractError(e, "Test failed"));
    } finally {
      setTesting(false);
    }
  };

  const readOnly = !isBuAdmin;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Git write access (environment promotions)</CardTitle>
        {state && (
          <Badge tone={state.enabled ? "success" : "neutral"}>{state.enabled ? "enabled" : "disabled"}</Badge>
        )}
      </CardHeader>
      <CardBody>
        <p className="mb-4 max-w-3xl text-[13px] text-brand-muted">
          Environment promotions commit to the target stack's pinned branch and then run the normal
          plan → policy → approval pipeline. Off by default. Only this Business Unit's own credentials
          are used — a dedicated write token below, or else this BU's GitHub token — never a global token.
        </p>

        {!buSlug ? (
          <p className="text-[13px] text-brand-muted">Select a specific Business Unit in the top bar to manage git write access.</p>
        ) : !state ? (
          error ? <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p> : <p className="text-[13px] text-brand-muted">Loading…</p>
        ) : (
          <div className="space-y-5">
            <label className="flex items-center gap-2 text-[13px] text-brand-text">
              <input
                type="checkbox"
                checked={state.enabled}
                disabled={readOnly || saving}
                onChange={(e) => save({ enabled: e.target.checked })}
                aria-label="Allow promotions to push commits"
              />
              Allow environment promotions to push commits for this Business Unit
            </label>

            <div className="text-[13px] text-brand-textSoft">
              {state.token_configured ? (
                <p>
                  Write credential:{" "}
                  <span className="font-medium text-brand-text">
                    {state.token_source === "dedicated" ? "dedicated token" : "using this BU's GitHub token"}
                  </span>{" "}
                  {state.token_tail && <span className="font-mono text-brand-muted">{state.token_tail}</span>}
                </p>
              ) : (
                <p className="text-[var(--td-warn-ink)]">No write credential — set a dedicated token, or a GitHub token for this BU.</p>
              )}
            </div>

            {!readOnly && (
              <div className="grid max-w-3xl grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <Label htmlFor="git-write-token">Dedicated write token (optional)</Label>
                  <div className="flex gap-2">
                    <Input id="git-write-token" type="password" autoComplete="off" value={token}
                      onChange={(e) => setToken(e.target.value)} placeholder="Leave empty to keep the current one" />
                    <Button disabled={!token || saving} onClick={() => save({ token })}>Save token</Button>
                  </div>
                </div>
                <div>
                  <Label htmlFor="git-write-username">Username (optional)</Label>
                  <Input id="git-write-username" value={username} onChange={(e) => setUsername(e.target.value)}
                    placeholder="x-access-token" />
                </div>
                <div />
                <div>
                  <Label htmlFor="git-write-bot-name">Committer name</Label>
                  <Input id="git-write-bot-name" value={botName} onChange={(e) => setBotName(e.target.value)} />
                </div>
                <div>
                  <Label htmlFor="git-write-bot-email">Committer email</Label>
                  <Input id="git-write-bot-email" value={botEmail} onChange={(e) => setBotEmail(e.target.value)} />
                </div>
                <div className="flex flex-wrap gap-2 sm:col-span-2">
                  <Button variant="secondary" disabled={saving}
                    onClick={() => save({ username, bot_name: botName, bot_email: botEmail })}>
                    Save identity
                  </Button>
                  {state.token_source === "dedicated" && (
                    <Button variant="danger" disabled={saving} onClick={() => save({ clear_token: true })}>
                      Remove dedicated token
                    </Button>
                  )}
                </div>
              </div>
            )}

            <div>
              <Button variant="secondary" onClick={runTest} disabled={testing}>
                {testing ? "Testing…" : "Test push access"}
              </Button>
              {test && (
                <ul className="mt-3 space-y-1">
                  {test.repos.length === 0 && (
                    <li className="text-[12.5px] text-brand-muted">No git-synced stacks in this BU.</li>
                  )}
                  {test.repos.map((r) => (
                    <li key={r.repo_url} className="flex items-start gap-2 text-[12.5px]">
                      <span className={r.can_push ? "text-[var(--td-green-ink)]" : "text-[var(--td-err-ink)]"}>
                        {r.can_push ? "✓" : "✗"}
                      </span>
                      <span className="font-mono text-brand-text">{r.repo_url}</span>
                      {r.detail && <span className="text-brand-muted">— {r.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {error && <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
