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
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.alvaos.photos.DeviceInfo
import org.alvaos.photos.HubClient
import org.alvaos.photos.HubException
import org.alvaos.photos.Me
import org.alvaos.photos.PairLink
import org.alvaos.photos.SyncEngine
import com.google.android.material.R as M

/**
 * The AlvaOS app. Signed out: connect with the QR code of the Hub (or a code,
 * or name and password). Signed in, three tabs: the Hub with every app the
 * person has on the NAS, the photo backup, and the settings.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var gallery: Gallery
    private lateinit var ui: Ui
    private lateinit var frame: FrameLayout
    private lateinit var nav: BottomNavigationView
    private var hub: HubPage? = null
    private var tab = TAB_HUB
    private val tabApps = mutableMapOf<Int, String>()   // tab → the Hub app it shows
    private var appsOpen: String? = null               // a Hub app opened from the Apps tab
    private var navKey = ""
    private var choosing = false               // the album list is open in the Backup tab
    private var bars = WindowInsetsCompat.CONSUMED
    private var afterDelete: ((List<String>) -> Unit)? = null
    private var deleting: List<String> = emptyList()
    private var autoDeleted = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var backupProgress: Pair<Int, Int>? = null

    companion object {
        const val TAB_HUB = 1
        const val TAB_BACKUP = 2
        const val TAB_SETTINGS = 3
        const val TAB_APPS = 4
        const val TAB_FIRST_APP = 100
    }

    private val askPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (hasGalleryAccess()) { choosing = true }
        show()
    }
    private val deleteRequest = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { res ->
        if (res.resultCode == Activity.RESULT_OK) afterDelete?.invoke(deleting)
        afterDelete = null
        show()
    }
    private val fileChooser = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        fileCallback?.onReceiveValue(android.webkit.WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data))
        fileCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        gallery = Gallery(this)
        ui = Ui(this)
        SyncWorker.channels(this)

        frame = FrameLayout(this)
        nav = BottomNavigationView(this).apply {
            labelVisibilityMode = BottomNavigationView.LABEL_VISIBILITY_LABELED
            setOnItemSelectedListener { item -> tab = item.itemId; choosing = false; appsOpen = null; show(); true }
            setOnItemReselectedListener { if (appsOpen != null || choosing) { appsOpen = null; choosing = false; show() } }
        }
        buildNav()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ui.color(M.attr.colorSurface))
            addView(frame, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
            addView(nav, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        // Android draws the app under the status bar and the gesture bar; keep the content clear of them.
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            bars = insets
            pad()
            insets
        }
        setContentView(root)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                val page = hub
                val first = nav.menu.getItem(0).itemId
                when {
                    choosing -> { choosing = false; show() }
                    showsHub() && page != null && page.canGoBack() -> page.goBack()
                    appsOpen != null -> { appsOpen = null; show() }
                    tab != first && store.signedIn -> { tab = first; show() }
                    else -> finish()
                }
            }
        })

        // The backup's progress, live in the Backup tab.
        val work = WorkManager.getInstance(this)
        for (name in listOf(SyncWorker.NOW, SyncWorker.PERIODIC)) {
            work.getWorkInfosForUniqueWorkLiveData(name).observe(this) { infos ->
                val running = infos.firstOrNull { it.state == WorkInfo.State.RUNNING }
                val next = running?.progress?.let { it.getInt("done", 0) to it.getInt("total", 0) }
                if (next != backupProgress) {
                    backupProgress = next
                    if (tab == TAB_BACKUP && !choosing && store.signedIn) show()
                }
            }
        }
        show()
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    override fun onResume() {
        super.onResume()
        if (!store.signedIn) return
        if (!showsHub()) show()
        deleteWhatTheNasDeleted()
        // Which apps the person has now, or signed out on the NAS (Phones and devices › Sign out, a new password)?
        lifecycleScope.launch {
            val answer: Any? = withContext(Dispatchers.IO) {
                try { store.pickServer(); store.hub().me() }
                catch (e: HubException) { if (e.signedOut) "out" else null }
                catch (e: Exception) { null }
            }
            when {
                answer == "out" -> signedOut("This phone was signed out on the NAS. Connect it again.")
                answer is Me -> {
                    store.remember(answer)
                    if (buildNav()) show() else if (showsHub()) hub?.load()
                }
                showsHub() -> hub?.load()
            }
        }
    }

    private fun pad() {
        val i = bars.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
        frame.setPadding(i.left, i.top, i.right, if (nav.visibility == View.VISIBLE) 0 else i.bottom)
    }

    private fun handle(intent: Intent?) {
        val link = intent?.data?.toString()?.let { PairLink.parse(it) } ?: return
        intent.data = null
        connect(link)
    }

    // ── Which screen ─────────────────────────────────────────────────────

    private fun show(message: String = "") {
        frame.removeAllViews()
        if (!store.signedIn) {
            nav.visibility = View.GONE
            pad()
            signIn(message)
            return
        }
        nav.visibility = View.VISIBLE
        pad()
        // Only marks the tab: setting selectedItemId here would call this again and again.
        nav.menu.findItem(tab)?.isChecked = true
        when (tab) {
            TAB_BACKUP -> if (choosing) chooseAlbums() else backupTab()
            TAB_SETTINGS -> settingsTab()
            TAB_APPS -> appsOpen?.let { hubTab(it) } ?: appsTab()
            else -> hubTab(tabApps[tab])
        }
    }

    private fun showsHub() = tab == TAB_HUB || tab in tabApps || (tab == TAB_APPS && appsOpen != null)

    private fun iconFor(name: String) = when (name) {
        "folder" -> R.drawable.ic_folder
        "image" -> R.drawable.ic_photos
        "calendar" -> R.drawable.ic_calendar
        "message-circle" -> R.drawable.ic_chat
        else -> R.drawable.ic_tab_hub
    }

    /**
     * One bar at the bottom: the person's Hub apps (Files, Photos, …), Backup and
     * Settings. With more than three Hub apps, or App Store apps, the rest are
     * under Apps. Returns whether the bar changed.
     */
    private fun buildNav(): Boolean {
        val apps = store.hubApps
        val more = apps.size > 3 || store.storeApps.isNotEmpty()
        val direct = if (more) apps.take(2) else apps
        val key = direct.joinToString { it.id } + "|" + more
        if (key == navKey && nav.menu.size() > 0) return false
        navKey = key
        tabApps.clear()
        nav.menu.clear()
        if (direct.isEmpty()) nav.menu.add(0, TAB_HUB, 0, "Hub").setIcon(R.drawable.ic_tab_hub)
        direct.forEachIndexed { i, a ->
            nav.menu.add(0, TAB_FIRST_APP + i, i, a.name).setIcon(iconFor(a.icon))
            tabApps[TAB_FIRST_APP + i] = a.id
        }
        if (more) nav.menu.add(0, TAB_APPS, 10, "Apps").setIcon(R.drawable.ic_tab_hub)
        nav.menu.add(0, TAB_BACKUP, 11, "Backup").setIcon(R.drawable.ic_tab_backup)
        nav.menu.add(0, TAB_SETTINGS, 12, "Settings").setIcon(R.drawable.ic_tab_settings)
        if (nav.menu.findItem(tab) == null) tab = nav.menu.getItem(0).itemId
        return true
    }

    private fun signedOut(message: String) {
        store.signOut()
        SyncWorker.stop(this)
        hub?.destroy()
        hub = null
        tab = TAB_HUB
        appsOpen = null
        show(message)
    }

    // ── Connecting ───────────────────────────────────────────────────────

    private fun device(): DeviceInfo {
        val name = Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME) ?: Build.MODEL
        val maker = Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
        val model = if (Build.MODEL.startsWith(maker, true)) Build.MODEL else "$maker ${Build.MODEL}"
        return DeviceInfo(name.take(60), model.take(60), "android", HubPage.appVersion(this))
    }

    private enum class Mode { Start, Code, Password }

    private fun signIn(error: String = "", mode: Mode = Mode.Start) {
        frame.removeAllViews()
        val page = ui.page(frame)
        page.gravity = Gravity.CENTER_HORIZONTAL
        ui.logo(page)
        ui.headline(page, if (mode == Mode.Password) "Sign in" else "Connect to your NAS", 20).gravity = Gravity.CENTER
        ui.body(page, "Your files, photos, calendar and chat from AlvaOS, and the backup of this phone's pictures.")
            .gravity = Gravity.CENTER
        if (error.isNotEmpty()) ui.error(page, error)
        ui.space(page, 12)
        when (mode) {
            Mode.Start -> {
                ui.feature(page, R.drawable.ic_folder, "Files and photos", "Everything on your NAS, at home and away.")
                ui.feature(page, R.drawable.ic_tab_backup, "Backup of this phone", "Your pictures go to the NAS by themselves.")
                ui.feature(page, R.drawable.ic_calendar, "Calendar and chat", "Every app of your Hub, in one place.")
                ui.space(page, 12)
                ui.button(page, "Scan the QR code", icon = R.drawable.ic_qr, top = 16) { scan() }
                ui.caption(page, "On a computer, open the Hub of your NAS and choose Phones and devices › Connect a phone.", 10)
                    .gravity = Gravity.CENTER
                ui.button(page, "Type the code instead", Ui.Kind.Outlined, top = 24) { signIn(mode = Mode.Code) }
                ui.button(page, "Sign in with name and password", Ui.Kind.Text, top = 4) { signIn(mode = Mode.Password) }
            }
            Mode.Code -> {
                val address = ui.field(page, "NAS address", store.server, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI,
                    "As in the Hub, e.g. 192.168.1.20:8090")
                val code = ui.field(page, "Code", "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS,
                    "Shown under the QR code, e.g. QFR9-YKZK")
                ui.button(page, "Connect", top = 20) {
                    connect(PairLink(code.text.toString().trim(), listOf(normalise(address.text.toString())), "", ""))
                }
                ui.button(page, "Back", Ui.Kind.Text, top = 4) { signIn() }
            }
            Mode.Password -> {
                val address = ui.field(page, "NAS address", store.server, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI,
                    "As in the Hub, e.g. 192.168.1.20:8090")
                val name = ui.field(page, "Name", store.user)
                val password = ui.field(page, "Password", "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
                val code = ui.field(page, "Two-step code", "", InputType.TYPE_CLASS_NUMBER, "Only if you use one")
                ui.button(page, "Sign in", top = 20) {
                    val server = normalise(address.text.toString())
                    val user = name.text.toString().trim().lowercase()
                    busy("Signing in…") {
                        val token = HubClient(server).signIn(user, password.text.toString(), code.text.toString().trim(), device())
                        store.server = server; store.addresses = listOf(server); store.user = user; store.token = token
                        store.nasName = Uri.parse(server).host.orEmpty()
                        try { store.remember(store.hub().me()) } catch (e: Exception) { /* later */ }
                    }
                }
                ui.button(page, "Back", Ui.Kind.Text, top = 4) { signIn() }
            }
        }
    }

    private fun scan() {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).enableAutoZoom().build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code ->
                val link = PairLink.parse(code.rawValue.orEmpty())
                if (link == null) signIn("That is not the QR code of AlvaOS. Use the one in the Hub › Phones and devices.")
                else connect(link)
            }
            .addOnFailureListener { e ->
                signIn("The scanner did not start (${e.message}). Type the code instead.", Mode.Code)
            }
    }

    /** The QR code (or typed code): find the address that answers, then trade the code for a session. */
    private fun connect(link: PairLink) {
        busy("Connecting to ${link.nas.ifEmpty { "your NAS" }}…") {
            val server = link.addresses.firstOrNull { HubClient(it).reachable() }
                ?: throw HubException("The NAS cannot be reached at ${link.addresses.joinToString(" or ")}. " +
                    "Is the phone in the same network as the NAS (Wi-Fi)?")
            val paired = HubClient(server).pair(link.code, device())
            store.server = server
            store.addresses = link.addresses
            store.token = paired.token
            store.user = paired.user.ifEmpty { link.user }
            store.nasName = paired.nas_name.ifEmpty { link.nas }
            try { store.remember(store.hub().me()) } catch (e: Exception) { /* the tabs come on the next start */ }
        }
    }

    /** Shows a moment of waiting, does the work, and opens the Hub, or says what went wrong. */
    private fun busy(label: String, work: suspend () -> Unit) {
        frame.removeAllViews()
        nav.visibility = View.GONE
        val page = ui.page(frame)
        page.gravity = Gravity.CENTER_HORIZONTAL
        ui.space(page, 120)
        ui.logo(page)
        ui.title(page, label, 24).gravity = Gravity.CENTER
        LinearProgressIndicator(this).apply { isIndeterminate = true }
            .also { page.addView(it, LinearLayout.LayoutParams(ui.dp(160), ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = ui.dp(16) }) }
        lifecycleScope.launch {
            val problem = withContext(Dispatchers.IO) {
                try { work(); "" } catch (e: Exception) { e.message ?: "That did not work." }
            }
            if (problem.isNotEmpty()) { signIn(problem); return@launch }
            autoDeleted = false
            tab = TAB_HUB
            appsOpen = null
            navKey = ""
            buildNav()
            hub?.destroy(); hub = null
            if (store.backupOn) SyncWorker.schedule(this@MainActivity)
            show()
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

    // ── The Hub tab ──────────────────────────────────────────────────────

    private fun hubTab(appId: String?) {
        val page = hub ?: HubPage(this, store, ui,
            pickFiles = { intent, callback ->
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                try { fileChooser.launch(intent) } catch (e: Exception) { fileCallback = null; callback.onReceiveValue(null) }
            },
            unreachable = {
                lifecycleScope.launch {
                    withContext(Dispatchers.IO) { store.pickServer() }
                    hub?.load(force = true)
                }
            }).also { hub = it }
        (page.view.parent as? ViewGroup)?.removeView(page.view)
        frame.addView(page.view)
        if (appId != null) page.open(appId) else page.load()
    }

    // ── The Apps tab (more Hub apps, and the App Store apps) ─────────────

    private fun appsTab() {
        val page = ui.page(frame)
        ui.headline(page, "Apps")
        ui.caption(page, "Everything on ${store.nasName.ifEmpty { "your NAS" }} for you.")
        val tiles = mutableListOf<Triple<Int, String, () -> Unit>>()
        val subs = mutableListOf<String>()
        for (a in store.hubApps.filter { it.id !in tabApps.values }) {
            tiles += Triple(iconFor(a.icon), a.name) { appsOpen = a.id; show() }
            subs += "In the Hub"
        }
        val host = Uri.parse(store.server).host.orEmpty()
        for (t in store.storeApps) {
            tiles += Triple(R.drawable.ic_tab_hub, t.name) {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("http://$host:${t.port}${t.path.ifEmpty { "/" }}")))
            }
            subs += "Opens in the browser"
        }
        if (tiles.isEmpty()) ui.body(page, "No more apps.", 16)
        tiles.zip(subs).chunked(2).forEach { pair ->
            val line = ui.row(page, 12)
            line.gravity = android.view.Gravity.FILL_VERTICAL
            pair.forEachIndexed { i, (tile, sub) ->
                ui.weighted(ui.tile(line, tile.first, tile.second, sub, tile.third), if (i == 0) 0 else 12)
            }
            if (pair.size == 1) line.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f).apply { marginStart = ui.dp(12) })
        }
    }

    // ── The Backup tab ───────────────────────────────────────────────────

    private fun hasGalleryAccess(): Boolean = needed().all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    private fun needed(): List<String> =
        if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO)
        else listOf(Manifest.permission.READ_EXTERNAL_STORAGE)

    private fun setUpBackup() {
        if (hasGalleryAccess()) { choosing = true; show(); return }
        val extra = mutableListOf(Manifest.permission.ACCESS_MEDIA_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) extra += Manifest.permission.POST_NOTIFICATIONS
        askPermissions.launch((needed() + extra).toTypedArray())
    }

    private fun backupTab() {
        val page = ui.page(frame)
        ui.headline(page, "Photo backup")
        if (!store.backupOn || store.albums.isEmpty()) {
            val card = ui.card(page, 20)
            ui.badge(card, R.drawable.ic_photos, 56)
            ui.title(card, "Back up your photos", 14)
            ui.body(card, "The pictures and videos of the albums you choose go to ${store.nasName.ifEmpty { "your NAS" }} " +
                "by themselves. They show up in Photos in the Hub, album by album. Deleting stays in sync both ways, " +
                "through the NAS's trash.")
            ui.button(card, "Set up backup", top = 16) { setUpBackup() }
            if (!hasGalleryAccess()) ui.caption(card, "The app asks to see your pictures and videos.", 8)
            return
        }

        // How it goes
        val status = ui.card(page, 16, M.attr.colorPrimaryContainer)
        val running = backupProgress
        val head = ui.row(status, 0)
        ui.badge(head, if (running != null) R.drawable.ic_tab_backup else R.drawable.ic_check, 44,
            M.attr.colorOnPrimary, M.attr.colorPrimary)
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        head.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(14) })
        val last = if (store.lastSync == 0L) "Not backed up yet"
        else "Last backup ${DateUtils.getRelativeTimeSpanString(store.lastSync)}"
        ui.title(texts, when {
            running != null && running.second > 0 -> "Backing up ${running.first} of ${running.second}"
            running != null -> "Looking for new pictures…"
            store.lastMessage.isNotEmpty() -> store.lastMessage
            else -> "Everything is backed up"
        })
        ui.caption(texts, "$last · to ${store.nasName.ifEmpty { Uri.parse(store.server).host ?: "" }}", 2)
        if (running != null) {
            LinearProgressIndicator(this).apply {
                isIndeterminate = running.second == 0
                max = maxOf(1, running.second)
                progress = running.first
            }.also { status.addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = ui.dp(14) }) }
        }

        // Deleted on the NAS
        val pending = store.pendingDeletes
        if (pending.isNotEmpty()) {
            val card = ui.card(page, 12, M.attr.colorTertiaryContainer)
            ui.title(card, "${pending.size} deleted on your NAS")
            if (gallery.maySilentlyDelete()) {
                ui.caption(card, "${if (pending.size == 1) "It goes" else "They go"} from this phone on their own.")
            } else {
                ui.caption(card, "They are in the NAS's trash. Delete them on this phone too?")
                ui.button(card, "Delete ${if (pending.size == 1) "it" else "them"} here too") {
                    delete(pending.toList()) { ids -> sync { SyncEngine(store.hub(), gallery, store).deletedHere(ids) } }
                }
                if (Build.VERSION.SDK_INT >= 31) {
                    ui.button(card, "Allow this without asking", Ui.Kind.Text, top = 2) { allowManageMedia() }
                }
            }
        }

        // The albums
        val albums = ui.card(page)
        ui.title(albums, "Albums")
        val counts = gallery.counts()
        for (album in store.albums.sorted()) {
            ui.body(albums, "$album  ·  ${counts[album] ?: 0}", 8)
        }
        ui.caption(albums, "${store.known.size} pictures and videos are on the NAS.", 10)
        ui.button(albums, "Change albums", Ui.Kind.Text, top = 4) { choosing = true; show() }

        val actions = ui.row(page, 16)
        val now = ui.button(actions, "Back up now", top = 0) {
            SyncWorker.now(this); store.lastMessage = ""; Snack.show(frame, "The backup starts.")
        }
        val free = ui.button(actions, "Free up space", Ui.Kind.Outlined, top = 0) { freeUp() }
        ui.weighted(now); ui.weighted(free, 10)
        ui.caption(page, "Free up space removes what is backed up and older than a month from this phone. It stays on the NAS.", 10)
    }

    private fun chooseAlbums() {
        val page = ui.page(frame)
        ui.headline(page, "What to back up")
        ui.body(page, "Choose the albums of this phone. Each one becomes an album in Photos on your NAS.")
        val counts = gallery.counts()
        val chosen = store.albums.ifEmpty { counts.keys.filter { it.equals("Camera", true) }.toSet() }.toMutableSet()
        val list = ui.card(page, 16)
        counts.entries.sortedByDescending { it.value }.forEach { (album, count) ->
            list.addView(MaterialCheckBox(this).apply {
                text = "$album  ·  $count"
                textSize = 16f
                isChecked = album in chosen
                minHeight = ui.dp(48)
                setOnCheckedChangeListener { _, on -> if (on) chosen += album else chosen -= album }
            })
        }
        if (counts.isEmpty()) ui.body(list, "There are no pictures on this phone yet.")
        ui.section(page, "How")
        ui.switch(page, "Only on Wi-Fi", store.wifiOnly) { store.wifiOnly = it }
        ui.switch(page, "Deleting a picture here deletes it on the NAS too (into its trash)", store.deleteOnNas) {
            store.deleteOnNas = it
        }
        ui.button(page, "Back up these albums", top = 20) {
            if (chosen.isEmpty()) { Snack.show(frame, "Choose at least one album."); return@button }
            store.albums = chosen
            store.backupOn = true
            choosing = false
            SyncWorker.schedule(this)
            SyncWorker.now(this)
            if (Build.VERSION.SDK_INT >= 31 && !gallery.maySilentlyDelete() && !store.askedManageMedia) askManageMedia()
            else show()
        }
        if (store.backupOn) ui.button(page, "Cancel", Ui.Kind.Text, top = 4) { choosing = false; show() }
    }

    /** Once: so that deleting stays in sync without a question each time. */
    private fun askManageMedia() {
        store.askedManageMedia = true
        MaterialAlertDialogBuilder(this)
            .setTitle("Keep deleting in sync")
            .setMessage("When you delete a picture on your NAS, the app deletes it on this phone too. Android asks you " +
                "each time, unless you allow AlvaOS to manage your media once. Then it just happens.")
            .setPositiveButton("Allow") { _, _ -> allowManageMedia() }
            .setNegativeButton("Not now", null)
            .setOnDismissListener { show() }
            .show()
    }

    private fun allowManageMedia() {
        startActivity(Intent(Settings.ACTION_REQUEST_MANAGE_MEDIA, Uri.parse("package:$packageName")))
    }

    /** What was deleted on the NAS goes here too, without a question when the person allowed that. */
    private fun deleteWhatTheNasDeleted() {
        val pending = store.pendingDeletes
        if (autoDeleted || pending.isEmpty() || !gallery.maySilentlyDelete()) return
        autoDeleted = true
        val ids = gallery.existing(pending).toList()
        val gone = pending - ids.toSet()
        if (gone.isNotEmpty()) sync { SyncEngine(store.hub(), gallery, store).deletedHere(gone) }
        if (ids.isNotEmpty()) delete(ids) { done -> sync { SyncEngine(store.hub(), gallery, store).deletedHere(done) } }
    }

    /** Pictures already on the NAS and older than a month go from the phone; they stay on the NAS. */
    private fun freeUp() {
        val monthAgo = System.currentTimeMillis() - 30L * 24 * 3600 * 1000
        val engine = SyncEngine(store.hub(), gallery, store)
        val ids = engine.backedUp(gallery.photos(store.albums)).filter { it.modified < monthAgo }.map { it.id }
        if (ids.isEmpty()) {
            Snack.show(frame, "Nothing older than a month to free up.")
            return
        }
        delete(ids) { done ->
            engine.freedUp(done)
            store.lastMessage = "${done.size} removed from this phone, kept on the NAS."
        }
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

    // ── The Settings tab ─────────────────────────────────────────────────

    private fun settingsTab() {
        val page = ui.page(frame)
        ui.headline(page, "Settings")

        val account = ui.card(page, 16)
        val who = ui.row(account, 0)
        ui.avatar(who, store.user)
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        who.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(14) })
        ui.title(texts, store.user).setTypeface(null, Typeface.BOLD)
        ui.caption(texts, "on ${store.nasName.ifEmpty { "your NAS" }}", 0)
        ui.caption(account, "Connected through ${store.server}", 14)
        val others = store.addresses.filter { it != store.server }
        if (others.isNotEmpty()) ui.caption(account, "Also tries ${others.joinToString(", ")}", 2)
        ui.button(account, "Phones and devices", Ui.Kind.Text, top = 6) {
            tab = tabApps.entries.firstOrNull { it.value == "files" }?.key ?: nav.menu.getItem(0).itemId
            appsOpen = null
            show()
            frame.postDelayed({ hub?.openDevices() }, 600)
        }

        ui.section(page, "Photo backup")
        ui.switch(page, "Only on Wi-Fi", store.wifiOnly) {
            store.wifiOnly = it
            if (store.backupOn) SyncWorker.schedule(this)
        }
        ui.switch(page, "Deleting here deletes on the NAS too", store.deleteOnNas) { store.deleteOnNas = it }
        if (Build.VERSION.SDK_INT >= 31) {
            if (gallery.maySilentlyDelete()) {
                ui.caption(page, "What is deleted on the NAS goes from this phone without asking.", 4)
            } else {
                ui.button(page, "Delete in sync without asking", Ui.Kind.Outlined, top = 8) { allowManageMedia() }
            }
        }
        if (store.backupOn) {
            ui.button(page, "Stop backing up", Ui.Kind.Text, top = 4) {
                MaterialAlertDialogBuilder(this)
                    .setTitle("Stop backing up?")
                    .setMessage("Pictures that are on the NAS stay there. You can set it up again at any time.")
                    .setPositiveButton("Stop") { _, _ -> store.backupOn = false; SyncWorker.stop(this); show() }
                    .setNegativeButton("Cancel", null)
                    .show()
            }
        }

        ui.section(page, "This phone")
        ui.button(page, "Sign out", Ui.Kind.Danger, top = 10) {
            MaterialAlertDialogBuilder(this)
                .setTitle("Sign out?")
                .setMessage("This phone leaves ${store.nasName.ifEmpty { "your NAS" }}: no Hub and no backup until you connect it again. " +
                    "Pictures on the NAS stay there.")
                .setPositiveButton("Sign out") { _, _ ->
                    val client = store.hub()
                    lifecycleScope.launch(Dispatchers.IO) { client.signOut() }
                    signedOut("")
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        ui.caption(page, "AlvaOS app ${HubPage.appVersion(this)}", 24).gravity = Gravity.CENTER
    }
}
