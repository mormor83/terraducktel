package com.terraducktel.jetbrains.editor

import com.terraducktel.jetbrains.api.Workspace
import java.net.URI
import java.net.URISyntaxException
import java.nio.file.Paths

/**
 * Port of `services/vscode/src/editor/mapping.ts` — pure Kotlin, no platform imports, so it can
 * decide which workspace a file on disk belongs to with plain-JUnit test coverage.
 */
data class Match(val ws: Workspace, val exact: Boolean)

private val SCP_LIKE = Regex("""^(?:[\w.-]+@)?((?:[\w.-]{2,}|[\w.-]*\.[\w.-]*)):(?!//)(\S+)$""")
private val TRAILING_SLASHES = Regex("/+$")
private val LEADING_SLASHES = Regex("^/+")
private val DOT_GIT_SUFFIX = Regex("\\.git$", RegexOption.IGNORE_CASE)
private val LEADING_DOT_SLASHES = Regex("^(?:\\./)+")
private val LEADING_OR_TRAILING_SLASHES = Regex("^/+|/+$")

object Mapping {

    /** Canonical "host/path" form for comparing git remotes across schemes. `local://` keeps its path. */
    fun normalizeRepoUrl(url: String?): String? {
        if (url == null) return null
        var u = url.trim()
        if (u.isEmpty()) return null
        if (u.startsWith("local://")) {
            val p = u.substring("local://".length).replace(TRAILING_SLASHES, "")
            return if (p.isNotEmpty()) "local:$p" else null
        }
        // scp-like: git@host:org/repo(.git); guard against Windows drive paths (C:\ or c:/) — the
        // host group requires 2+ chars or a dot, so a bare drive letter never matches.
        val scp = SCP_LIKE.find(u)
        if (scp != null) {
            u = "ssh://${scp.groupValues[1]}/${scp.groupValues[2]}"
        }
        val host: String
        val path: String
        try {
            val parsed = URI(u)
            val scheme = parsed.scheme?.lowercase()
            val hostname = parsed.host
            if (hostname.isNullOrEmpty()) return null
            val port = parsed.port
            // ssh's default port (22) is implicit in the scp-like form (git@host:org/repo), so an
            // explicit ssh://host:22/... must normalize the same way, not compare unequal on the port.
            host = if (scheme == "ssh" && port == 22) hostname.lowercase()
            else (if (port != -1) "$hostname:$port" else hostname).lowercase()
            path = (parsed.rawPath ?: "").lowercase()
        } catch (e: URISyntaxException) {
            return null
        }
        if (host.isEmpty()) return null
        var p = path.replace(TRAILING_SLASHES, "")
        p = p.replace(DOT_GIT_SUFFIX, "")
        p = p.replace(LEADING_SLASHES, "")
        if (p.isEmpty()) return null
        return "$host/$p"
    }

    /** Directory of `filePath` relative to `gitRoot`, posix-separated; "" at the root; null when outside. */
    fun relativeDir(gitRoot: String, filePath: String): String? {
        val parent = Paths.get(filePath).parent ?: return null
        val rel = try {
            Paths.get(gitRoot).relativize(parent)
        } catch (e: IllegalArgumentException) {
            return null // different roots (e.g. different drives on Windows)
        }
        val relStr = rel.toString()
        if (relStr.isEmpty()) return ""
        // Only a leading ".." element means "outside the root" — a directory literally named
        // "..foo" also starts with "..", but is a normal child, not an escape.
        if (rel.nameCount > 0 && rel.getName(0).toString() == "..") return null
        return relStr.replace(java.io.File.separatorChar, '/')
    }

    private fun isPrefix(dir: String, wd: String): Boolean =
        dir == wd || dir.startsWith(wd.replace(TRAILING_SLASHES, "") + "/")

    private fun normalizeWd(wd: String?): String =
        (wd ?: "").replace(LEADING_DOT_SLASHES, "").replace(LEADING_OR_TRAILING_SLASHES, "")

    /** Longest `tf_working_dir` prefix among workspaces whose repo matches. */
    fun matchWorkspace(workspaces: List<Workspace>, relativeDir: String, remoteUrl: String?): Match? {
        val remote = normalizeRepoUrl(remoteUrl)
        val candidates = workspaces.filter { w ->
            val wd = normalizeWd(w.tf_working_dir)
            if (wd.isEmpty() || wd == "." || !isPrefix(relativeDir, wd)) return@filter false
            val wsRepo = normalizeRepoUrl(w.repo_url)
            when {
                wsRepo?.startsWith("local:") == true -> true // local checkouts match by path alone
                remote != null -> wsRepo == remote // known remote: must match
                else -> true // unknown remote: path only, resolved below
            }
        }
        if (candidates.isEmpty()) return null
        val longest = candidates.maxOf { normalizeWd(it.tf_working_dir).length }
        val best = candidates.filter { normalizeWd(it.tf_working_dir).length == longest }
        if (best.size != 1) return null // ambiguous (typically unknown remote + same path in two repos)
        val ws = best[0]
        return Match(ws, normalizeWd(ws.tf_working_dir) == relativeDir)
    }
}
