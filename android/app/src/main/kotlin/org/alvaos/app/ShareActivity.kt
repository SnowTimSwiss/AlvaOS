package org.alvaos.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.alvaos.photos.HubException
import org.alvaos.photos.Upload
import org.alvaos.photos.fileNameOf
import org.alvaos.photos.freeName
import java.io.File
import java.io.InputStream
import com.google.android.material.R as M

/**
 * "Share" from any app (a picture, a PDF, a few files) lands here: choose a shared folder, and the
 * files go into its folder "From phone" (made if it is not there; a name already taken gets a number).
 * It stays open while it uploads; the files are the phone's own, the NAS only gets copies.
 */
class ShareActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var ui: Ui
    private lateinit var frame: FrameLayout
    private var uris: List<Uri> = emptyList()
    private var working = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        ui = Ui(this)
        frame = FrameLayout(this).apply { setBackgroundColor(ui.color(M.attr.colorSurface)) }
        ViewCompat.setOnApplyWindowInsetsListener(frame) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, bars.top, 0, bars.bottom)
            insets
        }
        setContentView(frame)
        uris = sharedUris(intent)
        when {
            !store.signedIn -> message("Sign in first", "Open AlvaOS once and sign in to your NAS, then share again.")
            uris.isEmpty() -> message("Nothing to save", "AlvaOS can save files and pictures. It cannot take plain text.")
            else -> choose()
        }
    }

    @Suppress("DEPRECATION")
    private fun sharedUris(intent: Intent): List<Uri> {
        val found = mutableListOf<Uri>()
        when (intent.action) {
            Intent.ACTION_SEND -> (intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))?.let { found += it }
            Intent.ACTION_SEND_MULTIPLE ->
                intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let { found += it }
        }
        val clip = intent.clipData
        if (found.isEmpty() && clip != null) for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { found += it }
        return found.distinct().take(200)
    }

    private fun screen(title: String): LinearLayout {
        frame.removeAllViews()
        return ui.screen(frame, title, back = { if (!working) finish() })
    }

    private fun message(head: String, text: String) {
        val col = screen("Save to AlvaOS")
        ui.headline(col, head)
        ui.body(col, text)
        ui.button(col, "Close", Ui.Kind.Outlined, top = 20) { finish() }
    }

    private fun choose() {
        val col = screen("Save to AlvaOS")
        ui.headline(col, if (uris.size == 1) "Save 1 file" else "Save ${uris.size} files")
        ui.caption(col, "Choose a shared folder. The files go into its folder “From phone”.")
        ui.progress(col, 0, 1, top = 16, indeterminate = true)
        Thread {
            val shares = try {
                store.pickServer()
                store.hub().me().shares.filter { it.access == "write" }
            } catch (e: Exception) {
                runOnUiThread { failed(e) }
                return@Thread
            }
            runOnUiThread { listShares(shares.map { it.name }) }
        }.start()
    }

    private fun listShares(names: List<String>) {
        val col = screen("Save to AlvaOS")
        ui.headline(col, if (uris.size == 1) "Save 1 file" else "Save ${uris.size} files")
        if (names.isEmpty()) {
            ui.body(col, "You may not add files to any shared folder on this NAS. Ask whoever runs it.")
            ui.button(col, "Close", Ui.Kind.Outlined, top = 20) { finish() }
            return
        }
        ui.caption(col, "Choose a shared folder. The files go into its folder “From phone”.")
        val group = ui.group(col, 14)
        names.forEach { name ->
            ui.item(group, R.drawable.ic_tab_files, name, "From phone") { upload(name) }
        }
    }

    private fun failed(e: Exception) {
        working = false
        val col = screen("Save to AlvaOS")
        ui.headline(col, "That did not work")
        ui.body(col, if (e is HubException && e.status == 401) "You are signed out. Open AlvaOS and sign in again."
            else (e.message ?: "The NAS could not be reached."))
        ui.button(col, "Try again", top = 20) { choose() }
        ui.button(col, "Close", Ui.Kind.Text) { finish() }
    }

    private fun upload(share: String) {
        working = true
        progress(0, uris.size, "")
        Thread {
            var saved = 0
            try {
                val hub = store.hub()
                hub.makeFolder(share, "", FOLDER)
                val taken = hub.names(share, FOLDER).toMutableSet()
                uris.forEachIndexed { i, uri ->
                    val (name, size, file) = describe(uri, i)
                    val target = freeName(taken, name)
                    runOnUiThread { progress(i, uris.size, target) }
                    try {
                        hub.upload(Upload("", share, FOLDER, target), size, System.currentTimeMillis()) { offset ->
                            open(uri, file, offset)
                        }
                    } finally {
                        file?.delete()
                    }
                    taken += target
                    saved += 1
                }
                runOnUiThread { done(share, saved) }
            } catch (e: Exception) {
                runOnUiThread { if (saved > 0) done(share, saved, e.message) else failed(e) }
            }
        }.start()
    }

    private fun progress(done: Int, total: Int, name: String) {
        val col = screen("Saving…")
        ui.headline(col, "Saving ${done + 1} of $total")
        if (name.isNotEmpty()) ui.caption(col, name)
        ui.progress(col, done, total, top = 16)
        ui.caption(col, "Keep this screen open until it is done.", 14)
    }

    private fun done(share: String, saved: Int, problem: String? = null) {
        working = false
        val col = screen("Saved")
        ui.headline(col, if (saved == 1) "1 file saved" else "$saved files saved")
        ui.body(col, "In “$share” › $FOLDER.")
        if (problem != null) ui.body(col, "Stopped early: $problem", 12)
        ui.button(col, "Done", top = 20) { finish() }
    }

    /** Name, size and (when the other app did not say how big it is) a temporary copy. */
    private fun describe(uri: Uri, index: Int): Triple<String, Long, File?> {
        var name: String? = null
        var size = -1L
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                name = c.getString(0)
                if (!c.isNull(1)) size = c.getLong(1)
            }
        }
        val clean = fileNameOf(name ?: uri.lastPathSegment, "shared-${index + 1}")
        if (size > 0) return Triple(clean, size, null)
        val copy = File.createTempFile("share", ".tmp", cacheDir)
        contentResolver.openInputStream(uri).use { input ->
            copy.outputStream().use { out -> input?.copyTo(out) }
        }
        return Triple(clean, copy.length(), copy)
    }

    private fun open(uri: Uri, file: File?, offset: Long): InputStream {
        val stream = file?.inputStream() ?: contentResolver.openInputStream(uri) ?: throw java.io.IOException("The file cannot be read.")
        var left = offset
        while (left > 0) {
            val n = stream.skip(left)
            if (n <= 0) { if (stream.read() < 0) break else left -= 1 } else left -= n
        }
        return stream
    }

    companion object {
        const val FOLDER = "From phone"
    }
}
