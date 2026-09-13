package com.terraducktel.jetbrains.api

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.*

data class HttpResponse(val status: Int, val text: String)

/** Minimal blocking JSON-over-HTTP on HttpURLConnection. Call off the EDT. Per-connection
 *  insecure TLS (self-signed dev stacks) never touches JVM-global SSL or hostname settings —
 *  the JDK HttpClient cannot relax hostname verification per client, hence HttpURLConnection. */
object HttpTransport {
    private val trustAll: SSLSocketFactory by lazy {
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) {}
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        SSLContext.getInstance("TLS").apply { init(null, arrayOf<TrustManager>(tm), SecureRandom()) }.socketFactory
    }

    @Throws(IOException::class)
    fun request(
        method: String, url: String, headers: Map<String, String>, body: String?, insecureTls: Boolean,
        connectTimeoutMs: Int = 10_000, readTimeoutMs: Int = 30_000,
    ): HttpResponse {
        val conn = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            if (insecureTls && conn is HttpsURLConnection) { conn.sslSocketFactory = trustAll; conn.hostnameVerifier = HostnameVerifier { _, _ -> true } }
            conn.requestMethod = method
            conn.connectTimeout = connectTimeoutMs; conn.readTimeout = readTimeoutMs
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Accept", "application/json")
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val text = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            return HttpResponse(status, text)
        } finally { conn.disconnect() }
    }
}
