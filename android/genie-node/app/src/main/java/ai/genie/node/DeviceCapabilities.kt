package ai.genie.node

import android.content.Context
import android.media.AudioManager
import org.json.JSONObject
import java.io.File

/**
 * The capabilities this node offers.
 *
 * Every one performs a real effect and reports whether it could be **observed afterwards** —
 * `verified` is the only claim the daemon accepts as success. A capability that cannot verify
 * itself must return `verified = false`, never `true`.
 *
 * Scope (docs/DEVICES.md): app launch, media control, notifications, screen observation,
 * touch/a11y actions, keyboard, mic, camera, file exchange, device status.
 * The entries below are the ones that need no runtime permission prompt; the permission-gated
 * ones (camera, mic, notifications, a11y) are registered only once the owner has granted them.
 */
class DeviceCapabilities(private val context: Context) {

    fun all(): List<DeviceCapability> = listOf(
        MediaControl(context),
        DeviceInfo(),
        FileExchange(context),
        VolumeControl(context),
    )

    /** Media transport via the platform AudioManager key events. */
    class MediaControl(private val context: Context) : DeviceCapability {
        override val name = "media.next"

        override fun run(params: JSONObject): CapabilityResult {
            val action = params.optString("action", "next")
            val key = when (action) {
                "next" -> android.view.KeyEvent.KEYCODE_MEDIA_NEXT
                "previous" -> android.view.KeyEvent.KEYCODE_MEDIA_PREVIOUS
                "pause" -> android.view.KeyEvent.KEYCODE_MEDIA_PAUSE
                "play" -> android.view.KeyEvent.KEYCODE_MEDIA_PLAY
                else -> return CapabilityResult(false, false, "unknown media action $action",
                    errorCode = "bad_parameter")
            }
            val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val sent = try {
                audio.dispatchMediaKeyEvent(android.view.KeyEvent(
                    android.view.KeyEvent.ACTION_DOWN, key))
                audio.dispatchMediaKeyEvent(android.view.KeyEvent(
                    android.view.KeyEvent.ACTION_UP, key))
                true
            } catch (exc: Exception) {
                false
            }
            // The platform gives no read-back for a media key, so this cannot claim verification.
            return CapabilityResult(sent, false,
                if (sent) "media key $action dispatched (not independently verifiable)"
                else "media key dispatch failed",
                errorCode = if (sent) "" else "media_dispatch_failed")
        }
    }

    /** Device status — always verifiable, it is a read of real state. */
    class DeviceInfo : DeviceCapability {
        override val name = "device.info"

        override fun run(params: JSONObject): CapabilityResult {
            val data = JSONObject().apply {
                put("platform", "android")
                put("release", android.os.Build.VERSION.RELEASE)
                put("sdk", android.os.Build.VERSION.SDK_INT)
                put("model", android.os.Build.MODEL)
                put("manufacturer", android.os.Build.MANUFACTURER)
            }
            return CapabilityResult(true, true, "android ${android.os.Build.VERSION.RELEASE}",
                data)
        }
    }

    /** File exchange inside the app's own sandbox — no shared-storage permission needed. */
    class FileExchange(private val context: Context) : DeviceCapability {
        override val name = "files.write"

        override fun run(params: JSONObject): CapabilityResult {
            val relative = params.optString("path")
            if (relative.isEmpty()) {
                return CapabilityResult(false, false, "path is required",
                    errorCode = "bad_parameter")
            }
            val text = params.optString("text")
            // resolve inside the app sandbox; never escape it
            val root = File(context.filesDir, "exchange").apply { mkdirs() }
            val target = File(root, relative)
            val canonicalRoot = root.canonicalPath
            if (!target.canonicalPath.startsWith(canonicalRoot)) {
                return CapabilityResult(false, false, "path escapes the node sandbox",
                    errorCode = "path_denied")
            }
            return try {
                target.parentFile?.mkdirs()
                target.writeText(text)
                val readBack = target.readText()
                CapabilityResult(readBack == text, readBack == text,
                    "wrote ${text.length} chars to ${target.name}",
                    JSONObject().put("path", target.name).put("bytes", text.toByteArray().size))
            } catch (exc: Exception) {
                CapabilityResult(false, false, exc.message ?: "write failed",
                    errorCode = "io_error")
            }
        }
    }

    /** Volume control with a real read-back, so it can verify itself. */
    class VolumeControl(private val context: Context) : DeviceCapability {
        override val name = "system.volume.set"

        override fun run(params: JSONObject): CapabilityResult {
            val level = params.optInt("level", -1)
            if (level !in 0..100) {
                return CapabilityResult(false, false, "level must be 0-100",
                    errorCode = "bad_parameter")
            }
            val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
            val target = Math.round(level / 100f * max)
            return try {
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0)
                val actual = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
                val verified = actual == target
                CapabilityResult(verified, verified,
                    "volume set to $level% (stream=$actual/$max)",
                    JSONObject().put("volume", actual))
            } catch (exc: Exception) {
                CapabilityResult(false, false, exc.message ?: "volume failed",
                    errorCode = "audio_error")
            }
        }
    }
}
