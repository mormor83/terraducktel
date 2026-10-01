package com.terraducktel.jetbrains.editor

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/**
 * Plain-JUnit port of `services/vscode/test/unit/git.test.ts` against
 * `services/vscode/src/editor/git.ts`. Uses a fake [ExecFn] that records calls instead of a real
 * git repository, so the suite is fast and hermetic (no `git` binary required).
 */
class GitProbeTest {

    private val root = "/repo/root"
    private val file = "$root/account-1/eu-west-1/vpc/main.tf"
    private val dir = "$root/account-1/eu-west-1/vpc"

    /** A scripted exec: `handler` maps (cmd, args, cwd) to a result-or-throw; every call is recorded. */
    private class FakeExec(private val handler: (args: List<String>, cwd: String) -> String) : ExecFn {
        val calls = mutableListOf<Pair<List<String>, String>>()
        override operator fun invoke(cmd: String, args: List<String>, cwd: String, timeoutMs: Long): String {
            calls += args to cwd
            return handler(args, cwd)
        }
    }

    private fun happyPathExec(branch: String = "feat/x", remote: String? = "git@github.com:acme/infra.git"): FakeExec =
        FakeExec { args, cwd ->
            when {
                args == listOf("rev-parse", "--show-toplevel") -> root
                args == listOf("remote", "get-url", "origin") -> remote ?: throw RuntimeException("no such remote 'origin'")
                args == listOf("rev-parse", "--abbrev-ref", "HEAD") -> branch
                else -> throw IllegalStateException("unexpected git ${args.joinToString(" ")} in $cwd")
            }
        }

    @Test fun `reports root, origin and branch for a file inside a checkout`() {
        val exec = happyPathExec()
        val info = GitProbe(exec = exec).info(file)
        assertEquals(root, info?.root)
        assertEquals("git@github.com:acme/infra.git", info?.remoteUrl)
        assertEquals("feat/x", info?.branch)
    }

    @Test fun `returns null outside any checkout and never throws`() {
        val exec = FakeExec { _, _ -> throw RuntimeException("not a git repository") }
        assertNull(GitProbe(exec = exec).info("/tmp/no-git/x.tf"))
    }

    @Test fun `caches the root per directory within the TTL`() {
        var calls = 0
        val inner = happyPathExec()
        val counting: ExecFn = { cmd, args, cwd, timeoutMs -> calls++; inner(cmd, args, cwd, timeoutMs) }
        val probe = GitProbe(ttlMs = 10_000, exec = counting)
        probe.info(file)
        val first = calls
        probe.info(file)
        assertEquals(first, calls) // second call hits both the per-dir root cache and the per-root info cache
    }

    @Test fun `invalidate drops both the root and info caches so exec runs again`() {
        var calls = 0
        val inner = happyPathExec()
        val counting: ExecFn = { cmd, args, cwd, timeoutMs -> calls++; inner(cmd, args, cwd, timeoutMs) }
        val probe = GitProbe(ttlMs = 10_000, exec = counting)
        probe.info(file)
        val first = calls
        probe.invalidate()
        probe.info(file)
        assertTrue(calls > first)
    }

    @Test fun `invalidate(root) drops only that root's cache entries`() {
        val callCount = AtomicInteger(0)
        val inner = happyPathExec()
        val counting: ExecFn = { cmd, args, cwd, timeoutMs -> callCount.incrementAndGet(); inner(cmd, args, cwd, timeoutMs) }
        val probe = GitProbe(ttlMs = 10_000, exec = counting)
        probe.info(file)
        val first = callCount.get()
        probe.invalidate(root)
        probe.info(file)
        assertTrue(callCount.get() > first)
    }

    @Test fun `treats a missing origin as remoteUrl null`() {
        val exec = happyPathExec(remote = null)
        val info = GitProbe(exec = exec).info(file)
        assertNull(info?.remoteUrl)
        assertEquals("feat/x", info?.branch)
    }

    @Test fun `treats a detached HEAD as branch null`() {
        val exec = happyPathExec(branch = "HEAD")
        val info = GitProbe(exec = exec).info(file)
        assertNull(info?.branch)
    }

    @Test fun `treats a failing show-toplevel as null info`() {
        val exec = FakeExec { args, _ ->
            if (args == listOf("rev-parse", "--show-toplevel")) throw RuntimeException("fatal: not a git repository")
            else throw IllegalStateException("should not reach ${args.joinToString(" ")}")
        }
        assertNull(GitProbe(exec = exec).info(file))
    }

    @Test fun `remote and branch are cached per root, not re-derived for a different file in the same checkout`() {
        val exec = happyPathExec()
        val probe = GitProbe(ttlMs = 10_000, exec = exec)
        probe.info(file)
        val callsAfterFirst = exec.calls.size
        val otherFile = "$dir/other.tf"
        probe.info(otherFile)
        // Same directory as `file`, so even the per-dir root lookup is cached; nothing new runs.
        assertEquals(callsAfterFirst, exec.calls.size)
    }

    @Test fun `defaultExec runs git and captures stdout, throwing on non-zero exit`() {
        // Exercise the real ProcessBuilder-backed exec end-to-end without needing a git checkout:
        // invoke a trivial external command via the same ExecFn contract.
        val out = GitProbe.defaultExec("sh", listOf("-c", "printf hello"), "/tmp", 3_000)
        assertEquals("hello", out)
        try {
            GitProbe.defaultExec("sh", listOf("-c", "exit 7"), "/tmp", 3_000)
            throw AssertionError("expected defaultExec to throw on non-zero exit")
        } catch (e: Exception) {
            // expected
        }
    }
}
