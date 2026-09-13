# JetBrains plugin — Plan 1: foundation, tool window, trigger/approve

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A JetBrains plugin (`services/jetbrains/`) that signs in to a Terraducktel deployment (password / API key / SSO), shows the Workspaces and Runs trees in a tool window, triggers plan/apply/destroy, tails run steps into a console tab, opens plan output, and approves/rejects/cancels runs — the VS Code extension's milestone-A feature set.

**Architecture:** Kotlin, IntelliJ Platform SDK (native tool window, actions, dialogs), a blocking `HttpURLConnection` transport under a typed `TdtClient` whose refresh/sign-out semantics are a port of `services/vscode/src/api/client.ts`. Application-level services hold the session, settings and polling store; each project's tool window renders the shared store. Network work runs on `Dispatchers.IO` / pooled threads, never on the EDT.

**Tech Stack:** Kotlin 2.3.21 (JVM target 21), Gradle 9.7.1 wrapper, IntelliJ Platform Gradle Plugin 2.18.1, `intellijIdea("2026.1")`, bundled `kotlinx.serialization.json` + kotlinx.coroutines, JUnit 4 + `BasePlatformTestCase`, `com.sun.net.httpserver` stub server in tests.

**Spec:** `docs/superpowers/specs/2026-09-13-jetbrains-plugin-design.md`

## Global Constraints

- Plugin id `com.terraducktel.jetbrains`, name **Terraducktel**, vendor `Terraducktel`, version `0.1.0`, `sinceBuild = "261"`, **no** `untilBuild`.
- `<depends>com.intellij.modules.platform</depends>` only. No dependency on Terraform/HCL or Git plugins.
- **No third-party runtime dependencies.** JSON = `kotlinx.serialization.json` via `<dependencies><module name="intellij.libraries.kotlinx.serialization.json"/></dependencies>` + Gradle `bundledModule(...)`. Coroutines = the platform's bundled kotlinx.coroutines. HTTP = `java.net.HttpURLConnection`. Test-only deps: `junit:junit:4.13.2`.
- Kotlin package root `com.terraducktel.jetbrains`. JVM target 21 (`kotlin.compilerOptions.jvmTarget = JVM_21`, `JavaCompile.options.release = 21`).
- Never touch JVM-global TLS/hostname settings; insecure TLS is per-connection on the profile's `HttpsURLConnection` only.
- Secrets only in PasswordSafe; never in settings XML, logs, or notifications. Trace logging (`trace` setting) logs `METHOD /path → status (ms)` only.
- No network on the EDT. Every `AnAction` overrides `getActionUpdateThread() = ActionUpdateThread.BGT`.
- Signed out ⇒ no requests at all (client throws `ApiError(401, "Not signed in")` before the network when no credential is stored).
- Toolchain: the build needs a JDK 21 toolchain; `settings.gradle.kts` applies `org.gradle.toolchains.foojay-resolver-convention` version `1.0.0` so Gradle provisions one. Running Gradle itself needs any Java ≥ 17 — locally `JAVA_HOME=$HOME/.local/share/JetBrains/Toolbox/apps/pycharm/jbr` (JetBrains Runtime 25) works.
- Commit trailers (every commit): `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` then `Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit`. Conventional commit prefixes (`feat(jetbrains):`, `test(jetbrains):`, …).
- Port semantics from `services/vscode/src/**` **exactly** where a task says "port"; the TypeScript file is the reference implementation and its `test/unit/*.test.ts` cases are the required test cases. Do not invent different behaviour.

---

## File map

| File | Responsibility |
|---|---|
| `services/jetbrains/build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `gradlew*`, `gradle/wrapper/*`, `.gitignore` | Build (Task 1) |
| `services/jetbrains/api_contract.json`, `services/api/tests/test_jetbrains_api_contract.py` | Contract guard (Task 1) |
| `src/main/resources/META-INF/plugin.xml`, `icons/tdt.svg` | Descriptor, tool window icon (Task 1, grown by later tasks) |
| `api/Types.kt`, `api/ApiError.kt` | DTOs + statuses (Task 2) |
| `api/HttpTransport.kt` | Blocking JSON-over-HTTP, timeouts, per-connection insecure TLS (Task 2) |
| `api/TdtClient.kt` | Bearer/BU headers, 401→refresh→retry, auth epoch, typed calls (Task 3) |
| `auth/Credential.kt`, `auth/SecretStore.kt`, `auth/Jwt.kt`, `auth/TokenManager.kt` | Long-lived credential + access token lifecycle (Task 4) |
| `auth/Sso.kt` | Loopback listener + browser hand-off (Task 5) |
| `settings/Profile.kt`, `settings/TdtSettings.kt`, `settings/TdtConfigurable.kt`, `settings/ProfileTableModel.kt` | Persistent settings + Settings UI (Task 6) |
| `session/TdtSession.kt`, `session/TdtSessionListener.kt`, `session/SignInFlow.kt` | Active profile/BU/client, sign-in, events (Task 7) |
| `actions/auth/*.kt` | Sign in / Sign out / Switch profile / Switch BU (Task 7) |
| `state/Grouping.kt`, `state/Store.kt` | Tree grouping port, polling cache (Task 8) |
| `toolwindow/TdtToolWindowFactory.kt`, `toolwindow/WorkspacesPanel.kt`, `toolwindow/RunsPanel.kt`, `toolwindow/nodes/*.kt`, `toolwindow/TreeIcons.kt` | Tool window (Task 9) |
| `output/RunTail.kt`, `output/RunConsoles.kt` | Step tailing + console tabs (Task 10) |
| `actions/workspace/*.kt`, `actions/ActionUtil.kt` | Plan/Apply/Destroy/Set branch/Sync/Open/Copy (Task 10) |
| `output/PlanDocument.kt`, `actions/run/*.kt` | Plan document; Approve/Reject/Cancel/Watch/Show plan (Task 11) |
| `services/jetbrains/README.md`, `CHANGELOG.md`, `docs/JETBRAINS.md`, `CLAUDE.md` row, `Makefile` targets | Docs + make (Task 12) |

All Kotlin paths below are relative to `services/jetbrains/src/main/kotlin/com/terraducktel/jetbrains/`; tests to `services/jetbrains/src/test/kotlin/com/terraducktel/jetbrains/`.

---

### Task 1: Gradle scaffold, descriptor, contract guard

**Files:**
- Create: `services/jetbrains/settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `.gitignore`, `gradle/wrapper/gradle-wrapper.properties` (+ jar, `gradlew`, `gradlew.bat` generated)
- Create: `services/jetbrains/src/main/resources/META-INF/plugin.xml`, `src/main/resources/icons/tdt.svg`, `src/main/resources/icons/tdt_dark.svg`
- Create: `services/jetbrains/api_contract.json`, `services/api/tests/test_jetbrains_api_contract.py`
- Create: `src/main/kotlin/com/terraducktel/jetbrains/TdtLog.kt`, `src/test/kotlin/com/terraducktel/jetbrains/PluginLoadsTest.kt`

**Interfaces:**
- Produces: a building, testable Gradle project; `TdtLog.LOG` (`Logger`) and `TdtLog.trace(line: String)` used by every later task.

- [ ] **Step 1: Write the build files**

`services/jetbrains/settings.gradle.kts`:
```kotlin
plugins { id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0" }
rootProject.name = "terraducktel-jetbrains"
```

`services/jetbrains/build.gradle.kts`:
```kotlin
import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("java")
    id("org.jetbrains.kotlin.jvm") version "2.3.21"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.3.21"
    id("org.jetbrains.intellij.platform") version "2.18.1"
}

group = "com.terraducktel"
version = "0.1.0"

repositories {
    mavenCentral()
    intellijPlatform { defaultRepositories() }
}

dependencies {
    intellijPlatform {
        intellijIdea("2026.1")
        bundledModule("intellij.libraries.kotlinx.serialization.json")
        testFramework(TestFrameworkType.Platform)
    }
    testImplementation("junit:junit:4.13.2")
}

intellijPlatform {
    pluginConfiguration {
        id = "com.terraducktel.jetbrains"
        name = "Terraducktel"
        version = project.version.toString()
        vendor { name = "Terraducktel" }
        ideaVersion {
            sinceBuild = "261"
            untilBuild = provider { null }
        }
    }
    buildSearchableOptions = false
}

kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_21 } }
tasks.withType<JavaCompile> { options.release = 21 }
tasks.test { useJUnit(); testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL } }
```

`services/jetbrains/gradle.properties`:
```properties
org.gradle.jvmargs=-Xmx2g
org.gradle.caching=true
kotlin.stdlib.default.dependency=false
```
(`kotlin.stdlib.default.dependency=false`: the platform bundles the stdlib; shipping our own is forbidden by the SDK guidelines.)

`services/jetbrains/.gitignore`:
```
build/
.gradle/
.intellijPlatform/
.kotlin/
*.iml
```

- [ ] **Step 2: Generate the Gradle wrapper**

A Gradle 9.7.1 distribution is unpacked at `/tmp/claude-1000/-home-pavel-PycharmProjects-terraducktel/f9f3a543-0963-46e2-8869-189708b62795/scratchpad/gradle-9.7.1` (download `https://services.gradle.org/distributions/gradle-9.7.1-bin.zip` if it is gone).
```bash
cd services/jetbrains
export JAVA_HOME=$HOME/.local/share/JetBrains/Toolbox/apps/pycharm/jbr
<gradle-dir>/bin/gradle wrapper --gradle-version 9.7.1 --distribution-type bin
```
Expected: `gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`, `gradle/wrapper/gradle-wrapper.properties` created; `gradlew` is executable. From now on use `./gradlew` with that `JAVA_HOME`.

- [ ] **Step 3: Write the descriptor, icon and logger**

`src/main/resources/META-INF/plugin.xml`:
```xml
<idea-plugin>
  <id>com.terraducktel.jetbrains</id>
  <name>Terraducktel</name>
  <vendor url="https://github.com/mormor83/terraducktel">Terraducktel</vendor>
  <description><![CDATA[
    Terraducktel (TDT) inside your IDE: workspaces and runs trees, plan / apply / destroy with
    live step output, plan review, gated approvals, and approval notifications — against any
    self-hosted Terraducktel deployment. Sign in with email + password, an API key, or SSO.
  ]]></description>
  <depends>com.intellij.modules.platform</depends>
  <dependencies>
    <module name="intellij.libraries.kotlinx.serialization.json"/>
  </dependencies>
  <extensions defaultExtensionNs="com.intellij">
    <notificationGroup id="Terraducktel" displayType="BALLOON"/>
  </extensions>
</idea-plugin>
```

`src/main/resources/icons/tdt.svg` — copy `services/vscode/media/tdt.svg` and set `width="13" height="13"` on the root `<svg>`; `tdt_dark.svg` is the same file (currentColor already adapts). 

`TdtLog.kt`:
```kotlin
package com.terraducktel.jetbrains

import com.intellij.openapi.diagnostic.Logger

object TdtLog {
    val LOG: Logger = Logger.getInstance("#com.terraducktel")
    /** Request-level tracing; the settings `trace` flag gates it at the call site (Task 7). */
    fun trace(line: String) = LOG.info(line)
}
```

- [ ] **Step 4: Write the contract file and guard test**

`services/jetbrains/api_contract.json` — identical `endpoints` array to `services/vscode/api_contract.json`, with `_comment` changed to `"Every API endpoint the JetBrains plugin calls. Guarded by services/api/tests/test_jetbrains_api_contract.py. Paths are relative to /api/v1."`

`services/api/tests/test_jetbrains_api_contract.py` — copy `test_vscode_api_contract.py`, change `CONTRACT = Path(__file__).resolve().parents[2] / "jetbrains" / "api_contract.json"`, the docstring to say JetBrains plugin, and the assertion message `"JetBrains contract missing at …"`.

- [ ] **Step 5: Write a smoke test and run everything**

`src/test/kotlin/com/terraducktel/jetbrains/PluginLoadsTest.kt`:
```kotlin
package com.terraducktel.jetbrains

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.testFramework.fixtures.BasePlatformTestCase

