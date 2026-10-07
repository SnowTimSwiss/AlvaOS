package org.alvaos.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.ActivityOptions
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
import kotlinx.coroutines.withContext
import org.alvaos.photos.HubException
import org.alvaos.photos.SyncEngine
import java.util.concurrent.TimeUnit

/** The backup in the background: every hour (on Wi-Fi if chosen), and on "Back up now". */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        const val PERIODIC = "alvaos-backup"
        const val NOW = "alvaos-backup-now"
        const val CHANNEL = "backup"
        const val CHANNEL_ALERTS = "alerts"
        private const val PROGRESS_ID = 1
        private const val DELETE_ID = 2
        private const val SIGNIN_ID = 3

        private fun constraints(store: Store) = Constraints.Builder()
            .setRequiredNetworkType(if (store.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
            .setRequiresBatteryNotLow(true)
            .build()

        fun schedule(context: Context) {
            val store = Store(context)
            val request = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS)
                .setConstraints(constraints(store)).build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, request)
        }

        fun now(context: Context) {
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build()
            WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, request)
        }

        fun stop(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(PERIODIC)
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
        .build()

    override suspend fun getForegroundInfo() =
        ForegroundInfo(PROGRESS_ID, progress(0, 0), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        if (!store.signedIn || !store.backupOn || store.albums.isEmpty()) return@withContext Result.success()
        channels(applicationContext)
        store.pickServer()                    // at home or away: the address that answers now
        val engine = SyncEngine(store.hub(), Gallery(applicationContext), store)
        try {
            val result = engine.run(phoneName = { Build.MODEL }) { done, total ->
                setProgressAsync(workDataOf("done" to done, "total" to total))
                if (total > 0) setForegroundAsync(ForegroundInfo(PROGRESS_ID, progress(done, total),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC))
            }
            store.lastSync = System.currentTimeMillis()
            store.lastMessage = when {
                result.failed.isNotEmpty() -> "${result.failed.size} could not be backed up: ${result.lastError}"
                result.uploaded > 0 -> "${result.uploaded} backed up."
                else -> "Everything is backed up."
            }
            if (result.toDeleteHere.isNotEmpty()) {
                val gallery = Gallery(applicationContext)
                if (gallery.maySilentlyDelete()) {
                    // Allowed to delete without asking: try it now; what Android does not let a
                    // background job do goes, again without asking, the next time the app opens.
                    silentlyDelete(gallery, engine, result.toDeleteHere)
                } else askToDelete(result.toDeleteHere.size)
            }
            if (result.failed.isNotEmpty()) Result.retry() else Result.success()
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
