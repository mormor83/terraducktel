package com.terraducktel.jetbrains.auth

import com.intellij.testFramework.fixtures.BasePlatformTestCase

/** PasswordSafe is in-memory under the platform test framework, so this round-trips for real
 *  rather than against a fake. [TokenManager]'s own tests use [SecretStore] via an in-memory
 *  fake instead — this only proves the thin PasswordSafe adapter itself. */
class PasswordSafeSecretStoreTest : BasePlatformTestCase() {
    fun testSetGetDeleteRoundTrip() {
        val store = PasswordSafeSecretStore()
        val key = "terraducktel.cred.test-profile"

        assertNull(store.get(key))

        store.set(key, "s3cr3t")
        assertEquals("s3cr3t", store.get(key))

        store.delete(key)
        assertNull(store.get(key))
    }
}