class PluginLoadsTest : BasePlatformTestCase() {
    fun testDescriptorLoads() {
        val d = PluginManagerCore.getPlugin(PluginId.getId("com.terraducktel.jetbrains"))
        assertNotNull("plugin descriptor not loaded", d)
        assertEquals("Terraducktel", d!!.name)
    }
}
```
Run: `cd services/jetbrains && ./gradlew test buildPlugin --console=plain` → BUILD SUCCESSFUL, `build/distributions/terraducktel-jetbrains-0.1.0.zip` exists.
Run: `cd services/api && venv/bin/python -m pytest tests/test_jetbrains_api_contract.py -q` → 3 passed.

- [ ] **Step 6: Commit**
```bash
git add services/jetbrains services/api/tests/test_jetbrains_api_contract.py
git commit -m "feat(jetbrains): Gradle scaffold, plugin descriptor and API contract guard"
```

---

### Task 2: Types, ApiError, HttpTransport

**Files:**
- Create: `api/Types.kt`, `api/ApiError.kt`, `api/HttpTransport.kt`
- Test: `src/test/.../api/HttpTransportTest.kt`, `src/test/.../testutil/StubServer.kt`

**Interfaces:**
- Produces: `data class HttpResponse(val status: Int, val text: String)`; `object HttpTransport { fun request(method: String, url: String, headers: Map<String,String>, body: String?, insecureTls: Boolean, connectTimeoutMs: Int = 10_000, readTimeoutMs: Int = 30_000): HttpResponse }`; `class ApiError(val status: Int, message: String, val detail: JsonElement? = null) : RuntimeException(message)`; `@Serializable` DTOs `AuthConfig, TokenPair, BusinessUnit, Workspace, Run, RunStep, GraphSummary, RunGraph, Branches, PlanOutput, TriggerRunBody`; `TERMINAL_RUN_STATUSES`, `PLAN_LANDED_STATUSES`; `TdtJson` (`Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = false }`).

- [ ] **Step 1: Types**

`api/Types.kt` — one `@Serializable` data class per interface in `services/vscode/src/api/types.ts`, same field names (snake_case, matching the JSON), nullable where TS has `?`/`| null`, defaults for optional fields:
```kotlin
package com.terraducktel.jetbrains.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

val TdtJson = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = false }

@Serializable data class AuthConfig(val mode: String = "local", val oidc_enabled: Boolean = false, val oidc_issuer: String? = null, val cli_loopback: Boolean? = null)
@Serializable data class TokenPair(val access_token: String, val refresh_token: String, val token_type: String? = null)
@Serializable data class BusinessUnit(val id: String, val slug: String, val name: String)
@Serializable data class Workspace(
    val id: String, val business_unit_id: String = "", val name: String, val environment: String = "",
    val aws_account_id: String? = null, val region: String = "", val repo_url: String? = null, val tf_working_dir: String = "",
    val repo_ref: String = "", val kind: String = "terraform", val cluster_id: String? = null, val tags: Map<String, String> = emptyMap(),
    val drift_status: String = "unknown", val path_status: String = "ok", val azure_subscription_id: String? = null,
    val gcp_project_id: String? = null, val state_backend: String = "s3", val created_at: String? = null,
)
val TERMINAL_RUN_STATUSES: Set<String> = setOf("planned", "applied", "failed", "cancelled")
/** Statuses at which a plan-phase watcher may stop: the plan landed or is awaiting approval. */
val PLAN_LANDED_STATUSES: Set<String> = TERMINAL_RUN_STATUSES + "awaiting_approval"
val CANCELLABLE_RUN_STATUSES: Set<String> = setOf("pending", "running", "planning", "awaiting_approval")
@Serializable data class Run(
    val id: String, val workspace_id: String, val command: String, val status: String, val branch: String? = null,
    val triggered_by: String? = null, val policy_status: String? = null, val created_at: String? = null,
    val started_at: String? = null, val completed_at: String? = null,
)
@Serializable data class RunStep(
    val id: String = "", val run_id: String = "", val position: Int, val name: String, val status: String,
    val started_at: String? = null, val completed_at: String? = null, val duration_seconds: Double? = null,
    val output: String? = null, val summary_json: String? = null,
)
@Serializable data class GraphSummary(val add: Int = 0, val change: Int = 0, val destroy: Int = 0, val replace: Int = 0)
@Serializable data class RunGraph(val summary: GraphSummary = GraphSummary())
@Serializable data class Branches(val source: String = "", val default_branch: String? = null, val branches: List<String> = emptyList())
@Serializable data class PlanOutput(val plan_output: String? = null)
@Serializable data class TriggerRunBody(val command: String, val branch: String? = null)
@Serializable data class LoginBody(val email: String, val password: String)
@Serializable data class RefreshBody(val refresh_token: String)
@Serializable data class RepoRefPatch(val repo_ref: String)
@Serializable data class RejectBody(val comment: String)
```

`api/ApiError.kt`:
```kotlin
package com.terraducktel.jetbrains.api

import kotlinx.serialization.json.*

