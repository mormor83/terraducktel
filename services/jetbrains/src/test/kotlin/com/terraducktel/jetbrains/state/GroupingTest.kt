package com.terraducktel.jetbrains.state

import com.terraducktel.jetbrains.api.Workspace
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Plain-JUnit port of `services/vscode/test/unit/grouping.test.ts` against
 * `services/vscode/src/state/grouping.ts`. [Grouping] has no platform imports, so this needs no
 * IntelliJ test fixture.
 */
class GroupingTest {

    private fun ws(
        name: String,
        awsAccountId: String? = "000000000000",
        region: String = "us-east-1",
        tfWorkingDir: String = "",
        azureSubscriptionId: String? = null,
        gcpProjectId: String? = null,
    ): Workspace = Workspace(
        id = name,
        business_unit_id = "bu",
        name = name,
        environment = "dev",
        aws_account_id = awsAccountId,
        region = region,
        tf_working_dir = tfWorkingDir,
        repo_ref = "main",
        kind = "terraform",
        tags = emptyMap(),
        drift_status = "unknown",
        path_status = "ok",
        azure_subscription_id = azureSubscriptionId,
        gcp_project_id = gcpProjectId,
        state_backend = "s3",
    )

    // ─── classify ────────────────────────────────────────────────────────────

    @Test fun `classify aws by account slash region`() {
        val w = ws(name = "vpc", awsAccountId = "123456789012", region = "eu-west-1", tfWorkingDir = "account-123456789012/eu-west-1/vpc")
        assertEquals(Classification(Cloud.AWS, "123456789012", "123456789012", "eu-west-1"), Grouping.classify(w))
    }

    @Test fun `classify azure by explicit link, region from path`() {
        val w = ws(
            name = "net", awsAccountId = "global", region = "global", azureSubscriptionId = "pk1",
            tfWorkingDir = "azure/subscription-11111111-1111-1111-1111-111111111111/westeurope/net",
        )
        val c = Grouping.classify(w)
        assertEquals(Cloud.AZURE, c.cloud)
        assertEquals("pk1", c.key)
        assertEquals("westeurope", c.region)
    }

    @Test fun `classify azure by path when unlinked`() {
        val w = ws(name = "net", awsAccountId = "global", region = "global", tfWorkingDir = "azure/subscription-abc/westeurope/net")
        val c = Grouping.classify(w)
        assertEquals(Cloud.AZURE, c.cloud)
        assertEquals("guid:abc", c.key)
        assertEquals("subscription-abc", c.label)
        assertEquals("westeurope", c.region)
    }

    @Test fun `classify gcp by path`() {
        val w = ws(name = "gke", awsAccountId = "global", region = "global", tfWorkingDir = "gcp/project-acme-prod-1234/us-central1/gke")
        val c = Grouping.classify(w)
        assertEquals(Cloud.GCP, c.cloud)
        assertEquals("pid:acme-prod-1234", c.key)
        assertEquals("us-central1", c.region)
    }

    @Test fun `classify other providers group under their top folder`() {
        val w = ws(name = "dns", awsAccountId = "global", region = "global", tfWorkingDir = "cloudflare/tenant-home/dns")
        val c = Grouping.classify(w)
        assertEquals(Cloud.OTHER, c.cloud)
        assertEquals("other:cloudflare", c.key)
        assertEquals("cloudflare", c.label)
        assertEquals("global", c.region)
    }

    // ─── workspacePathSegments ───────────────────────────────────────────────

    @Test fun `workspacePathSegments strips account and region, returns folders and leaf`() {
        val w = ws(name = "w", region = "eu-west-1", tfWorkingDir = "account-1/eu-west-1/cust01/worker")
        assertEquals(PathSegments(listOf("cust01"), "worker"), Grouping.workspacePathSegments(w))
    }

    @Test fun `workspacePathSegments strips azure and gcp prefixes`() {
        val azure = ws(name = "w", region = "global", tfWorkingDir = "azure/subscription-x/westeurope/team/net")
        assertEquals(PathSegments(listOf("team"), "net"), Grouping.workspacePathSegments(azure))

        val gcp = ws(name = "w", region = "global", tfWorkingDir = "gcp/project-p/us-central1/gke")
        assertEquals(PathSegments(emptyList(), "gke"), Grouping.workspacePathSegments(gcp))
    }

    @Test fun `workspacePathSegments falls back to the name when the path is empty`() {
        val w = ws(name = "manual", tfWorkingDir = ".")
        assertEquals(PathSegments(emptyList(), "manual"), Grouping.workspacePathSegments(w))
    }

    // ─── buildTree ───────────────────────────────────────────────────────────

    @Test fun `buildTree groups cloud region folders leaves, sorted`() {
        val tree = Grouping.buildTree(
            listOf(
                ws(name = "b", awsAccountId = "1", region = "r1", tfWorkingDir = "account-1/r1/b"),
                ws(name = "a", awsAccountId = "1", region = "r1", tfWorkingDir = "account-1/r1/team/a"),
                ws(name = "z", awsAccountId = "1", region = "r2", tfWorkingDir = "account-1/r2/z"),
                ws(name = "net", awsAccountId = "global", region = "global", tfWorkingDir = "azure/subscription-s/westeurope/net"),
            ),
        )
        assertEquals(listOf(Cloud.AWS to "1", Cloud.AZURE to "guid:s"), tree.map { it.cloud to it.key })

        val r1 = tree[0].regions.first { it.region == "r1" }
        assertEquals(listOf("team"), r1.root.folders.keys.toList())
        assertEquals(listOf("b"), r1.root.workspaces.map { it.second })
        assertEquals(listOf("a"), r1.root.folders.getValue("team").workspaces.map { it.second })
    }

    @Test fun `buildTree folds a bare workspace into a same-named folder`() {
        val tree = Grouping.buildTree(
            listOf(
                ws(name = "tools", awsAccountId = "1", region = "r", tfWorkingDir = "account-1/r/tools"),
                ws(name = "agent", awsAccountId = "1", region = "r", tfWorkingDir = "account-1/r/tools/agent"),
            ),
        )
        val root = tree[0].regions[0].root
        assertEquals(emptyList<Any>(), root.workspaces)
        assertEquals(listOf("agent", "tools"), root.folders.getValue("tools").workspaces.map { it.second }.sorted())
    }
}
