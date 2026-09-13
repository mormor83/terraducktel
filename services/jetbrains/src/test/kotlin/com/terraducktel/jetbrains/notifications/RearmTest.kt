package com.terraducktel.jetbrains.notifications

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Plain-JUnit port of every case in `services/vscode/test/unit/rearm.test.ts`: primes once per
 * key and starts, re-arming on the same key skips the re-prime, a sign-out (key -> null) stops and
 * forgets the primed key, and — the generation guard this class exists for — neither a sign-out
 * nor a later rearm (profile/BU switch) that lands while an earlier call's blocking `prime()` is
 * still running lets that earlier call go on to `start()` with now-stale state.
 */
class RearmTest {

    @Test
    fun `primes once per key and starts`() {
        val calls = mutableListOf<String>()
        var key: String? = "local:default"
        val rearm = Rearm(
            key = { key },
            prime = { calls += "prime" },
            start = { calls += "start" },
            stop = { calls += "stop" },
        )

        rearm.invoke()
        // stop() precedes prime(): the previous key's timer must not poll during the prime.
        assertEquals(listOf("stop", "prime", "start"), calls)

        calls.clear()
        rearm.invoke() // same key: no re-prime, still (re)starts
        assertEquals(listOf("start"), calls)
    }

    @Test
    fun `stops and forgets the primed key when signed out`() {
        val calls = mutableListOf<String>()
        var key: String? = "local:default"
        val rearm = Rearm(
            key = { key },
            prime = { calls += "prime" },
            start = { calls += "start" },
            stop = { calls += "stop" },
        )

        rearm.invoke()
        calls.clear()
        key = null
        rearm.invoke()
        assertEquals(listOf("stop"), calls)

        calls.clear()
        key = "local:default" // signing back in re-primes (primedFor was cleared)
        rearm.invoke()
        assertEquals(listOf("stop", "prime", "start"), calls)
    }

    @Test
    fun `does not start() when a sign-out lands while prime() is still in flight`() {
        val calls = mutableListOf<String>()
        val key = AtomicReference<String?>("local:default")
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val rearm = Rearm(
            key = { key.get() },
            prime = {
                synchronized(calls) { calls += "prime" }
                entered.countDown()
                assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
            },
            start = { synchronized(calls) { calls += "start" } },
            stop = { synchronized(calls) { calls += "stop" } },
        )

        val p1 = Thread { rearm.invoke() } // seq=1: enters prime(), then hangs on the gate
        p1.start()
        assertTrue(entered.await(5, TimeUnit.SECONDS)) // let it reach prime()
        key.set(null) // sign-out races the still-in-flight prime
        release.countDown()
        p1.join(5_000)
        assertFalse("p1 thread never finished", p1.isAlive)

        assertEquals(listOf("stop", "prime"), calls) // must NOT have start()ed with a signed-out session
    }

    @Test
    fun `does not start() when a later rearm (profile or BU switch) supersedes an in-flight one`() {
        val calls = mutableListOf<String>()
        val key = AtomicReference<String?>("a:default")
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val primeCall = AtomicInteger(0)
        val rearm = Rearm(
            key = { key.get() },
            prime = {
                val n = primeCall.incrementAndGet()
                synchronized(calls) { calls += "prime:${key.get()}" }
                if (n == 1) {
                    entered.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                }
            },
            start = { synchronized(calls) { calls += "start:${key.get()}" } },
            stop = { synchronized(calls) { calls += "stop" } },
        )

        val p1 = Thread { rearm.invoke() } // seq=1: primes for "a:default", hangs
        p1.start()
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        key.set("b:default") // profile switch before p1's prime() resolves
        val p2 = Thread { rearm.invoke() } // seq=2: primes for "b:default" (different key), runs to completion
        p2.start()
        p2.join(5_000)
        assertFalse("p2 thread never finished", p2.isAlive)

        assertEquals(listOf("stop", "prime:a:default", "stop", "prime:b:default", "start:b:default"), calls)

        release.countDown()
        p1.join(5_000) // p1 finally resolves, but must not start() again — it was superseded
        assertFalse("p1 thread never finished", p1.isAlive)
        assertEquals(listOf("stop", "prime:a:default", "stop", "prime:b:default", "start:b:default"), calls)
    }

