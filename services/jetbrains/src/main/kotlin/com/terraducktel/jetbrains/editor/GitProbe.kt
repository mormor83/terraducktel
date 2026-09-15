package com.terraducktel.jetbrains.editor

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/** Port of `services/vscode/src/editor/git.ts`. */
data class GitInfo(val root: String, val remoteUrl: String? = null, val branch: String? = null)

/** Runs `cmd args...` in `cwd`, returns stdout, and throws on a non-zero exit or a timeout. */
typealias ExecFn = (cmd: String, args: List<String>, cwd: String, timeoutMs: Long) -> String

/**
 * Cheap, cached, never-throwing view of the git checkout a file lives in.
 *
 * `info()` is a blocking call (it may shell out to `git`) — callers must invoke it off the EDT.
 * The caches are touched from multiple threads, so both maps and the "is this entry still fresh"
 * check are safe for concurrent use.
 */
class GitProbe(
    private val ttlMs: Long = 10_000,
    private val timeoutMs: Long = 3_000,
    private val exec: ExecFn = defaultExec,
) {
    private data class RootEntry(val at: Long, val root: String?)
    private data class InfoEntry(val at: Long, val info: GitInfo)

    private val rootByDir = ConcurrentHashMap<String, RootEntry>()
    private val infoByRoot = ConcurrentHashMap<String, InfoEntry>()

    /** Drops the cache entry for `root` (and every directory entry pointing at it), or everything when `root` is null. */
    fun invalidate(root: String? = null) {
        if (root == null) {
            infoByRoot.clear()
            rootByDir.clear()
            return
        }
        infoByRoot.remove(root)
        rootByDir.entries.removeIf { it.value.root == root }
    }

    /** Blocking — call off the EDT. Never throws; every failure resolves to null. */
    fun info(filePath: String): GitInfo? {
        val dir = File(filePath).parent ?: "."
        val now = System.currentTimeMillis() // captured once so the TTL check below is stable

        val cachedRoot = rootByDir[dir]
        val root: String? = if (cachedRoot != null && now - cachedRoot.at < ttlMs) {
            cachedRoot.root
        } else {
            val resolved = runCatching { exec("git", listOf("rev-parse", "--show-toplevel"), dir, timeoutMs) }
                .getOrNull()?.trim()?.takeIf { it.isNotEmpty() }
            rootByDir[dir] = RootEntry(now, resolved)
            resolved
        }
        if (root == null) return null

        val cachedInfo = infoByRoot[root]
        if (cachedInfo != null && now - cachedInfo.at < ttlMs) return cachedInfo.info

        val remoteUrl = runCatching { exec("git", listOf("remote", "get-url", "origin"), root, timeoutMs) }
            .getOrNull()?.trim()?.takeIf { it.isNotEmpty() }
        val branch = runCatching { exec("git", listOf("rev-parse", "--abbrev-ref", "HEAD"), root, timeoutMs) }
            .getOrNull()?.trim()?.takeIf { it.isNotEmpty() && it != "HEAD" }

        val info = GitInfo(root, remoteUrl, branch)
        infoByRoot[root] = InfoEntry(now, info)
        return info
    }

    companion object {
        val defaultExec: ExecFn = { cmd, args, cwd, timeout ->
            val process = ProcessBuilder(listOf(cmd) + args)
                .directory(File(cwd))
                .redirectErrorStream(false)
                .start()

            val stdout = ByteArrayOutputStream()
            val stderr = ByteArrayOutputStream()
            val stdoutReader = Thread { process.inputStream.copyTo(stdout) }.apply { isDaemon = true; start() }
            val stderrReader = Thread { process.errorStream.copyTo(stderr) }.apply { isDaemon = true; start() }

            val finished = process.waitFor(timeout, TimeUnit.MILLISECONDS)
            if (!finished) {
                process.destroyForcibly()
                stdoutReader.join(1_000)
                stderrReader.join(1_000)
                throw IOException("timed out after ${timeout}ms running $cmd ${args.joinToString(" ")} in $cwd")
            }
            stdoutReader.join()
            stderrReader.join()

            val exitCode = process.exitValue()
            if (exitCode != 0) {
                throw IOException(
                    "$cmd ${args.joinToString(" ")} exited $exitCode in $cwd: ${stderr.toString(Charsets.UTF_8)}",
                )
            }
            stdout.toString(Charsets.UTF_8)
        }
    }
}
