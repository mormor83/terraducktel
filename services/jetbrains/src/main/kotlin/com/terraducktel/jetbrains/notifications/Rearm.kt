package com.terraducktel.jetbrains.notifications

import java.util.concurrent.atomic.AtomicInteger

/**
 * Blocking port of `services/vscode/src/notifications/rearm.ts`'s `createRearm()` — the
 * sign-out-races-prime guard, extracted so it's testable without a plugin host. Primes once per
 * `key()` (e.g. `"<profile>:<bu>"`), then starts the poll; a call superseded by a later one (sign-
 * out, profile/BU switch, config change) while its `prime()` is still in flight must not go on to
 * `start()` with now-stale state.
 *
 * The `primedFor` read-and-set and the `stop()`/`start()` bookkeeping around it are guarded by
 * [lock] so two concurrent [invoke] calls (e.g. a single Settings apply firing both
 * `settingsChanged` and `sessionChanged`) can't interleave the way a plain check-then-act on a
 * `@Volatile` field allowed: one call's `stop()` landing *after* another call already armed the
 * loop for the same key used to silently orphan it (kill the just-started loop, then bail out on
 * the generation check without re-starting it — nothing left polling). `prime()` itself runs
 * OUTSIDE [lock] — it's a network call and must stay allowed to run concurrently with another
 * call's [lock]-guarded transition for a *different* key (see the "later rearm supersedes an
 * in-flight one" test below); only the bookkeeping around it is serialized.
 */
class Rearm(
    private val key: () -> String?,
    private val prime: () -> Unit,
    private val start: () -> Unit,
    private val stop: () -> Unit,
) {
    private val lock = Any()
    private var primedFor: String? = null

    // Bumped on every call: a call still blocked in prime() when a later call starts (sign-out,
    // profile/BU switch) must not go on to start() the timer with stale state.
    private val gen = AtomicInteger(0)

    /** Blocks the calling thread for the duration of `prime()` when the key has changed. Safe to
     *  call concurrently from any (non-EDT) thread(s): the `primedFor`/`stop`/`start` bookkeeping
     *  is serialized on [lock], so two racing calls can't orphan the poll loop or leave it
     *  unarmed. `prime()` itself is called outside the lock (it's the slow, blocking part), so a
     *  call for one key never blocks a concurrent call for a different key on `prime()` alone. */
    fun invoke() {
        val my = gen.incrementAndGet()
        val k = key()
        if (k == null) {
            synchronized(lock) {
                stop()
                primedFor = null
            }
            return
        }
        // A rapid BU switch can prime() twice in a row (once per call) before either reaches
        // start(); harmless — prime() only records the current backlog as seen, it never notifies.
        // stop() FIRST: the previous key's timer is still armed, and a poll it fires during the
        // prime() below would race it and treat the new session's whole backlog as fresh.
        //
        // primedFor's read-and-set is bundled with stop() in the SAME synchronized block so a
        // second call for the same key can never observe `primedFor == k` before this call's
        // stop() has actually run — closing the exact window that used to let a late stop() kill
        // a loop a concurrent call just armed.
        var justTransitioned = false
        synchronized(lock) {
            if (primedFor != k) {
                primedFor = k
                stop()
                justTransitioned = true
            }
        }
        if (justTransitioned) prime()
        // Re-check both: a later call superseding this one (gen), and a sign-out that landed
        // mid-prime without (yet) producing a new call at all (key back to null). Bundled with
        // start() under lock so this call's "am I still the latest?" check and its start() are
        // atomic with respect to any other call's stop()/start().
        synchronized(lock) {
            if (my != gen.get() || key() == null) return
            start()
        }
    }
}
