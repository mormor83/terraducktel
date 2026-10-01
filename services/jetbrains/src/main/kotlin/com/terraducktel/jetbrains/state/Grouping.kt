package com.terraducktel.jetbrains.state

import com.terraducktel.jetbrains.api.Workspace

/** Port of `services/vscode/src/state/grouping.ts` (itself a port of `services/ui/src/components/
 *  workspace-tree/paths.ts`). Pure Kotlin — no platform imports — so it can build the workspace
 *  tree shown by the plugin's tool window with plain-JUnit test coverage. */
enum class Cloud { AWS, AZURE, GCP, OTHER }

data class Classification(val cloud: Cloud, val key: String, val label: String, val region: String)
data class PathSegments(val folders: List<String>, val leaf: String)

/** `localeCompare`-equivalent ordering for names shown in the tree: case-insensitive first (so
 *  "AWS"/"aws" interleave the way they would under the default locale collation the TS side gets
 *  for free), natural comparison as the tiebreak so equal-ignoring-case names still sort
 *  deterministically instead of by insertion order. */
private val NAME_COMPARATOR: Comparator<String> = String.CASE_INSENSITIVE_ORDER.then(Comparator.naturalOrder())

class FolderNode(val name: String) {
    val folders = sortedMapOf<String, FolderNode>(NAME_COMPARATOR)
    val workspaces = mutableListOf<Pair<Workspace, String>>() // ws to leaf
}
data class RegionGroup(val region: String, val root: FolderNode, val count: Int)
data class CloudGroup(val cloud: Cloud, val key: String, val label: String, val regions: List<RegionGroup>, val count: Int)

private data class ProviderInfo(val id: String, val region: String)

private val SUBSCRIPTION_RE = Regex("^subscription-(.+)$")
private val PROJECT_RE = Regex("^project-(.+)$")
private val CLOUD_ORDER = listOf(Cloud.AWS, Cloud.AZURE, Cloud.GCP, Cloud.OTHER)

object Grouping {

    private fun parts(ws: Workspace): List<String> = ws.tf_working_dir.trim().split("/").filter { it.isNotEmpty() }

    private fun azureInfo(ws: Workspace): ProviderInfo? {
        val p = parts(ws)
        if (p.getOrNull(0)?.lowercase() != "azure") return null
        val m = SUBSCRIPTION_RE.find(p.getOrNull(1) ?: "") ?: return null
        return ProviderInfo(m.groupValues[1], p.getOrNull(2) ?: ws.region)
    }

    private fun gcpInfo(ws: Workspace): ProviderInfo? {
        val p = parts(ws)
        if (p.getOrNull(0)?.lowercase() != "gcp") return null
        val m = PROJECT_RE.find(p.getOrNull(1) ?: "") ?: return null
        return ProviderInfo(m.groupValues[1], p.getOrNull(2) ?: ws.region)
    }

    /** Which top-level group a workspace belongs to. Explicit links win over path detection;
     *  AWS is the default only when an account id is set; everything else groups by its top folder. */
    fun classify(ws: Workspace): Classification {
        if (!ws.azure_subscription_id.isNullOrEmpty()) {
            val i = azureInfo(ws)
            return Classification(Cloud.AZURE, ws.azure_subscription_id, i?.let { "subscription-${it.id}" } ?: "Azure subscription", i?.region ?: ws.region)
        }
        if (!ws.gcp_project_id.isNullOrEmpty()) {
            val i = gcpInfo(ws)
            return Classification(Cloud.GCP, ws.gcp_project_id, i?.let { "project-${it.id}" } ?: "GCP project", i?.region ?: ws.region)
        }
        azureInfo(ws)?.let { return Classification(Cloud.AZURE, "guid:${it.id}", "subscription-${it.id}", it.region) }
        gcpInfo(ws)?.let { return Classification(Cloud.GCP, "pid:${it.id}", "project-${it.id}", it.region) }
        if (!ws.aws_account_id.isNullOrEmpty() && ws.aws_account_id != "global") {
            return Classification(Cloud.AWS, ws.aws_account_id, ws.aws_account_id, ws.region)
        }
        val top = parts(ws).getOrNull(0) ?: "other"
        return Classification(Cloud.OTHER, "other:$top", top, ws.region.ifEmpty { "global" })
    }

    fun workspacePathSegments(ws: Workspace): PathSegments {
        val raw = ws.tf_working_dir.trim()
        if (raw.isEmpty() || raw == ".") return PathSegments(emptyList(), ws.name)
        val p = raw.split("/").filter { it.isNotEmpty() }.toMutableList()
        if (p.getOrNull(0)?.startsWith("account-") == true) p.removeAt(0)
        var regionToStrip = ws.region
        val head = p.getOrNull(0)?.lowercase()
        val second = p.getOrNull(1) ?: ""
        if ((head == "azure" && second.startsWith("subscription-")) || (head == "gcp" && second.startsWith("project-"))) {
            p.removeAt(0)
            p.removeAt(0)
            regionToStrip = p.getOrNull(0) ?: ws.region
        }
        if (p.getOrNull(0) == regionToStrip) p.removeAt(0)
        if (p.isEmpty()) return PathSegments(emptyList(), ws.name)
        val leaf = p.removeAt(p.size - 1)
        return PathSegments(p, leaf)
    }

    private fun buildFolderTree(workspaces: List<Workspace>): FolderNode {
        val root = FolderNode("")
        val items = workspaces.map { w -> val seg = workspacePathSegments(w); Triple(w, seg.folders, seg.leaf) }
        for ((_, folders, _) in items) {
            var cur = root
            for (name in folders) cur = cur.folders.getOrPut(name) { FolderNode(name) }
        }
        for ((w, folders, leaf) in items) {
            var cur = root
            for (name in folders) cur = cur.folders.getValue(name)
            // Folder-collision rule: a leaf whose name matches a sibling folder is attached to
            // that folder's own workspaces, not the parent's.
            val colliding = cur.folders[leaf]
            (colliding ?: cur).workspaces += w to leaf
        }
        fun sortNode(n: FolderNode) {
            n.workspaces.sortWith(compareBy(NAME_COMPARATOR) { it.second })
            n.folders.values.forEach(::sortNode)
        }
        sortNode(root) // folders are already sorted by name via the sortedMapOf backing map
        return root
    }

    fun countNode(n: FolderNode): Int {
        var c = n.workspaces.size
        for (f in n.folders.values) c += countNode(f)
        return c
    }

    private data class GroupAcc(val cls: Classification, val byRegion: LinkedHashMap<String, MutableList<Workspace>> = LinkedHashMap())

    fun buildTree(workspaces: List<Workspace>): List<CloudGroup> {
        val groups = LinkedHashMap<String, GroupAcc>()
        for (w in workspaces) {
            val cls = classify(w)
            val gk = "${cls.cloud}|${cls.key}"
            val g = groups.getOrPut(gk) { GroupAcc(cls) }
            g.byRegion.getOrPut(cls.region) { mutableListOf() } += w
        }
        return groups.values.map { (cls, byRegion) ->
            val regions = byRegion.entries.sortedWith(compareBy(NAME_COMPARATOR) { it.key }).map { (region, list) ->
                val root = buildFolderTree(list)
                RegionGroup(region, root, countNode(root))
            }
            CloudGroup(cls.cloud, cls.key, cls.label, regions, regions.sumOf { it.count })
        }.sortedWith(compareBy<CloudGroup> { CLOUD_ORDER.indexOf(it.cloud) }.then(compareBy(NAME_COMPARATOR) { it.label }))
    }
}
