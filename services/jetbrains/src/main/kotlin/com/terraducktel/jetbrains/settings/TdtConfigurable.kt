package com.terraducktel.jetbrains.settings

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
 * a successful [apply]. The table itself edits [clone]d Profile instances, never [working]'s own —
 * aliasing them would let an in-place cell edit mutate the baseline too, hiding the change from
 * [isModified].
 */
class TdtConfigurable : BoundConfigurable("Terraducktel") {

    // Internal (not private) so TdtSettingsTest — in the same Gradle module, whose `test`
    // compilation is associated with `main` — can drive it in tests.
    internal val working: TdtSettings.State = TdtSettings.State().also {
        XmlSerializerUtil.copyBean(TdtSettings.getInstance().state, it)
    }

    // The table edits THESE Profile instances — deliberately cloned, never the same objects as
    // `working.profiles` (the baseline `isModified()` compares against). If the table aliased
    // `working.profiles` directly, an in-place cell edit (rename, URL/TLS change) would mutate the
    // "baseline" too and isModified() would never see a difference.
    private val tableModel = ProfileTableModel(clone(working.profiles))
    private val table = TableView(tableModel)
    private val activeProfileCombo = ComboBox<String>()

    /** The table's live working list of profiles — exposed (internal) so tests can simulate an
     *  in-place cell edit (e.g. a rename) without driving the actual `JTable`. */
    internal val profiles: MutableList<Profile> get() = tableModel.items

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
                row("Approval poll seconds (0 = off):") {
                    intTextField(0..3600).bindIntText(working::approvalsPollSeconds)
                }
                row { checkBox("Show status bar item").bindSelected(working::statusBarEnabled) }
                row { checkBox("Trace requests").bindSelected(working::trace) }
            }
        }
    }

    private fun clone(profiles: List<Profile>): MutableList<Profile> =
        profiles.map { Profile(it.name, it.url, it.uiUrl, it.insecureTls) }.toMutableList()

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
        activeProfileCombo.model = DefaultComboBoxModel(names.toTypedArray())
        activeProfileCombo.selectedItem = preferred?.takeIf { it in names }
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

        val newProfiles = tableModel.items.map { Profile(it.name, it.url, it.uiUrl, it.insecureTls) }.toMutableList()
        val newActiveName = activeProfileCombo.selectedItem as? String ?: ""
        val newActive = newProfiles.find { it.name == newActiveName }

        val activeUrlOrTlsChanged = oldActive != null && newActive != null && oldActiveName == newActiveName &&
            (oldActive.url != newActive.url || oldActive.insecureTls != newActive.insecureTls)
        val activeRenamedOrRemoved = oldActiveName != newActiveName
        val anyRemoved = removedProfiles.isNotEmpty()
        val shouldFire = activeUrlOrTlsChanged || activeRenamedOrRemoved || anyRemoved

        val secretStore = PasswordSafeSecretStore()
        val newBuByProfile = actual.buByProfile.toMutableMap()
        for (removed in removedProfiles) {
            secretStore.delete("terraducktel.cred.${removed.name}")
            newBuByProfile.remove(removed.name)
        }
        removedProfiles.clear()

        working.profiles = newProfiles
        working.activeProfile = newActiveName
        working.buByProfile = newBuByProfile

        settings.loadState(working)
        // Resync the working baseline to independent copies of what was just persisted, so a
        // later isModified() check compares against the new committed state, not a stale one.
        XmlSerializerUtil.copyBean(settings.state, working)
        tableModel.items = clone(working.profiles)
        rebuildActiveCombo(working.activeProfile)

        if (shouldFire) settings.fireChanged()
    }

    override fun reset() {
        removedProfiles.clear()
        XmlSerializerUtil.copyBean(TdtSettings.getInstance().state, working)
        tableModel.items = clone(working.profiles)
        super.reset() // pulls the DSL-bound scalar fields back from `working`
        rebuildActiveCombo(working.activeProfile)
    }
}
