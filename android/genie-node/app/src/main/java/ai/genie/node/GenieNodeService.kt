package ai.genie.node

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder

/**
 * Foreground service that keeps the device node connected.
 *
 * The node must survive the screen turning off, so it runs as a foreground service with a
 * persistent notification — the same reason any long-lived connection on Android needs one.
 * The notification also doubles as the owner-visible sign that GENIE can reach this device.
 */
class GenieNodeService : Service() {

    companion object {
        const val EXTRA_HOST = "host"
        const val EXTRA_PORT = "port"
        const val EXTRA_DEVICE_ID = "device_id"
        const val EXTRA_CODE = "code"
        private const val CHANNEL_ID = "genie-node"
        private const val NOTIFICATION_ID = 1
    }

    private var node: GenieNode? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val host = intent?.getStringExtra(EXTRA_HOST) ?: "127.0.0.1"
        val port = intent?.getIntExtra(EXTRA_PORT, 8765) ?: 8765
        val deviceId = intent?.getStringExtra(EXTRA_DEVICE_ID) ?: "phone_main"
        val code = intent?.getStringExtra(EXTRA_CODE) ?: ""

        startForeground(NOTIFICATION_ID, buildNotification("connecting to $host:$port"))
        node?.stop()
        node = GenieNode(
            deviceId = deviceId,
            displayName = Build.MODEL ?: "Android",
            host = host,
            port = port,
            capabilities = DeviceCapabilities(applicationContext).all(),
            pairingCode = code,
            platform = "android",
            appVersion = BuildConfig.VERSION_NAME,
        ).also { it.start() }
        return START_STICKY
    }

    override fun onDestroy() {
        node?.stop()
        node = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(
            CHANNEL_ID, "GENIE node", NotificationManager.IMPORTANCE_LOW))
    }

    private fun buildNotification(text: String): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("GENIE node")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
    }
}
