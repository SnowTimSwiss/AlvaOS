package org.alvaos.app

import android.app.ActivityOptions
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.alvaos.photos.HubException
import org.alvaos.photos.SyncEngine
import org.alvaos.photos.SyncResult
import org.alvaos.photos.staleDays
import java.util.concurrent.TimeUnit

/**
 * The backup in the background. Three ways to start it, one sync at a time:
 *  - WATCH: a minute after a new picture or video appeared on the phone (a content trigger);
 *  - PERIODIC: every hour, which also brings what was deleted on the NAS;
 *  - NOW: "Back up now", at once.
 * A sync that Android ends (the ten minutes of a background job) saves what it did and
 * continues by itself right away.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        const val PERIODIC = "alvaos-backup"
        const val NOW = "alvaos-backup-now"
        const val WATCH = "alvaos-backup-watch"
        const val MORE = "alvaos-backup-more"
        const val CHANNEL = "backup"
        const val CHANNEL_ALERTS = "alerts"
        private const val PROGRESS_ID = 1
        private const val DELETE_ID = 2
        private const val SIGNIN_ID = 3
        private const val STALE_ID = 4
        private const val KEY_WATCH = "watch"

        /** One sync at a time, whichever way it was started. */
        private val one = Mutex()

        /** While a sync runs: how many are done of how many (the app shows it live), else null. */
        @Volatile var live: Pair<Int, Int>? = null

        fun constraints(store: Store, network: Boolean = true): Constraints = Constraints.Builder()
            .setRequiredNetworkType(
                when {
                    !network -> NetworkType.NOT_REQUIRED
                    store.wifiOnly -> NetworkType.UNMETERED
                    else -> NetworkType.CONNECTED
                })
            .setRequiresBatteryNotLow(true)
            .setRequiresCharging(store.chargingOnly)
            .build()

        /** The hourly check and the watcher for new pictures; safe to call as often as wanted. */
        fun schedule(context: Context) {
            val store = Store(context)
            val work = WorkManager.getInstance(context)
            val request = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS)
                .setConstraints(constraints(store)).build()
            work.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, request)
            watch(context, again = false)
        }

        /**
         * Waits for a new picture or video (MediaStore changes), then syncs. Each run puts the next
         * one in line (again = true), so it goes on for as long as the backup is on.
         */
        fun watch(context: Context, again: Boolean) {
            val store = Store(context)
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(if (store.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
                .setRequiresBatteryNotLow(true)
                .setRequiresCharging(store.chargingOnly)
                .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
                .setTriggerContentUpdateDelay(30, TimeUnit.SECONDS)      // the camera is still writing
                .setTriggerContentMaxDelay(3, TimeUnit.MINUTES)
                .build()
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf(KEY_WATCH to true))
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                WATCH, if (again) ExistingWorkPolicy.APPEND_OR_REPLACE else ExistingWorkPolicy.KEEP, request)
        }

        fun now(context: Context) {
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build()
            WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, request)
        }

        /** Android ended a long sync: go on with the rest at once, not after a backoff. */
        private fun more(context: Context) {
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(constraints(Store(context))).build()
            WorkManager.getInstance(context).enqueueUniqueWork(MORE, ExistingWorkPolicy.REPLACE, request)
        }

        fun stop(context: Context) {
            val work = WorkManager.getInstance(context)
            for (name in listOf(PERIODIC, WATCH, MORE)) work.cancelUniqueWork(name)
        }

        fun channels(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Backup", NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ALERTS, "Needs you", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    private val store = Store(context)

    private fun progress(done: Int, total: Int) = NotificationCompat.Builder(applicationContext, CHANNEL)
        .setSmallIcon(R.drawable.ic_notify)
        .setContentTitle("Backing up to your NAS")
        .setContentText(if (total > 0) "$done of $total" else "Looking for new pictures…")
        .setProgress(total, done, total == 0)
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .build()

    override suspend fun getForegroundInfo() =
        ForegroundInfo(PROGRESS_ID, progress(0, 0), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val watching = inputData.getBoolean(KEY_WATCH, false)
        try {
            if (!store.signedIn || !store.backupOn || store.albums.isEmpty()) return@withContext Result.success()
            channels(applicationContext)
            val result = one.withLock {
                live = 0 to 0
                try { sync() } finally { live = null }
            }
            warnIfStale()
            result
        } finally {
            // The next wait for a new picture, as long as the backup is on and this phone is signed in.
            if (watching && store.signedIn && store.backupOn) watch(applicationContext, again = true)
        }
    }

    private suspend fun sync(): Result {
        store.pickServer()                    // at home or away: the address that answers now
        val gallery = Gallery(applicationContext)
        val engine = SyncEngine(store.hub(), gallery, store)
        return try {
            val result = engine.run(
                phoneName = { Build.MODEL },
                shouldStop = { isStopped },
            ) { done, total ->
                live = done to total
                setProgressAsync(workDataOf("done" to done, "total" to total))
                if (total > 0) setForegroundAsync(ForegroundInfo(PROGRESS_ID, progress(done, total),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC))
            }
            store.lastSync = System.currentTimeMillis()
            store.lastMessage = message(result)
            if (result.toDeleteHere.isNotEmpty()) {
                if (gallery.maySilentlyDelete()) {
                    // Allowed to delete without asking: try it now; what Android does not let a
                    // background job do goes, again without asking, the next time the app opens.
                    silentlyDelete(gallery, engine, result.toDeleteHere)
                } else askToDelete(result.toDeleteHere.size)
            }
            when {
                // Ended by Android, not by a failure: go on at once.
                result.stopped && result.waiting > 0 -> { more(applicationContext); Result.success() }
                result.failed.isNotEmpty() -> Result.retry()
                else -> Result.success()
            }
        } catch (e: HubException) {
            store.lastMessage = e.message ?: "The NAS said no."
            if (e.signedOut) {
                store.signOut()
                notify(SIGNIN_ID, "Sign in to your NAS again", "The backup is paused until you do.")
                Result.failure()
            } else Result.retry()
        } catch (e: java.io.IOException) {
            store.lastMessage = "The NAS cannot be reached: ${e.message}"
            Result.retry()
        }
    }

    /** No backup for days (the NAS is away, a battery saver stops the app): say so, once a day. */
    private fun warnIfStale() {
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        val now = System.currentTimeMillis()
        val days = staleDays(now, store.lastSync, store.lastWarned)
        if (days == null) {
            if (store.lastSync > 0 && now - store.lastSync < 60 * 60 * 1000L) nm.cancel(STALE_ID)
            return
        }
        store.lastWarned = now
        notify(STALE_ID, "No backup for $days days",
            "Your pictures are safe on the phone, but not backed up. Open AlvaOS to see why: ${store.lastMessage}")
    }

    private fun message(r: SyncResult) = when {
        r.stopped -> "Paused, it goes on by itself."
        r.failed.isNotEmpty() && r.uploaded == 0 && r.lastError.isNotEmpty() -> "Waiting for the NAS: ${r.lastError}"
        r.failed.isNotEmpty() -> "${r.failed.size} could not be backed up yet: ${r.lastError}"
        r.uploaded > 0 -> "${r.uploaded} backed up."
        else -> "Everything is backed up."
    }

    private suspend fun silentlyDelete(gallery: Gallery, engine: SyncEngine, ids: Set<String>) {
        try {
            val request = MediaStore.createDeleteRequest(applicationContext.contentResolver, ids.map { gallery.uri(it) })
            val options = if (Build.VERSION.SDK_INT >= 34) ActivityOptions.makeBasic()
                .setPendingIntentBackgroundActivityStartMode(ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED)
                .toBundle() else null
            request.send(applicationContext, 0, null, null, null, null, options)
            delay(5000)
        } catch (e: Exception) {
            return
        }
        val gone = ids - gallery.existing(ids)
        if (gone.isNotEmpty()) engine.deletedHere(gone)
    }

    private fun askToDelete(count: Int) =
        notify(DELETE_ID, "$count deleted on your NAS", "Tap to delete ${if (count == 1) "it" else "them"} on this phone too.")

    private fun notify(id: Int, title: String, text: String) {
        val open = PendingIntent.getActivity(applicationContext, id,
            Intent(applicationContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(applicationContext, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_notify).setContentTitle(title).setContentText(text)
            .setContentIntent(open).setAutoCancel(true).build()
        try {
            applicationContext.getSystemService(NotificationManager::class.java).notify(id, n)
        } catch (e: SecurityException) {
            // No permission for notifications: the app shows it when opened.
        }
    }
}
