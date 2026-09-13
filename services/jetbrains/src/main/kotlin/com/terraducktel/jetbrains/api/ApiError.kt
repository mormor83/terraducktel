package com.terraducktel.jetbrains.api

import kotlinx.serialization.json.*

class ApiError(val status: Int, message: String, val detail: JsonElement? = null) : RuntimeException(message) {
    companion object {
        /** Matches a JSON number literal, e.g. "5", "-3.14", "2e10" — used to tell a genuine
         *  unquoted JSON literal (number/true/false/null) apart from kotlinx's lenient acceptance
         *  of an arbitrary bare word (e.g. "oops") as a top-level JsonLiteral. */
        private val jsonNumberRegex = Regex("""-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?""")
        private fun isValidJsonLiteral(content: String) =
            content == "true" || content == "false" || content == "null" || jsonNumberRegex.matches(content)

        /** Port of client.ts `detailToMessage`: string detail → as is; FastAPI validation list →
         *  "loc: msg; …"; any other successfully-parsed JSON (object without `detail`, array,
         *  quoted string, or a genuine JSON number/true/false/null literal) → "HTTP <status>";
         *  anything that fails to parse — including kotlinx's lenient acceptance of a bare
         *  non-JSON word as a top-level literal — falls back to the raw trimmed text, matching
         *  `JSON.parse` throwing in the TS client. */
        fun fromResponse(status: Int, text: String): ApiError {
            val parsed = runCatching { TdtJson.parseToJsonElement(text) }.getOrNull()
            val notJson = parsed == null || (parsed is JsonPrimitive && !parsed.isString && !isValidJsonLiteral(parsed.content))
            if (notJson) return ApiError(status, text.trim().ifEmpty { "HTTP $status" }, null)
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
