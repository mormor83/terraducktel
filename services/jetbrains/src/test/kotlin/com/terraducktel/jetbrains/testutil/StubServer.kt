package com.terraducktel.jetbrains.testutil

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class StubServer : AutoCloseable {
    data class Call(val method: String, val path: String, val query: String?, val headers: Map<String, String>, val body: String)
    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    // The JDK's default (no executor) HttpServer dispatches all requests one at a time on a
    // single internal thread, so a handler that blocks (e.g. on a CountDownLatch, to force
    // concurrent requests to genuinely overlap) would wedge every other in-flight request behind
    // it. A cached thread pool lets handlers actually run in parallel.
    private val executor: ExecutorService = Executors.newCachedThreadPool()
    private val routes = HashMap<String, (Call, HttpExchange) -> Unit>()
    val calls = CopyOnWriteArrayList<Call>()
    val url: String get() = "http://127.0.0.1:${server.address.port}"

    init {
        server.executor = executor
        server.createContext("/") { ex ->
            val body = ex.requestBody.readBytes().toString(Charsets.UTF_8)
            val headers = ex.requestHeaders.entries.associate { it.key.lowercase() to it.value.joinToString(",") }
            val call = Call(ex.requestMethod, ex.requestURI.path, ex.requestURI.rawQuery, headers, body)
            calls += call
            val h = routes["${call.method} ${call.path}"]
            if (h == null) respond(ex, 404, """{"detail":"no route ${call.method} ${call.path}"}""") else h(call, ex)
        }
        server.start()
    }
    fun on(method: String, path: String, handler: (Call, HttpExchange) -> Unit) { routes["$method $path"] = handler }
    fun json(method: String, path: String, status: Int, body: String) = on(method, path) { _, ex -> respond(ex, status, body) }
    fun calls(method: String, path: String) = calls.filter { it.method == method && it.path == path }
    override fun close() {
        server.stop(0)
        executor.shutdown()
        executor.awaitTermination(5, TimeUnit.SECONDS)
    }

    companion object {
        fun respond(ex: HttpExchange, status: Int, body: String) {
            val bytes = body.toByteArray(Charsets.UTF_8)
            ex.responseHeaders.add("content-type", "application/json")
            ex.sendResponseHeaders(status, if (bytes.isEmpty()) -1 else bytes.size.toLong())
            if (bytes.isNotEmpty()) ex.responseBody.use { it.write(bytes) }
            ex.close()
        }
    }
}