class ApiError(val status: Int, message: String, val detail: JsonElement? = null) : RuntimeException(message) {
    companion object {
        /** Port of client.ts `detailToMessage`: string detail → as is; FastAPI validation list → "loc: msg; …"; else "HTTP <status>". */
        fun fromResponse(status: Int, text: String): ApiError {
            val parsed = runCatching { TdtJson.parseToJsonElement(text) }.getOrNull()
                ?: return ApiError(status, text.trim().ifEmpty { "HTTP $status" }, null)
            val detail = (parsed as? JsonObject)?.get("detail")
            return when {
                detail is JsonPrimitive && detail.isString -> ApiError(status, detail.content, detail)
                detail is JsonArray -> {
                    val msg = detail.mapNotNull { e ->
                        val o = e as? JsonObject ?: return@mapNotNull null
                        val loc = (o["loc"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.content }?.filter { it != "body" }?.joinToString(".") ?: ""
                        "$loc: ${(o["msg"] as? JsonPrimitive)?.content ?: ""}"
                    }.joinToString("; ")
                    ApiError(status, msg.ifEmpty { "HTTP $status" }, detail)
                }
                else -> ApiError(status, "HTTP $status", parsed)
            }
        }
    }
}
```

- [ ] **Step 2: Test stub server helper**

`src/test/kotlin/com/terraducktel/jetbrains/testutil/StubServer.kt` — a reusable in-process HTTP stub (port of the VS Code `test/fake-server.ts` idea):
```kotlin
package com.terraducktel.jetbrains.testutil

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList

class StubServer : AutoCloseable {
    data class Call(val method: String, val path: String, val query: String?, val headers: Map<String, String>, val body: String)
    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    private val routes = HashMap<String, (Call, HttpExchange) -> Unit>()
    val calls = CopyOnWriteArrayList<Call>()
    val url: String get() = "http://127.0.0.1:${server.address.port}"

