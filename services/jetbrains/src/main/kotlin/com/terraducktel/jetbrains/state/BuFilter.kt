package com.terraducktel.jetbrains.state

import com.terraducktel.jetbrains.api.BusinessUnit

/**
 * Pure rules for the "which business units are shown" filter. The persisted form is the set of
 * HIDDEN slugs (see [com.terraducktel.jetbrains.settings.TdtSettings.hiddenBuSlugs]), not the
 * visible set, so a business unit the user has never seen is visible by default.
 */
object BuFilter {

    /** [all] minus the hidden ones, in [all]'s order. Hidden slugs that are not in [all] (a
     *  membership that was removed) are ignored. */
    fun visible(all: List<BusinessUnit>, hidden: Set<String>): List<BusinessUnit> =
        all.filter { it.slug !in hidden }

    /** The hidden set that results from the user ticking exactly [selectedSlugs] in the filter
     *  dialog, or null when that selection is not allowed (no known business unit ticked). */
    fun hiddenForSelection(all: List<BusinessUnit>, selectedSlugs: Set<String>): Set<String>? {
        val known = all.map { it.slug }.toSet()
        if (known.none { it in selectedSlugs }) return null
        return known - selectedSlugs
    }
}
