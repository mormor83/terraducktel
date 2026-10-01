package com.terraducktel.jetbrains.api

import com.sun.net.httpserver.HttpsConfigurator
import com.sun.net.httpserver.HttpsServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.IOException
import java.net.InetSocketAddress
import java.security.KeyStore
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory

/**
 * [HttpTransport]'s security-relevant claim (see its class KDoc): `insecureTls` relaxes trust and
 * hostname verification for ONE connection only, never the JVM-wide default. Proven against a real
 * self-signed HTTPS server rather than mocks, so a regression that widened the relaxation (e.g. by
 * calling [HttpsURLConnection.setDefaultSSLSocketFactory]) would actually be caught.
 *
 * The self-signed keypair is generated at test time by shelling out to the running JDK's own
 * `keytool` into a throwaway PKCS12 file — a `sun.security.x509`-free way to mint a real
 * certificate without pulling in a crypto library dependency just for this one test.
 */
class HttpTransportTlsTest {
    private lateinit var server: HttpsServer

    // A second server, sharing the exact same self-signed cert/key but on its own ephemeral port
    // (still 127.0.0.1) — used as the target of the "plain" request in the negative test, below.
    // Reusing `server`'s own port for that second request would give the JDK's HttpURLConnection
    // keep-alive pool a chance to silently reuse the first (already-handshaked-under-insecureTls)
    // connection instead of opening a fresh one, which would validate nothing: this test needs an
    // actual NEW handshake, under the JVM's real defaults, to prove those defaults were never
    // touched by the earlier insecureTls=true request.
    private lateinit var server2: HttpsServer
    private lateinit var keystoreFile: File

    // The JVM-wide TLS defaults this test finds on arrival, and the strict factory it installs in
    // their place for its own duration. Installing our own is not incidental: when the whole suite
    // runs in one JVM, an IntelliJ platform test may already have initialised the IDE's
    // CertificateManager, whose default SSLSocketFactory accepts unknown certificates — under which
    // the negative assertion below would pass a self-signed certificate and prove nothing about
    // OUR code. A strict default (the JDK's own trust managers, i.e. the system CA store, which
    // cannot possibly trust a cert minted seconds ago) makes the assertion deterministic and
    // actually about `insecureTls` staying per-connection. Restored in @After.
    private lateinit var savedFactory: SSLSocketFactory
    private lateinit var savedVerifier: HostnameVerifier
    private lateinit var strictFactory: SSLSocketFactory

    @Before
    fun startServer() {
        keystoreFile = File.createTempFile("tdt-test-keystore", ".p12")
        keystoreFile.deleteOnExit()
        // keytool refuses to -genkeypair into an existing (even empty) file — createTempFile's
        // whole point is a guaranteed-fresh, race-free name, so just delete the placeholder it
        // created and let keytool create the real file at that same path.
        keystoreFile.delete()
        generateSelfSignedKeystore(keystoreFile)

        val ks = KeyStore.getInstance("PKCS12")
        keystoreFile.inputStream().use { ks.load(it, KEYSTORE_PASSWORD) }
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        kmf.init(ks, KEYSTORE_PASSWORD)
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(kmf.keyManagers, null, null)

        server = startHttpsServer(sslContext)
        server2 = startHttpsServer(sslContext)

        savedFactory = HttpsURLConnection.getDefaultSSLSocketFactory()
        savedVerifier = HttpsURLConnection.getDefaultHostnameVerifier()
        strictFactory = SSLContext.getInstance("TLS").apply { init(null, null, null) }.socketFactory
        HttpsURLConnection.setDefaultSSLSocketFactory(strictFactory)
    }

    private fun startHttpsServer(sslContext: SSLContext): HttpsServer {
        val srv = HttpsServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        srv.httpsConfigurator = HttpsConfigurator(sslContext)
        srv.createContext("/ok") { ex ->
            val bytes = "ok".toByteArray(Charsets.UTF_8)
            ex.sendResponseHeaders(200, bytes.size.toLong())
            ex.responseBody.use { it.write(bytes) }
            ex.close()
        }
        srv.start()
        return srv
    }

    @After
    fun stopServer() {
        HttpsURLConnection.setDefaultSSLSocketFactory(savedFactory)
        HttpsURLConnection.setDefaultHostnameVerifier(savedVerifier)
        server.stop(0)
        server2.stop(0)
        keystoreFile.delete()
    }

    private fun generateSelfSignedKeystore(out: File) {
        val keytool = File(System.getProperty("java.home"), "bin/keytool").absolutePath
        val process = ProcessBuilder(
            keytool, "-genkeypair", "-alias", "tdt-test", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "3650", "-storetype", "PKCS12",
            "-keystore", out.absolutePath, "-storepass", "changeit", "-keypass", "changeit",
            "-dname", "CN=127.0.0.1",
        ).redirectErrorStream(true).start()
        val output = process.inputStream.bufferedReader().readText()
        val exit = process.waitFor()
        check(exit == 0) { "keytool failed to generate a test keystore (exit=$exit): $output" }
    }

    private val url get() = "https://127.0.0.1:${server.address.port}/ok"
    private val url2 get() = "https://127.0.0.1:${server2.address.port}/ok"

    @Test fun `insecureTls connects to and reads from a self-signed server`() {
        val r = HttpTransport.request("GET", url, emptyMap(), null, insecureTls = true)
        assertEquals(200, r.status)
        assertEquals("ok", r.text)
    }

    @Test fun `without insecureTls the same self-signed server is rejected and the JVM default is untouched`() {
        // Relax trust for ONE request first — this is what must not leak into the plain request
        // below, against the very same host (a different port of it — see `server2`'s KDoc).
        val relaxed = HttpTransport.request("GET", url, emptyMap(), null, insecureTls = true)
        assertEquals(200, relaxed.status)

        // A fresh handshake under the JVM's (strict) defaults must still reject the self-signed
        // certificate. Had `insecureTls` installed its trust-all factory JVM-wide, this would
        // succeed instead.
        assertThrows(IOException::class.java) {
            HttpTransport.request("GET", url2, emptyMap(), null, insecureTls = false, connectTimeoutMs = 3_000, readTimeoutMs = 3_000)
        }

        // And the defaults are still, identically, the ones in force before either request.
        assertSame(strictFactory, HttpsURLConnection.getDefaultSSLSocketFactory())
        assertSame(savedVerifier, HttpsURLConnection.getDefaultHostnameVerifier())
    }

    private companion object {
        val KEYSTORE_PASSWORD = "changeit".toCharArray()
    }
}
