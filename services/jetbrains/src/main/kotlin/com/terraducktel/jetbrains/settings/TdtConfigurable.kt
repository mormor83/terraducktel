package com.terraducktel.jetbrains.settings

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.options.BoundConfigurable
import com.intellij.openapi.ui.ComboBox
import com.intellij.openapi.ui.DialogPanel
import com.intellij.ui.ToolbarDecorator
import com.intellij.ui.dsl.builder.Align
import com.intellij.ui.dsl.builder.bindIntText
import com.intellij.ui.dsl.builder.bindSelected
import com.intellij.ui.dsl.builder.panel
import com.intellij.ui.components.JBTextField
import com.intellij.ui.table.TableView
import com.intellij.util.xmlb.XmlSerializerUtil
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import java.util.IdentityHashMap
import java.util.concurrent.Future
import javax.swing.DefaultComboBoxModel

/**
 * Settings > Tools > Terraducktel. A profiles table (name / API URL / UI URL / insecure TLS) plus
 * general preferences.
 *
 * The scalar fields (refresh interval, runs limit, approval poll seconds, the two checkboxes) are
 * bound via the Kotlin UI DSL directly to [working]'s properties, so [DialogPanel.isModified] /
 * `apply` / `reset` (invoked via `super`) handle them for free. The profiles table and the active
 * profile combo are NOT DSL-bound components, so [isModified], [apply] and [reset] are overridden
 * to additionally compare/copy them against [working] — a deep copy of [TdtSettings]'s state that
 * this configurable treats as its "last known committed" baseline, refreshed in [reset] and after
 * a successful [apply]. [working]'s own mutable collections are always independently deep-copied
 * (see [deepCopyInto]) — `XmlSerializerUtil.copyBean` only shallow-copies fields, so without this a
 * caller mutating `working.profiles`/`buByProfile`/`notifiedRuns` in place would mutate the live
 * [TdtSettings] service's collections too, before `apply()` ever runs.
 *
 * The table itself edits [clone]d Profile instances, never [working]'s own — aliasing them would
 * let an in-place cell edit mutate the baseline too, hiding the change from [isModified]. Because
 * an in-place rename can't be told apart from the baseline by name alone, [baselineNames] records
 * each cloned row's name as of the last [reset]/[apply] (by object identity), and [renameMap]
 * derives the `oldName -> newName` map from rows whose current name has since diverged — used both
 * to keep the active-profile combo following a rename ([rebuildActiveCombo]) and, in [apply], to
 * migrate that profile's stored credential and `buByProfile` entry to the new name.
 */
class TdtConfigurable : BoundConfigurable("Terraducktel") {

    // Internal (not private) so TdtSettingsTest — in the same Gradle module, whose `test`
    // compilation is associated with `main` — can drive it in tests.
    internal val working: TdtSettings.State = TdtSettings.State().also { deepCopyInto(it, TdtSettings.getInstance().state) }

    // The table edits THESE Profile instances — deliberately cloned, never the same objects as
    // `working.profiles` (the baseline `isModified()` compares against). If the table aliased
    // `working.profiles` directly, an in-place cell edit (rename, URL/TLS change) would mutate the
    // "baseline" too and isModified() would never see a difference.
    private val tableModel = ProfileTableModel(clone(working.profiles))
    private val table = TableView(tableModel)
    private val activeProfileCombo = ComboBox<String>()

    /** Each row currently in [tableModel], mapped (by object identity) to its name as of the last
     *  [reset]/[apply] — the baseline [renameMap] diffs the row's CURRENT name against. */
    private var baselineNames: IdentityHashMap<Profile, String> = IdentityHashMap<Profile, String>().apply {
        tableModel.items.forEach { put(it, it.name) }
    }

    /** The table's live working list of profiles — exposed (internal) so tests can simulate an
     *  in-place cell edit (e.g. a rename) without driving the actual `JTable`. */
    internal val profiles: MutableList<Profile> get() = tableModel.items

    /** Fires the table-model-changed event tests need after mutating a [profiles] element's field
     *  directly (a real cell edit goes through `TableModel.setValueAt`, which already fires this;
     *  a direct field mutation from a test does not). */
    internal fun fireProfilesChangedForTest() = tableModel.fireTableDataChanged()

    /** The refresh-interval text field — exposed (internal) so tests can simulate a user edit by
     *  setting its text, rather than reaching past the UI into [working] directly (which
     *  `apply()` would just overwrite from the — unchanged — component on the next `apply()`). */
    internal lateinit var refreshIntervalField: JBTextField
        private set

    // Profiles actually deleted via the toolbar's Remove action since the last reset()/apply(),
    // tracked by explicit removal event rather than by diffing names before/after: a table cell
    // edit mutates a Profile's `.name` field IN PLACE (same object, same row), so naively diffing
    // "old profile names no longer present" would misidentify a plain rename as a removal and
    // wrongly delete that profile's still-wanted stored credential.
    private val removedProfiles = mutableListOf<Profile>()

