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
     *  plain check-then-act on `primedFor` used to mishandle: whichever call's `stop()` landed
     *  AFTER the other had already re-armed the loop would kill it, and the late `stop()`'s own
     *  call would then see itself superseded (by the generation check) and never `start()` again —
     *  net result, nothing polling at all. Modeled here with a fake "loop" (a simple cancel flag)
     *  standing in for [com.terraducktel.jetbrains.notifications.ApprovalWatcher]'s real coroutine
     *  job: `start` records a new loop as current, `stop` cancels whatever is current. */
    @Test
    fun `two concurrent rearms for the same key never orphan the poll loop`() {
        class FakeLoop { val cancelled = java.util.concurrent.atomic.AtomicBoolean(false) }

        val key = "local:default"
        val current = AtomicReference<FakeLoop?>(null)
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val primeCalls = AtomicInteger(0)

        val rearm = Rearm(
            key = { key },
            prime = {
                // Only the FIRST prime() (the one that actually transitioned primedFor) blocks —
                // it stands in for a slow network round trip racing a second, faster rearm() for
                // the very same key.
                if (primeCalls.incrementAndGet() == 1) {
                    entered.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                }
            },
            start = { current.set(FakeLoop()) },
            stop = { current.get()?.cancelled?.set(true) },
        )

        val t1 = Thread { rearm.invoke() } // enters prime() first and hangs on the gate
        t1.start()
        assertTrue(entered.await(5, TimeUnit.SECONDS))

        val t2 = Thread { rearm.invoke() } // same key: skips prime() (already primed), starts a loop
        t2.start()
        t2.join(5_000)
        assertFalse("t2 thread never finished", t2.isAlive)

        release.countDown()
        t1.join(5_000) // t1's prime() finally resolves; it must detect it was superseded and NOT start()
        assertFalse("t1 thread never finished", t1.isAlive)

        val loop = current.get()
        assertTrue("exactly one loop must be armed after both rearms finish", loop != null)
        assertFalse("the surviving loop must not have been cancelled by a stale stop()", loop!!.cancelled.get())
    }
}
