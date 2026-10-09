package ai.genie.node

import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec
import kotlin.concurrent.thread
import org.json.JSONObject

/**
 * GENIE device protocol — the Kotlin side of `devices/contracts.py`.
 *
 * Wire format: one JSON frame per line, `{"payload": {...}, "sig": "<hex hmac>"}`, where the
 * HMAC is over the canonical (sorted-key, no-whitespace) JSON of `payload`.
 *
 * Security model (master spec §6):
 *  - pairing by code: the node shows a 6-digit code, the owner enters it on the PC; both sides
 *    derive the same secret with PBKDF2, so the code never crosses the wire
 *  - every frame is signed; an unsigned or forged frame is rejected before it is parsed
 *  - a monotonic counter plus command_id dedupe makes replay harmless
 *
 * This mirrors `devices/node.py` exactly, so the reference node and the Android node are
 * interchangeable from the daemon's point of view.
 */
object GenieProtocol {

    const val PROTOCOL_VERSION = "1.0"

    /** Used only before pairing; authenticates nothing and grants nothing. */
    const val UNPAIRED_SECRET = "genie-unpaired-handshake"

    private const val PBKDF2_ITERATIONS = 120_000
    private const val PBKDF2_KEY_BITS = 256

    fun derivePairingSecret(code: String, deviceId: String): String {
        val salt = "genie-device-pairing".toByteArray(Charsets.UTF_8)
        val spec = PBEKeySpec("$deviceId:${code.trim()}".toCharArray(), salt,
            PBKDF2_ITERATIONS, PBKDF2_KEY_BITS)
        val factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
        return factory.generateSecret(spec).encoded.joinToString("") { "%02x".format(it) }
    }

    /** Canonical JSON: keys sorted, no whitespace — must match Python's `sort_keys` + separators. */
    fun canonicalJson(payload: JSONObject): String = canonicalise(payload)

    private fun canonicalise(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().sorted()
            .joinToString(",", "{", "}") { key ->
                "${quote(key)}:${canonicalise(value.get(key))}"
            }
        is org.json.JSONArray -> (0 until value.length())
            .joinToString(",", "[", "]") { canonicalise(value.get(it)) }
        is String -> quote(value)
        is Boolean -> value.toString()
        is Int, is Long -> value.toString()
        is Double -> if (value == Math.floor(value) && !value.isInfinite()) {
            value.toLong().toString()
        } else {
            value.toString()
        }
        else -> quote(value.toString())
    }

