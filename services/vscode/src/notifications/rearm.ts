/** Pure state machine behind `rearm()` in extension.ts — extracted so the sign-out-races-prime
 *  guard is unit-testable without a vscode host. Primes once per `key()` (profile+BU), then starts
 *  the poll; a rearm superseded by a later one (sign-out, profile/BU switch, config change) while
 *  its prime() is still in flight must not go on to start() with now-stale state. */
export interface RearmDeps {
  /** A string identifying "which session" (e.g. `${profile}:${bu}`), or undefined when signed out. */
  key: () => string | undefined;
  prime: () => Promise<void>;
  start: () => void;
  stop: () => void;
}

export function createRearm(d: RearmDeps): () => Promise<void> {
  let primedFor: string | undefined;
  // Bumped on every call: a call still awaiting prime() when a later call starts (sign-out,
  // profile/BU switch) must not go on to start() the timer with stale state.
  let gen = 0;
  return async () => {
    const my = ++gen;
    const key = d.key();
    if (!key) { d.stop(); primedFor = undefined; return; }
    // A rapid BU switch can prime() twice in a row (once per call) before either reaches start();
    // harmless — prime() only records the current backlog as seen, it never notifies.
    if (primedFor !== key) { primedFor = key; await d.prime(); }
    // Re-check both: a later call superseding this one (gen), and a sign-out that landed mid-prime
    // without (yet) producing a new call at all (key back to undefined).
    if (my !== gen || !d.key()) return;
    d.start();
  };
}
