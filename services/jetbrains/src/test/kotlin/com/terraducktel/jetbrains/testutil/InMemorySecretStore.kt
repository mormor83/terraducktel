package com.terraducktel.jetbrains.testutil

import com.terraducktel.jetbrains.auth.SecretStore

/** Plain in-memory [SecretStore] for tests that need to inject a secret store without touching
 *  PasswordSafe (e.g. [com.terraducktel.jetbrains.session.TdtSession]'s `secretStoreFactory` seam). */
class InMemorySecretStore : SecretStore {
    private val map = HashMap<String, String>()
    override fun get(key: String): String? = synchronized(map) { map[key] }
    override fun set(key: String, value: String) { synchronized(map) { map[key] = value } }
    override fun delete(key: String) { synchronized(map) { map.remove(key) } }
}
