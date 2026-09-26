package com.terraducktel.jetbrains.editor

import com.terraducktel.jetbrains.api.Workspace
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Paths

/**
 * Plain-JUnit port of `services/vscode/test/unit/mapping.test.ts` against
 * `services/vscode/src/editor/mapping.ts`. [Mapping] has no platform imports, so this needs no
 * IntelliJ test fixture.
 */
class MappingTest {

    private fun ws(
        name: String,
        tfWorkingDir: String,
        repoUrl: String? = "https://github.com/acme/infra.git",
    ): Workspace = Workspace(
        id = name,
        business_unit_id = "bu",
        name = name,
        environment = "dev",
        region = "us-east-1",
        aws_account_id = "1",
        repo_ref = "main",
        kind = "terraform",
        tags = emptyMap(),
        drift_status = "unknown",
        path_status = "ok",
        state_backend = "s3",
        repo_url = repoUrl,
        tf_working_dir = tfWorkingDir,
    )

    // ─── normalizeRepoUrl ────────────────────────────────────────────────────

    @Test fun `normalizeRepoUrl table`() {
        val cases = listOf(
            "https://github.com/Acme/Infra.git" to "github.com/acme/infra",
            "https://github.com/acme/infra" to "github.com/acme/infra",
            "git@github.com:acme/infra.git" to "github.com/acme/infra",
            "ssh://git@github.com/acme/infra.git" to "github.com/acme/infra",
            "http://forgejo:3002/infra/live/" to "forgejo:3002/infra/live",
            "https://user:tok@gitlab.example.com/grp/sub/repo.git" to "gitlab.example.com/grp/sub/repo",
            "local:///mnt/local-repos/probe" to "local:/mnt/local-repos/probe",
            "git@forgejo.internal:/repos/infra.git" to "forgejo.internal/repos/infra",
        )
        for ((input, want) in cases) {
            assertEquals("input=$input", want, Mapping.normalizeRepoUrl(input))
        }
    }

    @Test fun `normalizeRepoUrl returns null for empty, null or garbage`() {
        assertNull(Mapping.normalizeRepoUrl(""))
        assertNull(Mapping.normalizeRepoUrl(null))
        assertNull(Mapping.normalizeRepoUrl("not a url"))
    }

    @Test fun `normalizeRepoUrl guards Windows drive paths from scp-URL parsing`() {
        assertNull(Mapping.normalizeRepoUrl("C:\\Users\\x\\repo"))
        assertNull(Mapping.normalizeRepoUrl("c:/x/repo"))
        assertEquals("github.com/acme/infra", Mapping.normalizeRepoUrl("git@github.com:acme/infra.git"))
    }

    @Test fun `normalizeRepoUrl strips ssh's implicit default port so it compares equal to the scp-like form`() {
        assertEquals(Mapping.normalizeRepoUrl("git@host:o/r"), Mapping.normalizeRepoUrl("ssh://git@host:22/o/r"))
        assertTrue(Mapping.normalizeRepoUrl("ssh://git@host:2222/o/r") != Mapping.normalizeRepoUrl("git@host:o/r"))
    }

    // ─── relativeDir ─────────────────────────────────────────────────────────

    @Test fun `relativeDir returns the posix directory relative to the root`() {
        assertEquals("account-1/eu-west-1/vpc", Mapping.relativeDir("/home/u/infra", "/home/u/infra/account-1/eu-west-1/vpc/main.tf"))
        assertEquals("", Mapping.relativeDir("/home/u/infra", "/home/u/infra/main.tf"))
    }

    @Test fun `relativeDir returns null for files outside the root`() {
        assertNull(Mapping.relativeDir("/home/u/infra", "/home/u/other/main.tf"))
        assertNull(Mapping.relativeDir("/home/u/infra", "/home/u/infra2/main.tf"))
    }

    @Test fun `relativeDir accepts a directory literally named dotdotfoo rather than treating it as outside the root`() {
        assertEquals("..foo", Mapping.relativeDir("/home/u/infra", "/home/u/infra/..foo/main.tf"))
        assertEquals("..foo/bar", Mapping.relativeDir("/home/u/infra", "/home/u/infra/..foo/bar/main.tf"))
    }

