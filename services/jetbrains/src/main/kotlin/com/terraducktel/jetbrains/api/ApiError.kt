package com.terraducktel.jetbrains.api

import kotlinx.serialization.json.*

class ApiError(val status: Int, message: String, val detail: JsonElement? = null) : RuntimeException(message) {
    companion object {
        /** Port of client.ts `detailToMessage`: string detail → as is; FastAPI validation list → "loc: msg; …"; else "HTTP <status>". */
        fun fromResponse(status: Int, text: String): ApiError {
            val parsed = runCatching { TdtJson.parseToJsonElement(text) }.getOrNull() as? JsonObject
                ?: return ApiError(status, text.trim().ifEmpty { "HTTP $status" }, null)
            val detail = parsed["detail"]
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