    /** A single Settings > Apply fires BOTH `settingsChanged` and (via `TdtSession.reload()`'s own
     *  publish) `sessionChanged`, each dispatching its own `rearm()` onto a pooled thread with no
     *  ordering between them — the exact "two concurrent rearm() calls for the same key" shape a
     *  plain check-then-act on `primedFor` used to mishandle: T1 assigns `primedFor = k` and is
     *  descheduled BEFORE calling `stop()`; T2 (same key) then observes `primedFor == k` already,
     *  skips its own transition, and races straight to `start()` — arming a loop. T1 resumes,
     *  calls its OWN (now-stale) `stop()`, which kills the loop T2 just armed; T1 then finds
     *  itself superseded by the generation check and never calls `start()` again. Net result:
     *  nothing polling at all.
     *
     *  The fix bundles the `primedFor` assignment and `stop()` into one critical section, so this
     *  reproduces the bug shape by blocking THAT bundled operation — the `stop` callback, not
     *  `prime` (a prior version of this test gated on `prime()`, which runs *after* the assignment
     *  and `stop()` have already completed; by then the race window the fix closes is already
     *  shut, so that version passed against the pre-fix code too and proved nothing). T2 is
     *  released the moment it has merely been *started* (`Thread.start()`), never joined: joining
     *  it first would deadlock against the fix, where T2 blocks on [Rearm]'s lock until T1's own
     *  blocked `stop()` call — held below — returns.
     *
     *  Modeled here with a fake "loop" (a simple cancel flag) standing in for
     *  [com.terraducktel.jetbrains.notifications.ApprovalWatcher]'s real coroutine job: `start`
     *  records a new loop as current, `stop` cancels whatever is current. */
    @Test
    fun `two concurrent rearms for the same key never orphan the poll loop`() {
        class FakeLoop { val cancelled = java.util.concurrent.atomic.AtomicBoolean(false) }

        val key = "local:default"
        val current = AtomicReference<FakeLoop?>(null)
        val enteredStop = CountDownLatch(1)
        val release = CountDownLatch(1)
        val stopCalls = AtomicInteger(0)

        val rearm = Rearm(
            key = { key },
            prime = {},
            start = { current.set(FakeLoop()) },
            stop = {
                // The FIRST stop() call is T1's own — the initial (and, under the fix, the ONLY)
                // primedFor transition for this key — and it blocks here, standing in for the
                // window the fix now closes with a lock. Cancellation of whatever is CURRENTLY
                // armed happens only after the gate opens, so it observes whatever T2 did (or
                // didn't) manage to do while T1 was blocked.
                if (stopCalls.incrementAndGet() == 1) {
                    enteredStop.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                }
                current.get()?.cancelled?.set(true)
            },
        )

        val t1 = Thread { rearm.invoke() } // first-ever call for this key: transitions primedFor, blocks in stop()
        t1.start()
        assertTrue(enteredStop.await(5, TimeUnit.SECONDS))

        val t2 = Thread { rearm.invoke() } // second, concurrent call for the SAME key
        t2.start()
        // Bias scheduling so T2 — unsynchronized on the pre-fix code — reliably races all the way
        // to start() before T1's stop() resumes below; without this the discrimination is a coin
        // flip. Harmless on the fixed code, where T2 is blocked on Rearm's own lock regardless.
        Thread.sleep(100)
        release.countDown()

        t1.join(5_000)
        t2.join(5_000)
        assertFalse("t1 thread never finished", t1.isAlive)
        assertFalse("t2 thread never finished", t2.isAlive)

        val loop = current.get()
        assertTrue("exactly one loop must be armed after both rearms finish", loop != null)
        assertFalse(
            "the surviving loop must not have been cancelled by a stale stop() from a concurrent rearm() for the same key",
            loop!!.cancelled.get(),
        )
    }
}
