package com.terraducktel.jetbrains.settings

import com.intellij.ui.BooleanTableCellEditor
import com.intellij.ui.BooleanTableCellRenderer
import com.intellij.util.ui.ColumnInfo
import com.intellij.util.ui.ListTableModel
import javax.swing.table.TableCellEditor
import javax.swing.table.TableCellRenderer

private object NameColumn : ColumnInfo<Profile, String>("Name") {
    override fun valueOf(item: Profile): String = item.name
    override fun setValue(item: Profile, value: String) { item.name = value }
    override fun isCellEditable(item: Profile): Boolean = true
}

private object UrlColumn : ColumnInfo<Profile, String>("API URL") {
    override fun valueOf(item: Profile): String = item.url
    override fun setValue(item: Profile, value: String) { item.url = value }
    override fun isCellEditable(item: Profile): Boolean = true
}

private object UiUrlColumn : ColumnInfo<Profile, String>("UI URL") {
    override fun valueOf(item: Profile): String = item.uiUrl
    override fun setValue(item: Profile, value: String) { item.uiUrl = value }
    override fun isCellEditable(item: Profile): Boolean = true
}

private object InsecureTlsColumn : ColumnInfo<Profile, Boolean>("Insecure TLS") {
    override fun valueOf(item: Profile): Boolean = item.insecureTls
    override fun setValue(item: Profile, value: Boolean) { item.insecureTls = value }
    override fun isCellEditable(item: Profile): Boolean = true
    override fun getColumnClass(): Class<*> = java.lang.Boolean::class.java
    override fun getRenderer(item: Profile): TableCellRenderer = BooleanTableCellRenderer()
    override fun getEditor(item: Profile): TableCellEditor = BooleanTableCellEditor()
}

/** Backs the profiles [com.intellij.ui.table.TableView] in [TdtConfigurable]. `items` is the
 *  configurable's working list of profiles — mutated in place by add/remove/edit and read back
 *  wholesale on `apply()`. */
class ProfileTableModel(items: MutableList<Profile>) :
    ListTableModel<Profile>(arrayOf(NameColumn, UrlColumn, UiUrlColumn, InsecureTlsColumn), items)
