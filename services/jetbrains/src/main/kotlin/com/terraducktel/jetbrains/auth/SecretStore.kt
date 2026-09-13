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

/** PasswordSafe-backed [SecretStore]. Not unit-tested here (no headless PasswordSafe outside a
 *  platform test) — [TokenManager]'s own tests use an in-memory store instead. */
class PasswordSafeSecretStore : SecretStore {
    private fun attrs(key: String) = CredentialAttributes(generateServiceName("Terraducktel", key), key)
    override fun get(key: String): String? = PasswordSafe.instance.getPassword(attrs(key))
    override fun set(key: String, value: String) { PasswordSafe.instance.set(attrs(key), Credentials(key, value)) }
    override fun delete(key: String) { PasswordSafe.instance.set(attrs(key), null) }
}
