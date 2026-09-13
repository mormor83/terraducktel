package com.terraducktel.jetbrains.settings

/**
 * One configured Terraducktel deployment. Plain bean (no-arg constructor + `var` properties) so
 * [com.intellij.util.xmlb.XmlSerializer] can persist it as part of [TdtSettings.State]. Never
 * carries a credential — those live in [com.terraducktel.jetbrains.auth.PasswordSafeSecretStore]
 * under `terraducktel.cred.<name>`.
 *
 * `@JvmOverloads` doesn't change the Kotlin call surface (every parameter already has a default);
 * it exists purely so the compiler also emits a truly zero-arg constructor overload, which is what
 * XmlSerializer's reflection-based instantiation of list elements requires.
 */
class Profile @JvmOverloads constructor(
    var name: String = "",
    var url: String = "",
    var uiUrl: String = "",
    var insecureTls: Boolean = false,
) {
    override fun equals(other: Any?): Boolean =
        other is Profile && name == other.name && url == other.url && uiUrl == other.uiUrl && insecureTls == other.insecureTls

    override fun hashCode(): Int {
        var result = name.hashCode()
        result = 31 * result + url.hashCode()
        result = 31 * result + uiUrl.hashCode()
        result = 31 * result + insecureTls.hashCode()
        return result
    }

    override fun toString(): String = "Profile(name=$name, url=$url, uiUrl=$uiUrl, insecureTls=$insecureTls)"
}
