package com.terraducktel.jetbrains.auth

import com.intellij.credentialStore.CredentialAttributes
import com.intellij.credentialStore.Credentials
import com.intellij.credentialStore.generateServiceName
import com.intellij.ide.passwordSafe.PasswordSafe

/** Subset of the platform's secret storage so [TokenManager] is testable without the IDE. */
interface SecretStore {
    fun get(key: String): String?
    fun set(key: String, value: String)
    fun delete(key: String)
}

/** PasswordSafe-backed [SecretStore]. Round-tripped against the in-memory PasswordSafe the
 *  platform test framework provides in [PasswordSafeSecretStoreTest] — [TokenManager]'s own
 *  tests use a plain in-memory fake instead, since they don't need the IDE. */
class PasswordSafeSecretStore : SecretStore {
    private fun attrs(key: String) = CredentialAttributes(generateServiceName("Terraducktel", key), key)
    override fun get(key: String): String? = PasswordSafe.instance.getPassword(attrs(key))
    override fun set(key: String, value: String) { PasswordSafe.instance.set(attrs(key), Credentials(key, value)) }
    override fun delete(key: String) { PasswordSafe.instance.set(attrs(key), null) }
}
