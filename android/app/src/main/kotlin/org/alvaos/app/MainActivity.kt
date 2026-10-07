package org.alvaos.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.provider.Settings
import android.text.InputType
import android.text.format.DateUtils
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.alvaos.photos.HubClient
import org.alvaos.photos.SyncEngine

/**
 * Three screens: sign in to the NAS, choose the albums to back up, and how it
 * goes (back up now, free up space, delete what was deleted on the NAS).
 * Built in code, no layouts: small and checked by the compiler.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var gallery: Gallery
    private lateinit var root: LinearLayout
    private var afterDelete: ((List<String>) -> Unit)? = null
    private var deleting: List<String> = emptyList()

    private val askPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { show() }
    private val deleteRequest = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { res ->
        if (res.resultCode == Activity.RESULT_OK) afterDelete?.invoke(deleting)
        afterDelete = null
        show()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        gallery = Gallery(this)
        SyncWorker.channels(this)
        root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(28), dp(20), dp(28)) }
        setContentView(ScrollView(this).apply { addView(root) })
        show()
    }

    override fun onResume() {
        super.onResume()
        show()
    }

    // ── Building blocks ──────────────────────────────────────────────────

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun title(text: String) = TextView(this).apply {
        this.text = text; textSize = 24f; setTypeface(typeface, Typeface.BOLD); setPadding(0, 0, 0, dp(8))
    }.also { root.addView(it) }

    private fun text(text: String, size: Float = 15f) = TextView(this).apply {
        this.text = text; textSize = size; setPadding(0, dp(4), 0, dp(8))
    }.also { root.addView(it) }

    private fun field(hint: String, value: String = "", password: Boolean = false) = EditText(this).apply {
        this.hint = hint; setText(value); setSingleLine()
        inputType = if (password) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
    }.also { root.addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)) }

    private fun button(label: String, quiet: Boolean = false, onClick: () -> Unit) =
        MaterialButton(this, null, if (quiet) com.google.android.material.R.attr.materialButtonOutlinedStyle
        else com.google.android.material.R.attr.materialButtonStyle).apply {
            text = label; setOnClickListener { onClick() }
        }.also {
            root.addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(8) })
        }

    private fun switch(label: String, on: Boolean, onChange: (Boolean) -> Unit) = SwitchCompat(this).apply {
        text = label; isChecked = on; textSize = 15f; setPadding(0, dp(8), 0, dp(8))
        setOnCheckedChangeListener { _, checked -> onChange(checked) }
    }.also { root.addView(it) }

    private fun space() = root.addView(View(this), LinearLayout.LayoutParams(1, dp(16)))

    private fun busy(label: String, work: suspend () -> Unit) {
        root.removeAllViews()
        title(label)
        lifecycleScope.launch { work() }
    }

    // ── Screens ──────────────────────────────────────────────────────────

    private fun show() {
        root.removeAllViews()
        when {
            !store.signedIn -> signIn()
            !hasGalleryAccess() -> permissions()
            store.albums.isEmpty() -> chooseAlbums()
            else -> status()
        }
    }

    private fun signIn(error: String = "") {
        title("Back up to your NAS")
        text("Sign in with the same name and password as for the shared folders. The address is the one of AlvaOS Hub: " +
            "at home like 192.168.1.20:8090, away through Tailscale, or your own domain.")
        val server = field("NAS address", store.server.ifEmpty { "" })
        val user = field("Name", store.user)
        val password = field("Password", password = true)
        if (error.isNotEmpty()) text(error).setTextColor(0xFFC5221F.toInt())
        button("Sign in") {
            val address = normalise(server.text.toString())
            val name = user.text.toString().trim().lowercase()
            val pass = password.text.toString()
            busy("Signing in…") {
                val problem = withContext(Dispatchers.IO) {
                    try {
                        val token = HubClient(address).signIn(name, pass)
                        store.server = address; store.user = name; store.token = token
                        ""
                    } catch (e: Exception) {
                        e.message ?: "The NAS cannot be reached at $address."
                    }
                }
                if (problem.isEmpty()) show() else { root.removeAllViews(); signIn(problem) }
            }
        }
    }

    private fun normalise(raw: String): String {
        var s = raw.trim().trimEnd('/')
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            s = "http://$s"
            if (!Regex(":\\d+$").containsMatchIn(s)) s += ":8090"
        }
        return s
    }

    private fun hasGalleryAccess(): Boolean = needed().all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    private fun needed(): List<String> =
        if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO)
        else listOf(Manifest.permission.READ_EXTERNAL_STORAGE)

    private fun permissions() {
        title("Your pictures")
        text("To back them up, the app needs to see your pictures and videos. It also asks to keep where a photo was " +
            "taken and to show a notification while it backs up.")
        button("Allow") {
            val extra = mutableListOf(Manifest.permission.ACCESS_MEDIA_LOCATION)
            if (Build.VERSION.SDK_INT >= 33) extra += Manifest.permission.POST_NOTIFICATIONS
            askPermissions.launch((needed() + extra).toTypedArray())
        }
    }

    private fun chooseAlbums() {
        title("What to back up")
        text("Choose the albums of this phone. Each one becomes an album in Photos on your NAS.")
        val counts = gallery.counts()
        val chosen = store.albums.ifEmpty { counts.keys.filter { it.equals("Camera", true) }.toSet() }.toMutableSet()
        counts.entries.sortedByDescending { it.value }.forEach { (album, count) ->
            root.addView(CheckBox(this).apply {
                text = "$album  ·  $count"; textSize = 16f; isChecked = album in chosen
                setOnCheckedChangeListener { _, on -> if (on) chosen += album else chosen -= album }
            })
        }
        if (counts.isEmpty()) text("There are no pictures on this phone yet.")
        space()
        switch("Only on Wi-Fi", store.wifiOnly) { store.wifiOnly = it }
        switch("Deleting a picture here deletes it on the NAS too (it goes to the NAS's trash)", store.deleteOnNas) {
            store.deleteOnNas = it
        }
        button("Back up these albums") {
            if (chosen.isEmpty()) return@button
            store.albums = chosen
            SyncWorker.schedule(this)
            SyncWorker.now(this)
            show()
        }
    }

    private fun status() {
        title("Backup")
        text("${store.user} on ${Uri.parse(store.server).host ?: store.server}", 13f)
        val last = if (store.lastSync == 0L) "Not backed up yet."
        else "Last backup ${DateUtils.getRelativeTimeSpanString(store.lastSync)}."
        text("$last ${store.lastMessage}")
        text("Albums: ${store.albums.sorted().joinToString(", ")}", 14f)
        text("${store.known.size} pictures and videos are on the NAS.", 14f)

        val pending = store.pendingDeletes
        if (pending.isNotEmpty()) {
            space()
            text("${pending.size} ${if (pending.size == 1) "was" else "were"} deleted on your NAS.").setTypeface(null, Typeface.BOLD)
            button("Delete ${if (pending.size == 1) "it" else "them"} here too") {
                delete(pending.toList()) { ids -> sync { SyncEngine(store.hub(), gallery, store).deletedHere(ids) } }
            }
            if (Build.VERSION.SDK_INT >= 31 && !MediaStore.canManageMedia(this)) {
                button("Allow this without asking each time", quiet = true) {
                    startActivity(Intent(Settings.ACTION_REQUEST_MANAGE_MEDIA, Uri.parse("package:$packageName")))
                }
            }
        }
        space()
        button("Back up now") { SyncWorker.now(this); store.lastMessage = "Backing up…"; show() }
        button("Free up space", quiet = true) { freeUp() }
        button("Change albums", quiet = true) { root.removeAllViews(); chooseAlbums() }
        button("Sign out", quiet = true) { store.signOut(); SyncWorker.stop(this); show() }
    }

    /** Pictures already on the NAS and older than a month go from the phone; they stay on the NAS. */
    private fun freeUp() {
        val monthAgo = System.currentTimeMillis() - 30L * 24 * 3600 * 1000
        val engine = SyncEngine(store.hub(), gallery, store)
        val ids = engine.backedUp(gallery.photos(store.albums)).filter { it.modified < monthAgo }.map { it.id }
        if (ids.isEmpty()) {
            store.lastMessage = "Nothing older than a month to free up."
            show()
            return
        }
        delete(ids) { done -> engine.freedUp(done); store.lastMessage = "${done.size} removed from this phone, kept on the NAS." }
    }

    private fun delete(ids: List<String>, then: (List<String>) -> Unit) {
        deleting = ids
        afterDelete = then
        val request = MediaStore.createDeleteRequest(contentResolver, ids.map { gallery.uri(it) })
        deleteRequest.launch(IntentSenderRequest.Builder(request.intentSender).build())
    }

    private fun sync(work: () -> Unit) = lifecycleScope.launch(Dispatchers.IO) {
        try { work() } catch (e: Exception) { store.lastMessage = e.message ?: "" }
        withContext(Dispatchers.Main) { show() }
    }
}
