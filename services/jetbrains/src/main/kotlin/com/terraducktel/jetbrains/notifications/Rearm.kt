package com.terraducktel.jetbrains.notifications

import java.util.concurrent.atomic.AtomicInteger

/**
 * Blocking port of `services/vscode/src/notifications/rearm.ts`'s `createRearm()` — the
 * sign-out-races-prime guard, extracted so it's testable without a plugin host. Primes once per
 * `key()` (e.g. `"<profile>:<bu>"`), then starts the poll; a call superseded by a later one (sign-
 * out, profile/BU switch, config change) while its `prime()` is still in flight must not go on to
 * `start()` with now-stale state.
 */
class Rearm(
    private val key: () -> String?,
    private val prime: () -> Unit,
    private val start: () -> Unit,
    private val stop: () -> Unit,
) {
    @Volatile private var primedFor: String? = null

    // Bumped on every call: a call still blocked in prime() when a later call starts (sign-out,
    // profile/BU switch) must not go on to start() the timer with stale state.
    private val gen = AtomicInteger(0)

    /** Blocks the calling thread for the duration of `prime()` when the key has changed. Safe to
     *  call from any (non-EDT) thread. */
    fun invoke() {
        val my = gen.incrementAndGet()
        val k = key()
        if (k == null) {
            stop()
            primedFor = null
            return
        }
        // A rapid BU switch can prime() twice in a row (once per call) before either reaches
        // start(); harmless — prime() only records the current backlog as seen, it never notifies.
        // stop() FIRST: the previous key's timer is still armed, and a poll it fires during the
        // prime() below would race it and treat the new session's whole backlog as fresh.
        if (primedFor != k) {
            primedFor = k
            stop()
            prime()
        }
        // Re-check both: a later call superseding this one (gen), and a sign-out that landed
        // mid-prime without (yet) producing a new call at all (key back to null).
        if (my != gen.get() || key() == null) return
        start()
    }
}