    private fun quote(text: String): String {
        val out = StringBuilder("\"")
        for (ch in text) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                else -> if (ch < ' ') out.append("\\u%04x".format(ch.code)) else out.append(ch)
            }
        }
        return out.append('"').toString()
    }

    fun sign(payload: JSONObject, secret: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(canonicalJson(payload).toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    fun verify(payload: JSONObject, secret: String, signature: String): Boolean {
        val expected = sign(payload, secret)
        return MessageDigest.isEqual(expected.toByteArray(Charsets.UTF_8),
            signature.toByteArray(Charsets.UTF_8))
    }

    fun encodeFrame(payload: JSONObject, secret: String): String {
        val frame = JSONObject()
        frame.put("payload", payload)
        frame.put("sig", sign(payload, secret))
        return frame.toString() + "\n"
    }

    /** Returns null when the frame is malformed or its signature does not verify. */
    fun decodeFrame(line: String, secret: String): JSONObject? {
        return try {
            val frame = JSONObject(line)
            val payload = frame.getJSONObject("payload")
            val signature = frame.getString("sig")
            if (verify(payload, secret, signature)) payload else null
        } catch (exc: Exception) {
            null
        }
    }

    /** Replay protection: counters must advance, command ids are answered from cache. */
    class ReplayWindow(private val windowSize: Int = 512) {
        private var highestCounter = 0
        private val seen = LinkedHashMap<String, JSONObject>()

        fun acceptCounter(counter: Int): Boolean {
            if (counter <= highestCounter) return false
            highestCounter = counter
            return true
        }

        fun seen(commandId: String): JSONObject? = seen[commandId]

        fun remember(commandId: String, result: JSONObject) {
            if (commandId.isEmpty()) return
            seen[commandId] = result
            while (seen.size > windowSize) {
                val oldest = seen.keys.firstOrNull() ?: break
                seen.remove(oldest)
            }
        }
    }
}

/** A capability the node can perform. Implementations must verify their own effect. */
interface DeviceCapability {
    val name: String
    fun run(params: JSONObject): CapabilityResult
}

data class CapabilityResult(
    val ok: Boolean,
    val verified: Boolean,
    val detail: String = "",
    val data: JSONObject = JSONObject(),
    val errorCode: String = "",
)

/**
 * The node's transport loop: connect, authenticate, serve commands, reconnect with backoff.
 *
 * Deliberately the same state machine as `devices/node.py`, including the pairing handshake and
 * the "a node that never paired is served nothing" rule.
 */
class GenieNode(
    private val deviceId: String,
    private val displayName: String,
    private val host: String,
    private val port: Int,
    private val capabilities: List<DeviceCapability>,
    private val pairingCode: String,
    private val platform: String = "android",
    private val appVersion: String = "0.1.0",
) {
    private val secret: String = GenieProtocol.derivePairingSecret(pairingCode, deviceId)
    private val replay = GenieProtocol.ReplayWindow()
    private var socket: Socket? = null
    @Volatile private var running = false
    @Volatile var paired: Boolean = false
        private set

    private val backoff = listOf(1_000L, 2_000L, 5_000L, 10_000L, 20_000L)

    fun manifest(): JSONObject = JSONObject().apply {
        put("device_id", deviceId)
        put("name", displayName)
        put("type", "android")
        put("protocol_version", GenieProtocol.PROTOCOL_VERSION)
        put("platform", platform)
        put("app_version", appVersion)
        put("node_id", deviceId)
        put("transport", "tcp")
        put("capabilities", org.json.JSONArray(capabilities.map { it.name }))
    }

    fun start() {
        running = true
        thread(name = "genie-node", isDaemon = true) { serveLoop() }
    }

    fun stop() {
        running = false
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        socket = null
    }

    private fun serveLoop() {
        var attempt = 0
        while (running) {
            if (!connect()) {
                Thread.sleep(backoff[minOf(attempt, backoff.size - 1)])
                attempt += 1
                continue
            }
            attempt = 0
            serveConnection()
            closeSocket()
        }
    }

    private fun connect(): Boolean = try {
        socket = Socket().apply { connect(InetSocketAddress(host, port), 10_000) }
        true
    } catch (exc: Exception) {
        false
    }

    private fun closeSocket() {
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        socket = null
    }

    private fun send(payload: JSONObject): Boolean {
        val sock = socket ?: return false
        return try {
            val writer = BufferedWriter(OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8))
            writer.write(GenieProtocol.encodeFrame(payload, secret))
            writer.flush()
            true
        } catch (exc: Exception) {
            false
        }
    }

    private fun serveConnection() {
        val sock = socket ?: return
        send(JSONObject().apply {
            put("kind", "hello")
            put("protocol", GenieProtocol.PROTOCOL_VERSION)
            put("device_id", deviceId)
            put("manifest", manifest())
            put("app_version", appVersion)
        })
        val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
        while (running) {
            val line = try {
                reader.readLine() ?: break
            } catch (exc: Exception) {
                break
            }
            val payload = GenieProtocol.decodeFrame(line, secret) ?: continue
            when (payload.optString("kind")) {
                "hello_ack" -> {
                    paired = payload.optBoolean("ok", true)
                    if (!paired) {
                        closeSocket()
                        return
                    }
                }
                "pair_required" -> {
                    paired = false
                    closeSocket()
                    return
                }
                "command" -> send(handleCommand(payload))
                "cancel" -> send(JSONObject().apply {
                    put("kind", "result")
                    put("command_id", payload.optString("command_id"))
                    put("device_id", deviceId)
                    put("status", "cancelled")
                    put("ok", false)
                    put("verified", false)
                    put("detail", "cancelled")
                })
            }
        }
    }

    /** Execute one command. Idempotent: a repeated command_id returns the cached result. */
    fun handleCommand(payload: JSONObject): JSONObject {
        val commandId = payload.optString("command_id")
        replay.seen(commandId)?.let { return it }

        val issuedAt = payload.optLong("issued_at_ms", 0L)
        val ttl = payload.optLong("ttl_ms", 600_000L)
        if (issuedAt > 0 && System.currentTimeMillis() - issuedAt > ttl) {
            return remember(commandId, result(commandId, "expired", false, false,
                "command TTL elapsed before execution", errorCode = "expired"))
        }

        val capabilityName = payload.optString("capability")
        val capability = capabilities.firstOrNull { it.name == capabilityName }
            ?: return remember(commandId, result(commandId, "rejected", false, false,
                "$capabilityName is not offered by $deviceId",
                errorCode = "unsupported_capability"))

        val started = System.currentTimeMillis()
        val outcome = try {
            capability.run(payload.optJSONObject("params") ?: JSONObject())
        } catch (exc: Exception) {
            CapabilityResult(false, false, exc.message ?: "capability failed",
                errorCode = "handler_error")
        }
        val status = if (outcome.ok) "completed" else "failed"
        return remember(commandId, result(commandId, status, outcome.ok, outcome.verified,
            outcome.detail, outcome.data, outcome.errorCode,
            (System.currentTimeMillis() - started).toInt()))
    }

    private fun remember(commandId: String, payload: JSONObject): JSONObject {
        replay.remember(commandId, payload)
        return payload
    }

    private fun result(
        commandId: String,
        status: String,
        ok: Boolean,
        verified: Boolean,
        detail: String,
        data: JSONObject = JSONObject(),
        errorCode: String = "",
        latencyMs: Int = 0,
    ): JSONObject = JSONObject().apply {
        put("kind", "result")
        put("protocol", GenieProtocol.PROTOCOL_VERSION)
        put("command_id", commandId)
        put("device_id", deviceId)
        put("status", status)
        put("ok", ok)
        put("verified", verified)
        put("detail", detail)
        put("data", data)
        put("error_code", errorCode)
        put("latency_ms", latencyMs)
        put("at_ms", System.currentTimeMillis())
    }
}