    init {
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
    override fun close() = server.stop(0)

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
```

- [ ] **Step 3: Failing transport test**

`src/test/.../api/HttpTransportTest.kt`:
```kotlin
package com.terraducktel.jetbrains.api

import com.terraducktel.jetbrains.testutil.StubServer
import org.junit.Assert.*
import org.junit.Test

class HttpTransportTest {
    @Test fun `sends json body and headers, returns status and text`() = StubServer().use { srv ->
        srv.on("POST", "/echo") { c, ex -> StubServer.respond(ex, 201, """{"got":${c.body},"auth":"${c.headers["authorization"]}"}""") }
        val r = HttpTransport.request("POST", "${srv.url}/echo", mapOf("Authorization" to "Bearer t"), """{"a":1}""", insecureTls = false)
        assertEquals(201, r.status)
        assertTrue(r.text, r.text.contains("\"got\":{\"a\":1}") && r.text.contains("Bearer t"))
        assertEquals("application/json", srv.calls.single().headers["content-type"])
        assertEquals("application/json", srv.calls.single().headers["accept"])
    }
    @Test fun `error bodies are returned, not thrown`() = StubServer().use { srv ->
        srv.json("GET", "/nope", 422, """{"detail":"bad"}""")
        val r = HttpTransport.request("GET", "${srv.url}/nope", emptyMap(), null, insecureTls = false)
        assertEquals(422, r.status); assertEquals("""{"detail":"bad"}""", r.text)
    }
    @Test fun `204 yields empty text`() = StubServer().use { srv ->
        srv.on("POST", "/x") { _, ex -> ex.sendResponseHeaders(204, -1); ex.close() }
        assertEquals("", HttpTransport.request("POST", "${srv.url}/x", emptyMap(), null, insecureTls = false).text)
    }
    @Test(expected = java.io.IOException::class) fun `connection refused throws IOException`() {
        HttpTransport.request("GET", "http://127.0.0.1:9/x", emptyMap(), null, insecureTls = false, connectTimeoutMs = 500)
    }
}
```
Run: `./gradlew test --tests '*HttpTransportTest*'` → FAIL (unresolved reference `HttpTransport`).

- [ ] **Step 4: Implement the transport**

`api/HttpTransport.kt`:
```kotlin
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
        try {
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val text = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            return HttpResponse(status, text)
        } finally { conn.disconnect() }
    }
}
```
Run: `./gradlew test --tests '*HttpTransportTest*'` → 4 passed.

- [ ] **Step 5: Commit**
```bash
git add services/jetbrains/src
git commit -m "feat(jetbrains): API types, ApiError and HttpURLConnection transport"
```

---

### Task 3: TdtClient (port of client.ts)

**Files:**
- Create: `api/TdtClient.kt`
- Test: `src/test/.../api/TdtClientTest.kt`

**Interfaces:**
- Consumes: Task 2.
- Produces:
```kotlin
interface TokenProvider {
    fun getAccessToken(): String?          // may block (secret store load / lazy refresh)
    fun refreshAccessToken(): String?      // null = credential definitively dead; throws on transient failure
    fun hasCredential(): Boolean
    fun signOut()
}
class AuthState  // shared by a client and its withBu() clones: epoch, refreshing future, signingOut future, listeners
class TdtClient(val baseUrl: String, val bu: String, tokens: TokenProvider, insecureTls: Boolean = false, trace: ((String) -> Unit)? = null, auth: AuthState = AuthState()) {
    fun withBu(bu: String): TdtClient
    fun onSignedOut(l: () -> Unit): () -> Unit        // returns a remover
    fun ssoLoginUrl(port: Int, nonce: String): String
    fun authConfig(): AuthConfig; fun login(email: String, password: String): TokenPair; fun refresh(refreshToken: String): TokenPair
    fun listBusinessUnits(): List<BusinessUnit>; fun listWorkspaces(): List<Workspace>
    fun updateWorkspace(id: String, repoRef: String): Workspace; fun listBranches(id: String): Branches; fun syncWorkspace(id: String)
    fun triggerRun(id: String, body: TriggerRunBody): Run
    fun listRuns(limit: Int? = null, status: List<String>? = null, workspaceId: String? = null): List<Run>
    fun getRun(id: String): Run; fun getSteps(id: String, since: Int? = null, includeOutput: Boolean = true): List<RunStep>
    fun getGraph(id: String): RunGraph; fun getPlan(id: String): PlanOutput
    fun approve(id: String); fun reject(id: String, reason: String? = null); fun cancel(id: String)
}
```
All methods are **blocking** and throw `ApiError` (HTTP ≥ 400) or `IOException` (transport).

- [ ] **Step 1: Failing tests** — port every case of `services/vscode/test/unit/client.test.ts` (read it). Required cases, each against `StubServer`:
  1. sends `Authorization: Bearer <token>` and `X-Business-Unit: <bu>` on data calls; not on `authConfig()`/`login()`.
  2. query encoding: `listRuns(limit = 5, status = listOf("awaiting_approval","planned"))` → `?limit=5&status=awaiting_approval%2Cplanned`; `getSteps(id, since = 3, includeOutput = false)` → `since=3&include_output=false`; `getSteps(id)` has no `include_output`.
  3. 401 → `refreshAccessToken()` once → retry once with the fresh token → success; provider's refresh called exactly once.
  4. 401 whose token was already rotated by someone else (`getAccessToken()` now returns a different token than the one used) → retries with it, no refresh call.
  5. second 401 after refresh → `signOut()` called once, `onSignedOut` listeners fired once, `ApiError(401)` thrown.
  6. concurrent terminal-401s from 4 threads (use `Executors.newFixedThreadPool(4)`) → exactly one `signOut()` and one listener fire; a `withBu("x")` clone participating in the burst shares the same single sign-out.
  7. `getAccessToken()==null && hasCredential()` → `signOut()` once, `ApiError(401, "Session expired — sign in again")`, no HTTP request made.
  8. `getAccessToken()==null && !hasCredential()` → `ApiError(401, "Not signed in")`, no HTTP request, no `signOut()`.
  9. `refreshAccessToken()` throwing `IOException` → the original call throws that `IOException`; no sign-out.
  10. error detail propagation: `{"detail":"nope"}` → message `nope`; FastAPI list → `body.command: field required` style message (`loc: ["body","command"]`); non-JSON body → raw text; empty body → `HTTP 500`.
  11. `trace` receives `GET /workspaces → 200 (N ms)` lines.

Test provider helper:
```kotlin
class FakeTokens(var token: String? = "t", var cred: Boolean = true, val onRefresh: () -> String? = { "t2" }) : TokenProvider {
    val refreshes = java.util.concurrent.atomic.AtomicInteger(); val signOuts = java.util.concurrent.atomic.AtomicInteger()
    override fun getAccessToken() = token
    override fun refreshAccessToken(): String? { refreshes.incrementAndGet(); return onRefresh().also { token = it } }
    override fun hasCredential() = cred
    override fun signOut() { signOuts.incrementAndGet(); token = null; cred = false }
}
```
Run: `./gradlew test --tests '*TdtClientTest*'` → FAIL (unresolved `TdtClient`).

- [ ] **Step 2: Implement** — port `client.ts` line by line. Concurrency primitives: `AuthState` holds `@Volatile var epoch: Int`, `var refreshing: CompletableFuture<String?>?`, `var signingOut: CompletableFuture<Void>?`, `val listeners: CopyOnWriteArrayList<() -> Unit>`, and a private `lock = Any()`; `refreshOnce()` and `signOutOnce(epoch)` take `synchronized(lock)` to decide who creates the future, then `.get()` outside the lock. Skeleton:
```kotlin
class AuthState { @Volatile var epoch = 0; var refreshing: CompletableFuture<String?>? = null; var signingOut: CompletableFuture<Void>? = null; val listeners = CopyOnWriteArrayList<() -> Unit>(); val lock = Any() }

class TdtClient(val baseUrl: String, val bu: String, private val tokens: TokenProvider, private val insecureTls: Boolean = false,
                private val trace: ((String) -> Unit)? = null, private val auth: AuthState = AuthState()) {
    fun withBu(bu: String) = TdtClient(baseUrl, bu, tokens, insecureTls, trace, auth)
    fun onSignedOut(l: () -> Unit): () -> Unit { auth.listeners += l; return { auth.listeners -= l } }

    private fun url(path: String, query: Map<String, Any?> = emptyMap()): String {
        val qs = query.entries.filter { it.value != null && it.value != "" }.joinToString("&") { (k, v) ->
            val s = if (v is List<*>) v.joinToString(",") else v.toString()
            "${URLEncoder.encode(k, UTF_8)}=${URLEncoder.encode(s, UTF_8)}" }
        return baseUrl.trimEnd('/') + "/api/v1" + path + (if (qs.isEmpty()) "" else "?$qs")
    }

    private inline fun <reified R> send(method: String, path: String, query: Map<String, Any?> = emptyMap(), body: String? = null, auth: Boolean = true): R {
        val text = sendRaw(method, path, query, body, auth)
        return if (text.isEmpty() || R::class == Unit::class) Unit as R else TdtJson.decodeFromString(text)
    }
    private fun sendRaw(method: String, path: String, query: Map<String, Any?>, body: String?, useAuth: Boolean): String {
        val epoch = auth.epoch                                     // captured before any I/O
        fun attempt(token: String?): HttpResponse {
            val h = HashMap<String, String>()
            if (useAuth) { token?.let { h["Authorization"] = "Bearer $it" }; if (bu.isNotEmpty()) h["X-Business-Unit"] = bu }
            val t0 = System.currentTimeMillis()
            val res = HttpTransport.request(method, url(path, query), h, body, insecureTls)
            trace?.invoke("$method $path → ${res.status} (${System.currentTimeMillis() - t0} ms)")
            return res
        }
        var token: String? = null
        if (useAuth) {
            token = tokens.getAccessToken()
            if (token == null) {
                if (tokens.hasCredential()) { signOutOnce(epoch); throw ApiError(401, "Session expired — sign in again") }
                throw ApiError(401, "Not signed in")
            }
        }
        var res = attempt(token); var used = token
        if (useAuth && res.status == 401) {
            val current = tokens.getAccessToken()
            val fresh = if (current != null && current != used) current else refreshOnce()
            if (fresh != null) { res = attempt(fresh); used = fresh }
            if (res.status == 401) signOutOnce(epoch)
        }
        if (res.status >= 400) throw ApiError.fromResponse(res.status, res.text)
        return res.text
    }
    /** Coalesce parallel 401s into one refresh (one redemption of the rotating refresh token). The
     *  first caller under the lock creates the future and performs the refresh on its own thread;
     *  everyone else waits on that future. A rejection propagates unchanged (transient failure must
     *  fail the original request, never sign the user out). */
    private fun refreshOnce(): String? {
        var mine = false
        val f = synchronized(auth.lock) {
            auth.refreshing ?: CompletableFuture<String?>().also { auth.refreshing = it; mine = true }
        }
        if (mine) {
            try { f.complete(tokens.refreshAccessToken()) } catch (t: Throwable) { f.completeExceptionally(t) }
            finally { synchronized(auth.lock) { if (auth.refreshing === f) auth.refreshing = null } }
        }
        try { return f.get() } catch (e: ExecutionException) { throw e.cause ?: e }
    }

    /** Coalesce concurrent terminal-401s into one sign-out per auth epoch: a request that started in
     *  the current epoch triggers (and shares) the cycle; a request from a superseded epoch just joins
     *  whatever cycle is current. The epoch only advances on an actual sign-out. */
    private fun signOutOnce(epoch: Int) {
        var mine = false
        val f = synchronized(auth.lock) {
            if (epoch != auth.epoch) auth.signingOut
            else { auth.epoch++; CompletableFuture<Void>().also { auth.signingOut = it; mine = true } }
        } ?: return
        if (mine) {
            try { tokens.signOut(); auth.listeners.toList().forEach { it() }; f.complete(null) }
            catch (t: Throwable) { f.completeExceptionally(t) }
        }
        try { f.get() } catch (e: ExecutionException) { throw e.cause ?: e }
    }
    …typed methods exactly as in the Interfaces block; bodies encoded with TdtJson.encodeToString(LoginBody(email, password)) etc.; `reject(id, reason)` sends `RejectBody(reason)` only when reason is non-blank.
}
```
Preserve the comments' intent from `client.ts` (why epoch, why refresh rejection propagates). `refresh()`/`login()`/`authConfig()` use `auth = false`.
Run: `./gradlew test --tests '*TdtClientTest*'` → all pass.

- [ ] **Step 3: Commit**
```bash
git add services/jetbrains/src
git commit -m "feat(jetbrains): TdtClient with coalesced refresh and epoch-scoped sign-out"
```

---

### Task 4: Credentials, SecretStore, Jwt, TokenManager (port of tokenManager.ts)

**Files:**
- Create: `auth/Credential.kt`, `auth/SecretStore.kt`, `auth/Jwt.kt`, `auth/TokenManager.kt`
- Test: `src/test/.../auth/JwtTest.kt`, `src/test/.../auth/TokenManagerTest.kt`

**Interfaces:**
- Produces:
```kotlin
@Serializable data class StoredCredential(val kind: String /* "password"|"api_key"|"sso" */, val refresh_token: String? = null, val api_key: String? = null)
interface SecretStore { fun get(key: String): String?; fun set(key: String, value: String); fun delete(key: String) }
class PasswordSafeSecretStore : SecretStore   // CredentialAttributes(generateServiceName("Terraducktel", key)); user = key; password = value
data class AccessClaims(val sub: String? = null, val email: String? = null, val role: String? = null, val is_superadmin: Boolean? = null, val type: String? = null, val exp: Long? = null)
object Jwt { fun decodePayload(token: String): AccessClaims? }
class TokenManager(secrets: SecretStore, profileName: String) : TokenProvider {
    fun attach(client: TdtClient); fun onDidChange(l: () -> Unit): () -> Unit
    fun restore(); fun isSignedIn(): Boolean; fun kind(): String?; fun claims(): AccessClaims?
    fun signInWithPassword(email: String, password: String); fun signInWithTokenPair(pair: TokenPair, kind: String = "sso"); fun signInWithApiKey(key: String)
    override fun signOut(); override fun getAccessToken(): String?; override fun refreshAccessToken(): String?; override fun hasCredential(): Boolean
}
```
Secret key: `terraducktel.cred.<profileName>` (same as VS Code).

- [ ] **Step 1: Failing tests** — port `test/unit/jwt.test.ts` and `test/unit/tokenManager.test.ts` (read them). Required TokenManager cases with an `InMemorySecretStore`:
  1. `restore()` with nothing stored → `isSignedIn()==false`, `getAccessToken()==null`, `hasCredential()==false`.
  2. `signInWithApiKey("tdt_abc")` persists `{"kind":"api_key","api_key":"tdt_abc"}`; `getAccessToken()=="tdt_abc"`; `claims()==null`; rejects a key without `tdt_` prefix with `IllegalArgumentException` and stores nothing.
  3. `signInWithPassword` → client `login` → stores `{"kind":"password","refresh_token":…}`; access token = pair's; `claims()` decodes the JWT (build a fake 3-part token with base64url payload `{"email":"a@b","role":"operator"}`).
  4. lazy refresh: a new `TokenManager` over a store holding a password credential (no access token in memory) → `getAccessToken()` POSTs `/auth/refresh` once, persists the rotated refresh token, returns the new access token.
  5. concurrent `getAccessToken()` from 4 threads on a cold manager → exactly one `/auth/refresh` request.
  6. refresh 401 → `refreshAccessToken()` returns `null` (credential still stored — the client signs out); refresh `IOException`/5xx → rethrown, credential intact.
  7. `signOut()` deletes the stored secret, `isSignedIn()==false`, listeners fired.
  8. a refresh that completes after `signOut()` ran mid-flight does **not** resurrect the credential (store stays empty).
Run → FAIL (unresolved).

- [ ] **Step 2: Implement** — port `tokenManager.ts`. Blocking equivalents: `loadPromise` → a `loaded` flag under `synchronized(this)`; `refreshing` → `CompletableFuture<String?>?` created under the lock by the first caller who then performs the redemption on its own thread, others `.get()`; the `finally { if (refreshing === p) refreshing = null }` identity guard and the `if (this.cred !== cred)` post-refresh check (compare by reference to the `StoredCredential` instance read before the request) are both required. `Jwt.decodePayload` uses `java.util.Base64.getUrlDecoder()` (pad the segment to a multiple of 4 first) and `TdtJson.decodeFromString<AccessClaims>`, returning null on any failure or when the token isn't 3 parts.

`PasswordSafeSecretStore`:
```kotlin
class PasswordSafeSecretStore : SecretStore {
    private fun attrs(key: String) = CredentialAttributes(generateServiceName("Terraducktel", key), key)
    override fun get(key: String) = PasswordSafe.instance.getPassword(attrs(key))
    override fun set(key: String, value: String) = PasswordSafe.instance.set(attrs(key), Credentials(key, value))
    override fun delete(key: String) = PasswordSafe.instance.set(attrs(key), null)
}
```
Run: `./gradlew test --tests '*auth*'` → pass.

- [ ] **Step 3: Commit**
```bash
git add services/jetbrains/src
git commit -m "feat(jetbrains): PasswordSafe-backed token manager with single-flight refresh"
```

---

### Task 5: SSO loopback (port of sso.ts)

**Files:**
- Create: `auth/Sso.kt`
- Test: `src/test/.../auth/SsoTest.kt`

**Interfaces:**
- Produces:
```kotlin
class SsoCancelled : RuntimeException("SSO sign-in cancelled")
object Sso {
    /** Blocks until the browser calls back, the timeout elapses, or cancel() is invoked. */
    fun runLoopbackLogin(buildUrl: (port: Int, nonce: String) -> String, openUrl: (String) -> Boolean, timeoutMs: Long = 5 * 60_000, onCancel: ((cancel: () -> Unit) -> Unit)? = null): TokenPair
}
```

- [ ] **Step 1: Failing tests** — port `test/unit/sso.test.ts`: (a) success: `openUrl` captures the URL, the test parses `cli_port`/`cli_nonce`, GETs `http://127.0.0.1:<port>/callback?access_token=a&refresh_token=r&nonce=<nonce>` from another thread, `runLoopbackLogin` returns the pair and the response body contains "close this tab"; (b) nonce mismatch → 400 response containing "nonce mismatch", login still waiting (then cancel it); (c) missing tokens → 400 "missing tokens"; (d) `timeoutMs = 200` → throws with message containing "timed out"; (e) `onCancel` handle invoked from another thread → throws `SsoCancelled` and the port is released (a new `ServerSocket(port)` binds); (f) `openUrl` returning false → throws "Could not open the browser". Nonce is 32 url-safe chars (`[A-Za-z0-9_-]{32}`).
- [ ] **Step 2: Implement** with `com.sun.net.httpserver.HttpServer` bound to `127.0.0.1:0`, a `CompletableFuture<TokenPair>` settled by the handler / a timeout (`future.get(timeoutMs, MILLISECONDS)` → `TimeoutException` mapped to `RuntimeException("SSO sign-in timed out waiting for the browser callback")`) / cancel; `server.stop(0)` in a `finally`. Nonce: 24 random bytes (`SecureRandom`) → `Base64.getUrlEncoder().withoutPadding()`. Reuse the DONE_HTML text from `sso.ts` with "for JetBrains IDEs" instead of "for VS Code".
- [ ] **Step 3: Run `./gradlew test --tests '*SsoTest*'` → pass; commit** `feat(jetbrains): SSO loopback sign-in`.

---

### Task 6: Settings state + Settings UI

**Files:**
- Create: `settings/Profile.kt`, `settings/TdtSettings.kt`, `settings/ProfileTableModel.kt`, `settings/TdtConfigurable.kt`
- Modify: `plugin.xml` (add `<applicationConfigurable>`)
- Test: `src/test/.../settings/TdtSettingsTest.kt` (BasePlatformTestCase)

**Interfaces:**
- Produces:
```kotlin
class Profile(var name: String = "", var url: String = "", var uiUrl: String = "", var insecureTls: Boolean = false)   // bean, XML-serializable
@Service(Service.Level.APP) @State(name = "TerraducktelSettings", storages = [Storage("terraducktel.xml")])
class TdtSettings : PersistentStateComponent<TdtSettings.State> {
    class State { var profiles: MutableList<Profile> = mutableListOf(); var activeProfile: String = ""; var buByProfile: MutableMap<String, String> = mutableMapOf()
                  var refreshIntervalSeconds: Int = 30; var runsLimit: Int = 200; var approvalsPollSeconds: Int = 60; var trace: Boolean = false; var statusBarEnabled: Boolean = true
                  var notifiedRuns: MutableMap<String, Long> = mutableMapOf() }
    val state: State  // getState()/loadState() via XmlSerializerUtil.copyBean
    fun profile(name: String): Profile?; fun activeProfile(): Profile?   // active = named one if it exists, else first by name, else null
    fun uiUrlFor(p: Profile): String   // uiUrl if set else url with a trailing slash stripped
    companion object { fun getInstance(): TdtSettings = service() }
}
```
- [ ] **Step 1: Failing platform test**: set two profiles + active + bu, `XmlSerializer`-round-trip via `getState()`/`loadState()` on a fresh instance, assert equality; `activeProfile()` falls back to first-by-name when `activeProfile` names nothing; `uiUrlFor` strips a trailing slash and prefers `uiUrl`.
- [ ] **Step 2: Implement state** (`XmlSerializerUtil.copyBean(state, this.state)` in `loadState`).
- [ ] **Step 3: Configurable** — `TdtConfigurable : BoundConfigurable("Terraducktel")` using the Kotlin UI DSL v2 (`com.intellij.ui.dsl.builder.panel`): a `TableView<Profile>` over `ProfileTableModel : ListTableModel<Profile>` with columns Name / API URL / UI URL / Insecure TLS (`BooleanTableCellEditor`), wrapped in `ToolbarDecorator.createDecorator(table).setAddAction { add Profile("profile-N", "https://") }.setRemoveAction { … }`; a `ComboBox<String>` "Active profile" refreshed from the table's names on every table change; `intTextField(5..3600)` Refresh interval, `intTextField(10..1000)` Runs limit, `intTextField(0..3600)` Approval poll seconds (0 = off), checkboxes Show status bar item / Trace requests. `isModified`/`apply`/`reset` compare against a deep copy of `TdtSettings.state`. On `apply`, if the active profile's `url`/`insecureTls` changed or a profile was removed, call `TdtSettings.getInstance().fireChanged()` → publish `TdtSettingsListener.TOPIC.settingsChanged()` on the application message bus (define `interface TdtSettingsListener { fun settingsChanged() ; companion object { val TOPIC = Topic.create("Terraducktel settings", TdtSettingsListener::class.java) } }` in `TdtSettings.kt`). Removing a profile also calls `PasswordSafeSecretStore().delete("terraducktel.cred.<name>")` and drops its `buByProfile` entry.
  `plugin.xml`: `<applicationConfigurable parentId="tools" instance="com.terraducktel.jetbrains.settings.TdtConfigurable" id="com.terraducktel.jetbrains.settings" displayName="Terraducktel"/>`.
- [ ] **Step 4: Platform test** that `TdtConfigurable().createPanel()` builds without exception and `isModified` is false right after `reset()`. Run `./gradlew test --tests '*settings*'` → pass.
- [ ] **Step 5: Commit** `feat(jetbrains): persistent settings and Settings → Tools → Terraducktel page`.

---

### Task 7: Session, sign-in flow, auth actions

**Files:**
- Create: `session/TdtSessionListener.kt`, `session/TdtSession.kt`, `session/SignInFlow.kt`, `actions/auth/SignInAction.kt`, `SignOutAction.kt`, `SwitchProfileAction.kt`, `SwitchBusinessUnitAction.kt`, `actions/ActionUtil.kt`
- Modify: `plugin.xml` (actions group `Terraducktel.Toolbar`, `Terraducktel.ToolsMenu` under `ToolsMenu`)
- Test: `src/test/.../session/TdtSessionTest.kt` (BasePlatformTestCase + StubServer)

**Interfaces:**
- Produces:
```kotlin
interface TdtSessionListener { fun sessionChanged() /* profile, sign-in state, BU, or canWrite changed */ ; companion object { val TOPIC = Topic.create("Terraducktel session", TdtSessionListener::class.java) } }
@Service(Service.Level.APP) class TdtSession(val scope: CoroutineScope) : Disposable {
    val profile: Profile?; val tokens: TokenManager?; val client: TdtClient?; val bu: String
    fun isSignedIn(): Boolean; fun canWrite(): Boolean; fun uiUrl(): String?
    fun clientOrNull(): TdtClient?          // null unless signed in — the store's gate
    fun requireClient(): TdtClient          // throws IllegalStateException with the VS Code messages
    fun reload()                            // rebuild from settings (blocking; call off EDT)
    fun setActiveProfile(name: String); fun setBu(slug: String)
    fun signInWithPassword(email: String, password: String); fun signInWithApiKey(key: String); fun signInWithSso(indicator: ProgressIndicator)
    fun signOut()
    companion object { fun getInstance(): TdtSession = service() }
}
object SignInFlow { fun start(project: Project?) }   // UI: mode popup → dialogs → background task; success balloon "signed in to <profile> as <who>"
object ActionUtil { fun runBackground(project: Project?, title: String, cancellable: Boolean = false, block: (ProgressIndicator) -> Unit)  /* Task.Backgroundable; catches ApiError/IOException/IllegalStateException → error balloon in group "Terraducktel" with the message */
                    fun notify(project: Project?, text: String, type: NotificationType = INFORMATION, vararg actions: Pair<String, () -> Unit>) }
```
- [ ] **Step 1: Failing platform tests**: (a) with a `StubServer` serving `GET /api/v1/workspaces` → `[]` and `GET /api/v1/auth/config`, set a profile pointing at it, `reload()`, `signInWithApiKey("tdt_x")` → `isSignedIn()`, `client.listWorkspaces()` works, request carries `Authorization: Bearer tdt_x`; (b) `setBu("ops")` persists `buByProfile["p"]="ops"` and requests carry `X-Business-Unit: ops`; (c) `signOut()` → `clientOrNull()==null`, PasswordSafe entry gone; (d) `canWrite()`: API key → true; JWT with role viewer → false; operator → true; (e) `TdtSessionListener` fires on sign-in and sign-out (count with a `MessageBusConnection` on the test's disposable). Tests inject an `InMemorySecretStore` — give `TdtSession` an internal `secretStoreFactory` var (default `PasswordSafeSecretStore()`), set by tests; PasswordSafe in tests is in-memory anyway.
- [ ] **Step 2: Implement `TdtSession`** — port `session.ts` `reload()`/`setBu()`/`canWrite()`/`requireClient()` semantics: rebuild `TokenManager` + `TdtClient` per reload; `reloadGen` guard; per-cycle sign-out listener that (when its `tokens` is still current) clears the store (Task 8 wires it — leave a `var onSignedOutHook: (() -> Unit)?`), shows a warning balloon "Terraducktel: session expired — sign in again." with a **Sign in** action, and publishes `sessionChanged`. `trace` callback checks `TdtSettings.getInstance().state.trace` live before calling `TdtLog.trace`. Subscribe to `TdtSettingsListener.TOPIC` → `reload()` on a pooled thread (`ApplicationManager.getApplication().executeOnPooledThread`). Also `sessionChanged` is published (on the EDT via `invokeLater`) after every state change. The SSO flow: `signInWithSso(indicator)` calls `Sso.runLoopbackLogin(buildUrl = client::ssoLoginUrl, openUrl = { BrowserUtil.browse(it); true }, onCancel = { cancel -> cancelSso = cancel; /* poll indicator.isCanceled every 250 ms on scope.launch and call cancel */ })`; a second sign-in invokes the previous `cancelSso` first. `SsoCancelled` is swallowed by the caller (not an error).
- [ ] **Step 3: `SignInFlow` + actions**: `SignInFlow.start(project)`: if no profile → error balloon "Add a profile under Settings → Tools → Terraducktel first." with action **Open settings** (`ShowSettingsUtil.getInstance().showSettingsDialog(project, TdtConfigurable::class.java)`); else run `authConfig()` in background, then on EDT show a `JBPopupFactory.getInstance().createPopupChooserBuilder(listOf(modes…))` (SSO first when `oidc_enabled && cli_loopback == true` and not `AppMode.isRemoteDevHost()`; "Email + password" unless `mode == "oidc"`; "API key (tdt_…)" always; a single option is chosen without the popup). Password: `Messages.showInputDialog(project, "Email", "Sign in to <profile>", null)` then `Messages.showPasswordDialog(project, "Password", "Sign in to <profile>", null)`; API key: `showPasswordDialog(... "API key (tdt_…)" ...)`; SSO: `ActionUtil.runBackground(project, "Terraducktel: complete sign-in in your browser…", cancellable) { session.signInWithSso(it) }`. On success: `ActionUtil.notify(project, "Terraducktel: signed in to <profile> as <email|API key>")`. Actions: `SignInAction`, `SignOutAction` (enabled iff signed in), `SwitchProfileAction` (popup of profile names, checkmark on active; enabled iff ≥1 profile) → `session.setActiveProfile`, `SwitchBusinessUnitAction` (enabled iff signed in and `tokens.kind() != "api_key"`; background `listBusinessUnits()` + `all` when `claims().is_superadmin == true`; popup → `session.setBu`). All actions: `getActionUpdateThread() = BGT`, `update()` reads only in-memory session state.
  `plugin.xml`:
  ```xml
  <actions>
    <group id="Terraducktel.Toolbar">
      <action id="Terraducktel.SignIn" class="com.terraducktel.jetbrains.actions.auth.SignInAction" text="Sign In…" icon="AllIcons.Actions.Execute"/>
      <action id="Terraducktel.SignOut" class="…SignOutAction" text="Sign Out"/>
      <action id="Terraducktel.SwitchProfile" class="…SwitchProfileAction" text="Switch Profile…"/>
      <action id="Terraducktel.SwitchBu" class="…SwitchBusinessUnitAction" text="Switch Business Unit…"/>
    </group>
    <group id="Terraducktel.ToolsMenu" text="Terraducktel" popup="true">
      <add-to-group group-id="ToolsMenu" anchor="last"/>
      <reference ref="Terraducktel.SignIn"/><reference ref="Terraducktel.SignOut"/><reference ref="Terraducktel.SwitchProfile"/><reference ref="Terraducktel.SwitchBu"/>
    </group>
  </actions>
  ```
- [ ] **Step 4: Run `./gradlew test` → all green; commit** `feat(jetbrains): session service, sign-in flow (password, API key, SSO) and auth actions`.

---

### Task 8: Grouping port + Store

**Files:**
- Create: `state/Grouping.kt`, `state/Store.kt`
- Modify: `session/TdtSession.kt` (wire `Store.clear()` into the sign-out hook; `reload()` restarts the store)
- Test: `src/test/.../state/GroupingTest.kt`, `src/test/.../state/StoreTest.kt`

**Interfaces:**
- Produces:
```kotlin
enum class Cloud { AWS, AZURE, GCP, OTHER }
data class Classification(val cloud: Cloud, val key: String, val label: String, val region: String)
data class PathSegments(val folders: List<String>, val leaf: String)
class FolderNode(val name: String) { val folders = sortedMapOf<String, FolderNode>(); val workspaces = mutableListOf<Pair<Workspace, String>>() /* ws to leaf */ }
data class RegionGroup(val region: String, val root: FolderNode, val count: Int)
data class CloudGroup(val cloud: Cloud, val key: String, val label: String, val regions: List<RegionGroup>, val count: Int)
object Grouping { fun classify(ws: Workspace): Classification; fun workspacePathSegments(ws: Workspace): PathSegments; fun buildTree(workspaces: List<Workspace>): List<CloudGroup>; fun countNode(n: FolderNode): Int }
@Service(Service.Level.APP) class Store(val scope: CoroutineScope) : Disposable {
    @Volatile var workspaces: List<Workspace>; @Volatile var runs: List<Run>; @Volatile var lastError: Throwable?; @Volatile var consecutiveFailures: Int
    fun runsFor(wsId: String): List<Run>; fun workspace(id: String): Workspace?; fun run(id: String): Run?
    fun refresh(): Job         // single-flight; stale-scope discard when the session's client changes mid-fetch (port doRefresh MAX_RETRIES=5)
    fun refreshAndWait()       // blocking helper for tests/actions off the EDT
    fun clear(); fun setActive(active: Boolean); fun isActive(): Boolean
    fun start(intervalMs: Long); fun stop()   // loopEpoch semantics from store.ts; ≥3 failures → 5-minute interval
    fun addListener(parent: Disposable, l: () -> Unit)     // fired on a pooled thread after every change; UI marshals to EDT
    companion object { fun getInstance(): Store = service() }
}
```
Runs sorted newest first by `created_at` (string compare, nulls as ""). Store's client getter is `TdtSession.getInstance().clientOrNull()`; `runsLimit` read live from settings.
- [ ] **Step 1: Failing tests**: `GroupingTest` — port **every** case in `services/vscode/test/unit/grouping.test.ts` (classify ×5, workspacePathSegments ×3, buildTree ×2 — read the file; same inputs, same expected outputs; keep the fixture helper `ws(name, …)`). `StoreTest` — port `test/unit/store.test.ts`: single in-flight refresh (two concurrent `refresh()` → one `/workspaces` request); failure keeps the last snapshot and bumps `consecutiveFailures`; `stop()` during an in-flight tick prevents re-arming (use `intervalMs = 20`, count requests after stop + 200 ms sleep); a client swap mid-fetch discards the stale response (stub with a `CountDownLatch`-delayed response; swap by changing the session's profile URL between request start and response). For the Store tests use a fake client provider: give `Store` an internal `var clientProvider: () -> TdtClient?` defaulting to the session, overridable in tests (plain JVM tests can construct `Store(CoroutineScope(Dispatchers.IO + SupervisorJob()))` directly).
- [ ] **Step 2: Implement** — `Grouping.kt` is a mechanical port of `grouping.ts` (keep the `other:<top>` classification and the region/`account-` stripping rules; folder collision rule: a leaf whose name collides with a sibling folder is attached to that folder). `Store.refresh()` = `scope.launch(Dispatchers.IO)` guarded by a `Mutex.tryLock`-style `AtomicReference<Job?>`; `start()` launches a loop `while (isActive) { delay(interval); if (active) refreshAndWait() }` stored in a `loopJob`, `stop()` cancels it (this is the coroutine equivalent of `loopEpoch`; a fresh `start()` after `stop()` must not double-poll — assert in the test).
- [ ] **Step 3: Wire into `TdtSession`**: `reload()` ends with `Store.getInstance().stop(); start(refreshIntervalSeconds * 1000L); refresh()`; the per-cycle sign-out hook calls `Store.getInstance().clear()`; `setBu()` refreshes the store; `signOut()` clears it. Run `./gradlew test` → green.
- [ ] **Step 4: Commit** `feat(jetbrains): workspace grouping port and polling store`.

---

### Task 9: Tool window with Workspaces and Runs trees

**Files:**
- Create: `toolwindow/TdtToolWindowFactory.kt`, `toolwindow/TreePanel.kt` (shared tree plumbing), `toolwindow/WorkspacesPanel.kt`, `toolwindow/RunsPanel.kt`, `toolwindow/nodes/TdtNode.kt` (base `SimpleNode` with stable `id`), `nodes/CloudGroupNode.kt`, `RegionNode.kt`, `FolderNode.kt`, `WorkspaceNode.kt`, `RunNode.kt`, `StepNode.kt`, `MessageNode.kt`, `toolwindow/TreeIcons.kt`
- Modify: `plugin.xml` (`<toolWindow id="Terraducktel" anchor="left" icon="/icons/tdt.svg" factoryClass="…TdtToolWindowFactory"/>`, groups `Terraducktel.WorkspaceMenu`, `Terraducktel.RunMenu` — empty for now, filled by Tasks 10–11)
- Test: `src/test/.../toolwindow/TreesTest.kt`

**Interfaces:**
- Produces: `TdtDataKeys.WORKSPACE: DataKey<Workspace>`, `TdtDataKeys.RUN: DataKey<Run>` (in `toolwindow/TdtDataKeys.kt`) — the tree panels implement `DataProvider` (`uiDataSnapshot`) so context-menu actions receive the selected workspace/run; `RunsPanel.pendingApprovals(): Int`; `TreePanel.revealWorkspace(id: String)`.
- [ ] **Step 1: Failing platform test**: with a fake store snapshot (set `Store.workspaces`/`runs` directly — make the setters internal), `WorkspacesPanel(project, disposable).rebuild()` produces root children `[aws:123456789012]` → region `eu-west-1` → leaf `vpc`; an unlinked `cloudflare/tenant-home/dns` workspace appears under a group labelled `cloudflare`; `RunsPanel` orders `awaiting_approval` before `running` before `applied`, newest first within a status, and `pendingApprovals()==1`; a signed-out session renders a single `MessageNode("Sign in to Terraducktel")`; `Store.lastError` renders a warning `MessageNode` at the top.
- [ ] **Step 2: Implement** — `TreePanel`: `SimpleTreeStructure` whose root `TdtNode` builds children from the store snapshot; `StructureTreeModel(structure, null, disposable)` + `AsyncTreeModel(model, disposable)` on a `Tree`; `TreeUtil.installActions`; `PopupHandler.installPopupMenu(tree, "<group id>", "TerraducktelTree")`; `tree.isRootVisible = false`; `TdtNode.getEqualityObjects()` returns the stable id so `invalidateAsync()` keeps expansion. Both panels register `Store.addListener` + `TdtSessionListener` subscriptions → `model.invalidateAsync()` (`rebuild()` for tests). Rows (`update(PresentationData)`): CloudGroup `label (count)` with icon `AllIcons.Nodes.Folder` / per-cloud icons from `TreeIcons` (use `AllIcons.Nodes.*` only — no new colours); Region `region (count)`; Folder; Workspace: text `name`, secondary `[drift]` when `drift_status` is `drifted`, `· repo_ref`, icon by last run status (`AllIcons.RunConfigurations.TestPassed` applied, `TestFailed` failed, `TestPaused` awaiting_approval, `TestIgnored` cancelled, `AllIcons.Process.Step_1` running/planning, `AllIcons.Nodes.Module` none), tooltip with id / tf_working_dir / repo / environment / tags; children = `store.runsFor(ws.id)` → `RunNode`s. Run row: `<workspace name> · <command>` + secondary `<status> · <8-char id> · <created_at>`; children = `StepNode`s fetched lazily on a pooled thread via `client.getSteps(id, includeOutput = false)` (cache per run once status ∈ `TERMINAL_RUN_STATUSES`; show `MessageNode("loading…")` until fetched, then invalidate that node). Runs panel sort: rank `awaiting_approval`=0; `pending/running/planning/applying`=1; `planned/applied/failed/cancelled`=2; unknown=3; then `created_at` desc.
  `TdtToolWindowFactory.createToolWindowContent` creates two `Content`s ("Workspaces", "Runs") via `ContentFactory.getInstance()`, sets the toolbar (`ActionManager.getInstance().getAction("Terraducktel.Toolbar")` plus a `RefreshAction` you add to that group in this task: `Store.getInstance().refresh()`), and updates the Runs tab title to `Runs · N` when `N > 0` on every store change (EDT). `setActive`: `ToolWindowManagerListener.stateChanged` / `toolWindow.isVisible` → `Store.getInstance().setActive(anyVisible)` (track per project in a static counter). `toolWindow.setAvailable(true)`.
- [ ] **Step 3: Run tests → green; commit** `feat(jetbrains): Terraducktel tool window with Workspaces and Runs trees`.

---

### Task 10: Workspace actions + run console tailing

**Files:**
- Create: `output/RunTail.kt`, `output/RunConsoles.kt`, `actions/workspace/PlanAction.kt`, `ApplyAction.kt`, `DestroyAction.kt`, `SetBranchAction.kt`, `SyncWorkspaceAction.kt`, `OpenInBrowserAction.kt`, `CopyIdAction.kt`, `actions/workspace/RunCommandActionBase.kt`, `actions/run/WatchRunAction.kt`
- Modify: `plugin.xml` (fill `Terraducktel.WorkspaceMenu`; add Watch run to toolbar and `Terraducktel.RunMenu`)
- Test: `src/test/.../output/RunTailTest.kt`

**Interfaces:**
- Produces:
```kotlin
interface LineSink { fun appendLine(line: String) }
object RunTail { fun tail(client: TdtClient, runId: String, sink: LineSink, pollMs: Long = 2000, isCancelled: () -> Boolean = { false }, timeoutMs: Long = 3 * 3600_000): Run }
@Service(Service.Level.PROJECT) class RunConsoles(val project: Project) : Disposable {
    fun watch(runId: String, title: String, onLanded: ((Run) -> Unit)? = null)   // opens/reveals a console content tab "Run <8 chars> · <title>" in the Terraducktel tool window; Task.Backgroundable "TDT: watching <title>" cancellable; re-attach prints the re-attached divider
}
object RunActions { fun watch(project: Project, run: Run); fun announceAwaiting(project: Project, run: Run) /* balloon "TDT: <ws> <command> is awaiting approval." with Show plan / Approve… — Task 11 wires the actions; keep a `var onAwaitingHook: ((Run) -> Unit)?` for Plan 2's markSeen */ }
```
- [ ] **Step 1: Failing `RunTailTest`** — port the three cases of `test/unit/runOutput.test.ts` (read it): since-cursor stays on the first unfinished step, headers printed once, finished output not repeated, terminal flush prints `── run planned`; cancellation returns the last run; nothing appended after the cancel flag flips.
- [ ] **Step 2: Implement `RunTail`** as a line-for-line port of `tailRun` (`Thread.sleep(pollMs)` between polls; check `isCancelled()` at the same points).
- [ ] **Step 3: `RunConsoles`** — `TextConsoleBuilderFactory.getInstance().createBuilder(project).console` in a new `Content` (closeable, `content.setDisposer(console)`) added to the "Terraducktel" tool window and selected; `LineSink` prints `line + "\n"` with `ConsoleViewContentType.NORMAL_OUTPUT` (`ERROR_OUTPUT` for lines starting with `✕`). Map `runId → (content, active, cancelFlag)`; `dispose()` cancels every tail before disposing.
- [ ] **Step 4: Actions** — `RunCommandActionBase(command)`: `update` visible iff a `WORKSPACE` is in the data context and `session.canWrite()`; `actionPerformed`: Destroy first asks `Messages.showInputDialog(project, "Type the workspace name (<name>) to confirm destroy", "Destroy <name>", Messages.getWarningIcon())` and aborts unless equal; then `ActionUtil.runBackground("TDT: <command> <name>") { run = client.triggerRun(ws.id, TriggerRunBody(command)); store.refreshAndWait(); invokeLater { RunActions.watch(project, run) } }`. `RunActions.watch` = `RunConsoles.getInstance(project).watch(run.id, wsName) { landed -> store.refresh(); if landed.status == "awaiting_approval" announceAwaiting(project, landed) else if "failed" error balloon "TDT: <ws> <command> failed — see the run output." }`. `SetBranchAction`: background `listBranches` → popup (default branch marked) → `updateWorkspace(id, branch)` → refresh. `SyncWorkspaceAction` → `syncWorkspace` → refresh → balloon "Sync requested". `OpenInBrowserAction` (workspace → `<uiUrl>/`, run → `<uiUrl>/runs/<id>`, `BrowserUtil.browse`). `CopyIdAction` → `CopyPasteManager.getInstance().setContents(StringSelection(id))`. `WatchRunAction`: with a `RUN` in context → watch it; otherwise (toolbar/Tools menu) a popup of `store.runs` labelled `<ws> · <command> — <status> · <8 chars>` → watch.
  `plugin.xml` menus: `Terraducktel.WorkspaceMenu` = Plan, Apply, Destroy…, separator, Set Tracked Branch…, Sync From Repo, separator, Open in Browser, Copy Id. `Terraducktel.RunMenu` = Watch Run, Open in Browser, Copy Id (Task 11 adds the rest). Add `Terraducktel.WatchRun` to `Terraducktel.Toolbar` and the Tools submenu.
- [ ] **Step 5: `./gradlew test buildPlugin` → green; commit** `feat(jetbrains): plan/apply/destroy, branch pin, sync, and run console tailing`.

---

### Task 11: Plan document, approve / reject / cancel

**Files:**
- Create: `output/PlanDocument.kt`, `actions/run/ShowPlanAction.kt`, `ApproveAction.kt`, `RejectAction.kt`, `CancelRunAction.kt`
- Modify: `plugin.xml` (`Terraducktel.RunMenu`: Show Plan, Approve…, Reject…, Cancel Run before the Task 10 entries), `output/RunConsoles.kt`/`RunActions` (wire Show plan / Approve… on the awaiting balloon)
- Test: `src/test/.../output/PlanDocumentTest.kt`

**Interfaces:**
- Produces: `object PlanDocument { fun open(project: Project, runId: String, label: String) /* background getPlan → EDT open */ ; fun classifyLine(line: String): PlanLineKind /* ADD, DELETE, CHANGE, NONE — port of planDocument.ts decoration rules */ ; fun fileNameFor(label: String, runId: String): String = "<label>-<8 chars>.tfplan.txt" }`; `object Approvals { fun approve(project: Project, run: Run) }` (the modal + POST, reused by Plan 2's notifications).
- [ ] **Step 1: Failing test**: `classifyLine` — port the cases in `test/unit/planDocument.test.ts` (`+ resource` → ADD, `- resource` → DELETE, `~ update` / `-/+` → CHANGE, `#` comments and plain text → NONE, indented forms); `fileNameFor("vpc", "0123456789abcdef")=="vpc-01234567.tfplan.txt"`; platform test: `PlanDocument.openText(project, "x.tfplan.txt", "+ a\n- b\n~ c\n")` opens a read-only editor whose document has 3 line highlighters.
- [ ] **Step 2: Implement** — `LightVirtualFile(name, fileType, text)` with `fileType = FileTypeManager.getInstance().getFileTypeByExtension("tf").takeUnless { it is UnknownFileType || it == PlainTextFileType.INSTANCE } ?: PlainTextFileType.INSTANCE`, `isWritable = false`; open via `FileEditorManager.getInstance(project).openFile(vf, true)`; for the `TextEditor`, add line highlighters through `editor.markupModel.addLineHighlighter(key, line, HighlighterLayer.ADDITIONAL_SYNTAX)` with `DiffColors.DIFF_INSERTED` / `DIFF_DELETED` / `DIFF_MODIFIED`. `open()` fetches `getPlan(runId).plan_output ?: "(no plan output)"` on a background task.
- [ ] **Step 3: Actions** — `ShowPlanAction` (run in context, else popup over `store.runs`). `Approvals.approve`: background `getGraph` (failure → zeros), then EDT `MessageDialogBuilder.yesNoCancel("Approve <command> on <ws>?", "+<add> to add, ~<change> to change, -<destroy> to destroy, ±<replace> to replace.").yesText("Approve").noText("Show plan").cancelText("Cancel").asWarning().show(project)`: YES → background `approve(id)` → balloon "TDT: approved <ws> <command>." → refresh → `RunActions.watch`; NO → `PlanDocument.open`. `ApproveAction`/`RejectAction` visible iff `RUN.status == "awaiting_approval"` and `canWrite()`; `RejectAction` asks `Messages.showInputDialog("Reject <command> on <ws> — reason (optional)")` (null = cancelled) → `reject(id, reason)` → refresh. `CancelRunAction` visible iff `status in CANCELLABLE_RUN_STATUSES` and `canWrite()` → `cancel(id)` → refresh. The Task 10 awaiting balloon gets actions **Show plan** → `PlanDocument.open`, **Approve…** → `Approvals.approve`.
- [ ] **Step 4: `./gradlew test buildPlugin` → green; commit** `feat(jetbrains): plan document and gated approve / reject / cancel`.

---

### Task 12: Docs, Makefile, manual smoke against the local stack

**Files:**
- Create: `services/jetbrains/README.md`, `services/jetbrains/CHANGELOG.md`, `docs/JETBRAINS.md`
- Modify: `Makefile` (`test-jetbrains`, `build-jetbrains`; add `test-jetbrains` to `test`), `CLAUDE.md` repo map row, `docs/ARCHITECTURE.md` clients paragraph (add the JetBrains plugin next to the VS Code sentence, same contract-guard wording)

- [ ] **Step 1: Makefile**
```make
# JetBrains plugin. Needs a JDK ≥ 17 to run Gradle (a JDK 21 toolchain is auto-provisioned). With no
# JAVA_HOME and no java on PATH, fall back to a Toolbox-installed JetBrains Runtime.
JB_JAVA_HOME ?= $(or $(JAVA_HOME),$(shell command -v java >/dev/null 2>&1 || ls -d $$HOME/.local/share/JetBrains/Toolbox/apps/*/jbr 2>/dev/null | head -1))
test-jetbrains:
	cd services/jetbrains && JAVA_HOME=$(JB_JAVA_HOME) ./gradlew --console=plain test
build-jetbrains:
	cd services/jetbrains && JAVA_HOME=$(JB_JAVA_HOME) ./gradlew --console=plain buildPlugin
```
Add both to `.PHONY`; `test: test-api test-cli test-vscode test-jetbrains`.
- [ ] **Step 2: Docs** — `docs/JETBRAINS.md` mirrors `docs/VSCODE.md`'s structure (install from zip: *Settings → Plugins → ⚙ → Install Plugin from Disk…*; profiles in *Settings → Tools → Terraducktel*; sign-in modes incl. SSO caveats; the tool window; actions; troubleshooting: insecure TLS, "Not signed in", trace logging in `idea.log`). `services/jetbrains/README.md`: what it is, build (`make build-jetbrains`), test, layout table, the contract guard. `CHANGELOG.md`: `0.1.0` — the feature list of this plan. `CLAUDE.md` row: `| services/jetbrains/ | JetBrains plugin (2026.1+): same feature set as the VS Code extension — trees, runs, plan document, approvals. Kotlin, no runtime deps; endpoints pinned by api_contract.json. |`.
- [ ] **Step 3: Manual smoke** (record the outcome in the task report — this is the plan's acceptance gate): `make build-jetbrains`; the compose stack is up (`make up`); install the zip into the local PyCharm 2026.2 by unzipping it into `~/.local/share/JetBrains/PyCharm2026.2/plugins/` (create the dir if needed; the zip contains a `terraducktel-jetbrains/lib/*.jar` layout) **or** via *Install Plugin from Disk* if a human is driving; if PyCharm cannot be restarted from this session, run `./gradlew runIde` headless-free is not possible — instead run the full platform test suite and `./gradlew verifyPluginProjectConfiguration`, and state clearly in the report that the in-IDE check is pending. Expected in-IDE: profile `local` → `http://localhost:8001`, password sign-in as `admin@test.com`, trees render, Plan on a workspace opens a console tab with steps, Show plan opens the document, Approve modal shows the graph counts.
- [ ] **Step 4: Commit** `docs(jetbrains): README, changelog, JETBRAINS.md, make targets`.