    @Test fun `relativeDir resolves through a symlinked checkout when both sides are realpath'd`() {
        // git rev-parse --show-toplevel always returns the realpath. A caller that probes with the
        // realpath but maps against the unresolved (symlinked) editor path would get a root that
        // isn't a prefix of the file path; the fix is to realpath the file first and use that
        // resolved path for both GitProbe.info() and relativeDir().
        val root = Files.createTempDirectory("tdt-relroot-")
        val linkParent = Files.createTempDirectory("tdt-rellink-")
        try {
            val nested = root.resolve("account-1/eu-west-1/vpc")
            Files.createDirectories(nested)
            val file = nested.resolve("main.tf")
            Files.writeString(file, "# tf\n")

            val link = linkParent.resolve("checkout")
            Files.createSymbolicLink(link, root)
            val linkedFile = link.resolve("account-1/eu-west-1/vpc/main.tf")

            val realRoot = root.toRealPath()
            val realLinkedFile = linkedFile.toRealPath()
            assertEquals("account-1/eu-west-1/vpc", Mapping.relativeDir(realRoot.toString(), realLinkedFile.toString()))
            // Using the UNRESOLVED editor path against the resolved root reproduces the bug: no prefix relation.
            assertNull(Mapping.relativeDir(realRoot.toString(), linkedFile.toString()))
        } finally {
            linkParent.toFile().deleteRecursively()
            root.toFile().deleteRecursively()
        }
    }

    // ─── matchWorkspace ──────────────────────────────────────────────────────

    private val list = listOf(
        ws(name = "vpc", tfWorkingDir = "account-1/eu-west-1/vpc"),
        ws(name = "vpc-peering", tfWorkingDir = "account-1/eu-west-1/vpc/peering"),
        ws(name = "other-repo", tfWorkingDir = "account-1/eu-west-1/vpc", repoUrl = "https://github.com/acme/other.git"),
        ws(name = "local", tfWorkingDir = "proxmox/cluster-home/pve/probe", repoUrl = "local:///mnt/local-repos/probe"),
    )

    @Test fun `matchWorkspace picks the longest tf_working_dir prefix within the matching repo`() {
        val m1 = Mapping.matchWorkspace(list, "account-1/eu-west-1/vpc/peering/modules", "git@github.com:acme/infra.git")
        assertEquals("vpc-peering", m1?.ws?.name)

        val m2 = Mapping.matchWorkspace(list, "account-1/eu-west-1/vpc", "https://github.com/acme/infra")
        assertEquals("vpc", m2?.ws?.name)
        assertEquals(true, m2?.exact)
    }

    @Test fun `matchWorkspace excludes workspaces from a different repo`() {
        val m = Mapping.matchWorkspace(list, "account-1/eu-west-1/vpc", "https://github.com/acme/other.git")
        assertEquals("other-repo", m?.ws?.name)
    }

    @Test fun `matchWorkspace matches local checkouts by path alone`() {
        assertEquals("local", Mapping.matchWorkspace(list, "proxmox/cluster-home/pve/probe", null)?.ws?.name)
        assertEquals(
            "local",
            Mapping.matchWorkspace(list, "proxmox/cluster-home/pve/probe", "https://github.com/acme/infra.git")?.ws?.name,
        )
    }

    @Test fun `matchWorkspace with an unknown remote falls back to a path match only when unique`() {
        assertEquals("vpc-peering", Mapping.matchWorkspace(list, "account-1/eu-west-1/vpc/peering", null)?.ws?.name)
        assertNull(Mapping.matchWorkspace(list, "account-1/eu-west-1/vpc", null))
    }

    @Test fun `matchWorkspace does not match a parent directory or an unrelated path`() {
        assertNull(Mapping.matchWorkspace(list, "account-1/eu-west-1", "https://github.com/acme/infra.git"))
        assertNull(Mapping.matchWorkspace(list, "account-1/eu-west-1/vpcx", "https://github.com/acme/infra.git"))
    }

    @Test fun `matchWorkspace strips a leading dot slash from tf_working_dir before matching`() {
        val withDotSlash = listOf(ws(name = "vpc", tfWorkingDir = "./account-1/eu-west-1/vpc"))
        val m = Mapping.matchWorkspace(withDotSlash, "account-1/eu-west-1/vpc", "https://github.com/acme/infra.git")
        assertEquals("vpc", m?.ws?.name)
        assertEquals(true, m?.exact)
    }

    @Test fun `matchWorkspace never matches a workspace whose tf_working_dir is the repo root`() {
        val rootWs = listOf(ws(name = "root-ws", tfWorkingDir = "."))
        assertNull(Mapping.matchWorkspace(rootWs, "", "https://github.com/acme/infra.git"))
        assertNull(Mapping.matchWorkspace(rootWs, "account-1/eu-west-1/vpc", "https://github.com/acme/infra.git"))
    }
}