    /** Test seam: the pooled-thread [Future] doing the actual PasswordSafe I/O for the most
     *  recent [apply] (rename migrations + deletions). `apply()` always runs on the EDT and must
     *  return without waiting on it, so tests that assert secret-store state after `apply()` need
     *  to wait on this explicitly first. */
    internal var pendingSecretWork: Future<*>? = null
        private set

    override fun createPanel(): DialogPanel {
        tableModel.addTableModelListener { rebuildActiveCombo(activeProfileCombo.selectedItem as? String) }
        rebuildActiveCombo(working.activeProfile)

        val tablePanel = ToolbarDecorator.createDecorator(table)
            .setAddAction {
                val existing = tableModel.items.map { p -> p.name }.toSet()
                var n = tableModel.items.size + 1
                while ("profile-$n" in existing) n++
                tableModel.addRow(Profile("profile-$n", "https://"))
            }
            .setRemoveAction {
                val row = table.selectedRow
                if (row >= 0) removeProfileAt(table.convertRowIndexToModel(row))
            }
            .createPanel()

        return panel {
            group("Profiles") {
                row { cell(tablePanel).align(Align.FILL) }.resizableRow()
                row("Active profile:") { cell(activeProfileCombo) }
            }
            group("General") {
                row("Refresh interval (seconds):") {
                    refreshIntervalField = intTextField(5..3600).bindIntText(working::refreshIntervalSeconds).component
                }
                row("Runs limit:") {
                    intTextField(10..1000).bindIntText(working::runsLimit)
                }
                row("Approval poll seconds (0 = off) (reserved for a future release):") {
                    intTextField(0..3600).bindIntText(working::approvalsPollSeconds)
                }
                row { checkBox("Show status bar item (reserved for a future release)").bindSelected(working::statusBarEnabled) }
                row { checkBox("Trace requests").bindSelected(working::trace) }
            }
        }
    }

    private fun clone(profiles: List<Profile>): MutableList<Profile> =
        profiles.map { Profile(it.name, it.url, it.uiUrl, it.insecureTls) }.toMutableList()

    /** Deep-copies [source] into [target]: `XmlSerializerUtil.copyBean` only shallow-copies
     *  fields, so without this `target`'s `profiles`/`buByProfile`/`notifiedRuns` would be the SAME
     *  collection (and, for `profiles`, the same [Profile] instances) as `source`'s — any in-place
     *  edit to `target` would then also mutate `source` before anyone called `apply()`. */
    private fun deepCopyInto(target: TdtSettings.State, source: TdtSettings.State) {
        XmlSerializerUtil.copyBean(source, target)
        target.profiles = clone(source.profiles)
        target.buByProfile = HashMap(source.buByProfile)
        target.notifiedRuns = HashMap(source.notifiedRuns)
    }

    /** Clones [profiles] into the table and records the clones' names as the new [baselineNames]
     *  — done together (baseline captured from the SAME clones, before they're published to the
     *  table) so a table-model listener firing synchronously off the `tableModel.items =` write
     *  below never observes a stale baseline for the new rows. */
    private fun refreshTableFrom(profiles: List<Profile>) {
        val cloned = clone(profiles)
        baselineNames = IdentityHashMap<Profile, String>().apply { cloned.forEach { put(it, it.name) } }
        tableModel.items = cloned
    }

    /** `oldName -> newName` for every row whose current name has diverged from [baselineNames] —
     *  i.e. renamed in place since the last [reset]/[apply] (a removed row is tracked separately,
     *  in [removedProfiles]; a brand-new row has no baseline entry and so never appears here). */
    private fun renameMap(): Map<String, String> {
        val renamed = LinkedHashMap<String, String>()
        for (p in tableModel.items) {
            val base = baselineNames[p] ?: continue
            if (base != p.name) renamed[base] = p.name
        }
        return renamed
    }

    private fun removeProfileAt(modelRow: Int) {
        removedProfiles += tableModel.items[modelRow]
        tableModel.removeRow(modelRow)
    }

    /** Removes the named profile exactly as the toolbar's Remove action would (tracked for
     *  credential/BU cleanup on `apply()`), for tests that don't want to drive the real `JTable`
     *  selection. No-op if no profile has that name. */
    internal fun removeProfileForTest(name: String) {
        val idx = tableModel.items.indexOfFirst { it.name == name }
        if (idx >= 0) removeProfileAt(idx)
    }

    private fun rebuildActiveCombo(preferred: String?) {
        val names = tableModel.items.map { it.name }
        // Follow a rename: `preferred` is normally the combo's current selection, i.e. the OLD
        // name if the active profile was just renamed in the table — map it through so the active
        // profile doesn't fall out of the list just because its name changed under it.
        val mapped = preferred?.let { renameMap()[it] ?: it }
        activeProfileCombo.model = DefaultComboBoxModel(names.toTypedArray())
        activeProfileCombo.selectedItem = mapped?.takeIf { it in names }
    }

    private fun profilesModified(): Boolean {
        val current = tableModel.items
        val base = working.profiles
        if (current.size != base.size) return true
        return current.zip(base).any { (a, b) -> a != b }
    }

    override fun isModified(): Boolean {
        if (super.isModified()) return true
        if (profilesModified()) return true
        val selectedActive = activeProfileCombo.selectedItem as? String ?: ""
        return selectedActive != working.activeProfile
    }

    override fun apply() {
        super.apply() // pushes the DSL-bound scalar fields into `working`

        val settings = TdtSettings.getInstance()
        val actual = settings.state
        val oldActiveName = actual.activeProfile
        val oldActive = actual.profiles.find { it.name == oldActiveName }

        // oldName -> newName for every row renamed in place since the last reset()/apply().
        val renames = renameMap()

        val newProfiles = tableModel.items.map { Profile(it.name, it.url, it.uiUrl, it.insecureTls) }.toMutableList()
        var newActiveName = activeProfileCombo.selectedItem as? String ?: ""
        // Authoritative: if the PERSISTED active profile was renamed, follow it to the new name —
        // regardless of what the (UI-driven) combo happens to show right now.
        renames[oldActiveName]?.let { newActiveName = it }
        val newActive = newProfiles.find { it.name == newActiveName }

        val activeUrlOrTlsChanged = oldActive != null && newActive != null && oldActiveName == newActiveName &&
            (oldActive.url != newActive.url || oldActive.insecureTls != newActive.insecureTls)
        val activeRenamedOrRemoved = oldActiveName != newActiveName
        val anyRemoved = removedProfiles.isNotEmpty()
        val shouldFire = activeUrlOrTlsChanged || activeRenamedOrRemoved || anyRemoved

        val newBuByProfile = actual.buByProfile.toMutableMap()

        // Baseline (pre-rename) name for each removed row. A row renamed earlier in THIS apply
        // cycle already has its `.name` mutated in place, so the credential/BU key actually on
        // disk is the ORIGINAL name — using `removed.name` here would look up a key that was
        // never written (the just-assigned new name) and leave the real credential orphaned
        // under the old name forever. Removed rows are also never in `tableModel.items` (removal
        // drops them immediately), so they can never also appear in `renames` above.
        val removals = removedProfiles.map { baselineNames[it] ?: it.name }
        removedProfiles.clear()

        for (old in removals) newBuByProfile.remove(old)
        for ((old, new) in renames) newBuByProfile.remove(old)?.let { newBuByProfile[new] = it }

        working.profiles = newProfiles
        working.activeProfile = newActiveName
        working.buByProfile = newBuByProfile

        settings.loadState(working)
        // Rebuild the working baseline from independent copies of what was just persisted (see
        // deepCopyInto), so a later isModified() check compares against the new committed state —
        // not a stale one, and not one that aliases the service's own collections.
        deepCopyInto(working, settings.state)
        refreshTableFrom(working.profiles)
        rebuildActiveCombo(working.activeProfile)

        // Configurable.apply() always runs on the EDT, but PasswordSafe access can block (see
        // TokenManager.restore()'s KDoc) — so only the WORK LIST (renames/removals, computed
        // above from in-memory state) is decided here; the actual secret-store reads/writes/
        // deletes run on a pooled thread, and nothing above waits on it.
        //
        // `fireChanged()` moves into that SAME pooled task, running only after the migration/
        // removal work below — never before it, and never separately on the EDT. `fireChanged()`
        // synchronously publishes to `TdtSettingsListener.TOPIC`, whose one subscriber
        // (`TdtSession`) schedules a `reload()` that re-reads `terraducktel.cred.<name>` — if the
        // active profile was the one just renamed and `fireChanged()` fired first (as it used to,
        // synchronously on the EDT, before the migration below had even been scheduled), that
        // reload could race the migration and find no credential yet under the new name, signing
        // the user out until a restart. Chaining this task off the PREVIOUS apply's own pooled
        // task (rather than letting two Applies run their migrations concurrently) keeps
        // successive renames/removals from ever landing out of order.
        val previousSecretWork = pendingSecretWork
        pendingSecretWork = ApplicationManager.getApplication().executeOnPooledThread {
            try {
                previousSecretWork?.get()
            } catch (e: Exception) {
                // That was the previous apply's own failure (if any) — this apply's migration/
                // removal work is independent and must still run.
            }
            val secretStore = PasswordSafeSecretStore()
            for ((old, new) in renames) {
                val credential = secretStore.get("terraducktel.cred.$old")
                if (credential != null) {
                    secretStore.set("terraducktel.cred.$new", credential)
                    secretStore.delete("terraducktel.cred.$old")
                }
            }
            for (old in removals) secretStore.delete("terraducktel.cred.$old")
            if (shouldFire) settings.fireChanged()
        }
    }

    override fun reset() {
        removedProfiles.clear()
        deepCopyInto(working, TdtSettings.getInstance().state)
        refreshTableFrom(working.profiles)
        super.reset() // pulls the DSL-bound scalar fields back from `working`
        rebuildActiveCombo(working.activeProfile)
    }
}
