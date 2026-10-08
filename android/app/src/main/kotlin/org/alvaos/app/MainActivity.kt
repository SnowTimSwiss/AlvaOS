package org.alvaos.app

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
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
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.alvaos.photos.AlbumProgress
import org.alvaos.photos.DeviceInfo
import org.alvaos.photos.HubClient
import org.alvaos.photos.HubException
import org.alvaos.photos.Me
import org.alvaos.photos.Paired
import org.alvaos.photos.PairLink
import org.alvaos.photos.SyncEngine
import org.alvaos.photos.albumProgress
import com.google.android.material.R as M

/**
 * The AlvaOS app. Signed out: connect with the QR code of the Hub (or a code, or name and
 * password). Signed in, one bar at the bottom: the Hub apps the person has (Files, Photos, …,
 * the Hub itself inside), the photo backup and the settings. The native screens look like the
 * Hub (Ui.kt), so it reads as one app.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var gallery: Gallery
    private lateinit var ui: Ui
    private lateinit var frame: FrameLayout
    private lateinit var nav: BottomNavigationView
    private lateinit var statusBar: View
    private var hub: HubPage? = null
    private var tab = TAB_HUB
    private val tabApps = mutableMapOf<Int, String>()   // tab → the Hub app it shows
    private var appsOpen: String? = null               // a Hub app opened from the Apps tab
    private var navKey = ""
    private var choosing = false                       // the album list is open in the Backup tab
    private var bars = WindowInsetsCompat.CONSUMED
    private var afterDelete: ((List<String>) -> Unit)? = null
    private var deleting: List<String> = emptyList()
    private var autoDeleted = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var backupProgress: Pair<Int, Int>? = null

    // The Backup tab, redrawn in parts while a backup runs.
    private var statusHost: LinearLayout? = null
    private var albumsHost: LinearLayout? = null
    private var albums: List<AlbumProgress>? = null
    private var albumsAt = 0L

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
        statusBar = View(this)
        nav = BottomNavigationView(this).apply {
            labelVisibilityMode = BottomNavigationView.LABEL_VISIBILITY_LABELED
            // The Hub's own bar: the side colour, the selection in blue, the same line icons.
            setBackgroundColor(ui.color(M.attr.colorSurfaceContainer))
            itemActiveIndicatorColor = ColorStateList.valueOf(ui.color(M.attr.colorPrimaryContainer))
            val on = ui.color(M.attr.colorOnSurface)
            val off = ui.color(M.attr.colorOnSurfaceVariant)
            val tint = ColorStateList(arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf()), intArrayOf(on, off))
            itemIconTintList = tint
            itemTextColor = tint
            setOnItemSelectedListener { item -> tab = item.itemId; choosing = false; appsOpen = null; show(); true }
            setOnItemReselectedListener { if (appsOpen != null || choosing) { appsOpen = null; choosing = false; show() } }
        }
        buildNav()
        val line = View(this).apply { setBackgroundColor(ui.color(M.attr.colorOutlineVariant)) }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ui.color(M.attr.colorSurface))
            addView(statusBar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0))
            addView(frame, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
            addView(line, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ui.dp(1)))
            addView(nav, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        nav.tag = line
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
                    page != null && page.inFullscreen() -> page.exitFullscreen()
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
        for (name in listOf(SyncWorker.NOW, SyncWorker.PERIODIC, SyncWorker.WATCH, SyncWorker.MORE)) {
            work.getWorkInfosForUniqueWorkLiveData(name).observe(this) { infos ->
                val running = infos.firstOrNull { it.state == WorkInfo.State.RUNNING }
                val next = running?.progress?.let { it.getInt("done", 0) to it.getInt("total", 0) } ?: SyncWorker.live
                if (next != backupProgress) {
                    backupProgress = next
                    if (tab == TAB_BACKUP && !choosing && store.signedIn) backupChanged()
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
        if (store.backupOn) SyncWorker.schedule(this)         // the watchers survive an update of the app
        deleteWhatTheNasDeleted()
        // Which apps the person has now, or signed out on the NAS (Devices › Sign out, a new password)?
        lifecycleScope.launch {
            val answer: Any? = withContext(Dispatchers.IO) {
                try { store.pickServer(); LinkService.register(store, store.hub()); store.hub().me() }
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
        // The bar's colour continues under the status bar.
        statusBar.layoutParams = (statusBar.layoutParams as LinearLayout.LayoutParams).apply { height = i.top }
        statusBar.setBackgroundColor(ui.color(
            if (store.signedIn) M.attr.colorSurfaceContainerHigh else M.attr.colorSurface))
        frame.setPadding(i.left, 0, i.right, if (nav.visibility == View.VISIBLE) 0 else i.bottom)
    }

    private fun handle(intent: Intent?) {
        val link = intent?.data?.toString()?.let { PairLink.parse(it) } ?: return
        intent.data = null
        connect(link)
    }

    // ── Which screen ─────────────────────────────────────────────────────

    private fun show(message: String = "") {
        frame.removeAllViews()
        statusHost = null
        albumsHost = null
        val signed = store.signedIn
        nav.visibility = if (signed) View.VISIBLE else View.GONE
        (nav.tag as? View)?.visibility = nav.visibility
        pad()
        if (!signed) {
            signIn(message)
            return
        }
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
        "folder" -> R.drawable.ic_tab_files
        "image" -> R.drawable.ic_tab_photos
        "calendar" -> R.drawable.ic_tab_calendar
        "contact" -> R.drawable.ic_tab_contacts
        "message-circle" -> R.drawable.ic_tab_chat
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
                ui.feature(page, R.drawable.ic_tab_files, "Files and photos", "Everything on your NAS, at home and away.")
                ui.feature(page, R.drawable.ic_tab_backup, "Backup of this phone", "Your pictures go to the NAS by themselves.")
                ui.feature(page, R.drawable.ic_tab_calendar, "Calendar and chat", "Every app of your Hub, in one place.")
                ui.space(page, 12)
                ui.button(page, "Scan the QR code", icon = R.drawable.ic_qr, top = 16) { scan() }
                ui.caption(page, "On a computer, open the Hub of your NAS and choose Devices › Connect a phone.", 10)
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
                if (link == null) signIn("That is not the QR code of AlvaOS. Use the one in the Hub › Devices.")
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
            val paired: Paired
            val base: String
            if (server != null) {
                paired = HubClient(server).pair(link.code, device())
                base = server
            } else if (link.link.isNotEmpty()) {
                // Not at home: through AlvaOS Link, no address needed.
                paired = LinkService.pair(store, link.link, link.code, device())
                base = LinkService.proxyUrl(store) ?: throw HubException(LinkService.NOT_AVAILABLE)
            } else {
                throw HubException("The NAS cannot be reached at ${link.addresses.joinToString(" or ")}. " +
                    "Is the phone in the same network as the NAS (Wi-Fi)?")
            }
            store.linkRegistered = false
            if (link.link.isNotEmpty()) store.linkNas = link.link
            store.server = base
            store.addresses = link.addresses
            store.token = paired.token
            store.user = paired.user.ifEmpty { link.user }
            store.nasName = paired.nas_name.ifEmpty { link.nas }
            if (server != null) LinkService.register(store, store.hub())
            try { store.remember(store.hub().me()) } catch (e: Exception) { /* the tabs come on the next start */ }
        }
    }

    /** Shows a moment of waiting, does the work, and opens the Hub, or says what went wrong. */
    private fun busy(label: String, work: suspend () -> Unit) {
        frame.removeAllViews()
        nav.visibility = View.GONE
        (nav.tag as? View)?.visibility = View.GONE
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

    // ── The Hub tabs ─────────────────────────────────────────────────────

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
            },
            openBackup = {
                val backup = nav.menu.findItem(TAB_BACKUP)
                if (backup != null) nav.selectedItemId = backup.itemId
            }).also { hub = it }
        (page.view.parent as? ViewGroup)?.removeView(page.view)
        frame.addView(page.view)
        if (appId != null) page.open(appId) else page.load()
    }

    /** Opens a Hub app and one of its tools (Trash, Devices) in it. */
    private fun hubTool(tool: String) {
        tab = tabApps.entries.firstOrNull { it.value == "files" }?.key ?: nav.menu.getItem(0).itemId
        appsOpen = null
        show()
        hub?.openTool(tool)
    }

    // ── The Apps tab (more Hub apps, and the App Store apps) ─────────────

    private fun appsTab() {
        val page = ui.screen(frame, "Apps")
        ui.caption(page, "Everything on ${store.nasName.ifEmpty { "your NAS" }} for you.", 0)
        val inHub = store.hubApps.filter { it.id !in tabApps.values }
        if (inHub.isNotEmpty()) {
            val group = ui.group(page, 14)
            for (a in inHub) ui.item(group, iconFor(a.icon), a.name, "In the Hub") { appsOpen = a.id; show() }
        }
        val host = Uri.parse(store.server).host.orEmpty()
        if (store.storeApps.isNotEmpty()) {
            ui.section(page, "From the App Store")
            val group = ui.group(page)
            for (t in store.storeApps) {
                ui.item(group, R.drawable.ic_tab_hub, t.name, "Opens in the browser") {
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("http://$host:${t.port}${t.path.ifEmpty { "/" }}")))
                }
            }
        }
        if (inHub.isEmpty() && store.storeApps.isEmpty()) ui.body(page, "No more apps.", 16)
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
        val page = ui.screen(frame, "Photo backup")
        if (!store.backupOn || store.albums.isEmpty()) {
            val card = ui.card(page, 4)
            ui.badge(card, R.drawable.ic_tab_backup, 52)
            ui.title(card, "Back up your photos", 14)
            ui.body(card, "The pictures and videos of the albums you choose go to ${store.nasName.ifEmpty { "your NAS" }} " +
                "by themselves, also when the app is closed. They show up in Photos, album by album. Deleting stays " +
                "in sync both ways, through the NAS's trash.")
            ui.button(card, "Set up backup", top = 16) { setUpBackup() }
            if (!hasGalleryAccess()) ui.caption(card, "The app asks to see your pictures and videos.", 8)
            return
        }

        val status = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        statusHost = status
        page.addView(status, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        renderStatus()

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

        // The albums, with how far each one is
        val host = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        albumsHost = host
        page.addView(host, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        renderAlbums()
        loadAlbums()

        val actions = ui.row(page, 16)
        val now = ui.button(actions, "Back up now", icon = R.drawable.ic_refresh, top = 0) {
            SyncWorker.now(this); Snack.show(frame, "The backup starts.")
        }
        val free = ui.button(actions, "Free up space", Ui.Kind.Outlined, top = 0) { freeUp() }
        ui.weighted(now); ui.weighted(free, 10)
        ui.caption(page, "Free up space removes what is backed up and older than a month from this phone. It stays on the NAS.", 10)
        ui.button(page, "Change albums", Ui.Kind.Text, top = 6) { choosing = true; show() }
    }

    /** What the backup is doing now, in a card. */
    private fun renderStatus() {
        val host = statusHost ?: return
        host.removeAllViews()
        val running = backupProgress ?: SyncWorker.live
        val waiting = albums?.sumOf { maxOf(0, it.total - it.backedUp) } ?: 0
        val card = ui.card(host, 4)
        val head = ui.row(card, 0)
        val icon: Int
        val title: String
        val caption: String
        when {
            running != null -> {
                icon = R.drawable.ic_tab_backup
                title = if (running.second > 0) "Backing up ${running.first} of ${running.second}" else "Looking for new pictures…"
                caption = "to ${store.nasName.ifEmpty { "your NAS" }}"
            }
            store.lastMessage.startsWith("Waiting for the NAS") || store.lastMessage.contains("cannot be reached") -> {
                icon = R.drawable.ic_wifi_off
                title = "Waiting for the NAS"
                caption = store.lastMessage.substringAfter(": ", store.lastMessage)
            }
            store.lastSync == 0L -> {
                icon = R.drawable.ic_tab_backup
                title = "Not backed up yet"
                caption = waitingReason().ifEmpty { "The first backup starts in a moment." }
            }
            waiting > 0 -> {
                icon = R.drawable.ic_tab_backup
                title = "$waiting waiting"
                caption = waitingReason().ifEmpty { "They go to ${store.nasName.ifEmpty { "your NAS" }} by themselves." }
            }
            else -> {
                icon = R.drawable.ic_check
                title = "Everything is backed up"
                caption = "Last backup ${DateUtils.getRelativeTimeSpanString(store.lastSync)}"
            }
        }
        ui.badge(head, icon, 44)
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        head.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(14) })
        ui.title(texts, title)
        ui.caption(texts, caption, 2)
        if (running != null) ui.progress(card, running.first, running.second, 14, indeterminate = running.second == 0)
        else if (store.lastMessage.isNotEmpty() && store.lastSync != 0L && waiting == 0 && !store.lastMessage.startsWith("Everything"))
            ui.caption(card, store.lastMessage, 10)
    }

    /** Why a backup that is due is not running, if the phone knows. */
    private fun waitingReason(): String {
        val cm = getSystemService(ConnectivityManager::class.java)
        val caps = cm?.getNetworkCapabilities(cm.activeNetwork)
        if (caps == null) return "Waiting for a connection."
        if (store.wifiOnly && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)) return "Waiting for Wi-Fi."
        val battery = getSystemService(BatteryManager::class.java)
        if (store.chargingOnly && battery?.isCharging != true) return "Waiting for the charger."
        return ""
    }

    private fun renderAlbums() {
        val host = albumsHost ?: return
        host.removeAllViews()
        ui.section(host, "Albums")
        val group = ui.group(host)
        val known = store.known.values.groupingBy { it }.eachCount()
        val list = albums ?: store.albums.sorted().map { AlbumProgress(it, -1, known[it] ?: 0) }
        for (a in list) {
            val sub = when {
                a.total < 0 -> "${a.backedUp} backed up"
                a.total == 0 -> "Nothing in it"
                a.done -> "All ${a.total} backed up"
                else -> "${a.backedUp} of ${a.total} backed up"
            }
            val row = ui.item(group, R.drawable.ic_tab_photos, a.album, sub)
            if (a.total > 0 && !a.done) ui.progress(row.getChildAt(1) as LinearLayout, a.backedUp, a.total, 8)
        }
    }

    /** The real numbers from the gallery, off the main thread; at most every few seconds while a backup runs. */
    private fun loadAlbums() {
        if (albumsHost == null) return
        albumsAt = System.currentTimeMillis()
        val host = albumsHost
        lifecycleScope.launch {
            val list = withContext(Dispatchers.IO) { albumProgress(gallery.photos(store.albums), store.known, store.albums) }
            if (albumsHost === host && host != null) { albums = list; renderAlbums(); renderStatus() }
        }
    }

    /** The backup moved on: the status card at once, the albums every few seconds. */
    private fun backupChanged() {
        if (statusHost == null) { show(); return }
        renderStatus()
        if (System.currentTimeMillis() - albumsAt > 2500 || backupProgress == null) loadAlbums()
    }

    private fun chooseAlbums() {
        val page = ui.screen(frame, "What to back up", back = { choosing = false; show() })
        ui.caption(page, "Choose the albums of this phone. Each one becomes an album in Photos on your NAS.", 0)
        val counts = gallery.counts()
        val chosen = store.albums.ifEmpty { counts.keys.filter { it.equals("Camera", true) }.toSet() }.toMutableSet()
        if (counts.isEmpty()) {
            ui.body(page, "There are no pictures on this phone yet.", 16)
        } else {
            val group = ui.group(page, 14)
            counts.entries.sortedByDescending { it.value }.forEach { (album, count) ->
                ui.checkItem(group, R.drawable.ic_tab_photos, album, "$count pictures and videos", album in chosen) { on ->
                    if (on) chosen += album else chosen -= album
                }
            }
        }
        ui.section(page, "How")
        val how = ui.group(page)
        ui.switchItem(how, R.drawable.ic_tab_backup, "Only on Wi-Fi", "Not on mobile data", store.wifiOnly) { store.wifiOnly = it }
        ui.switchItem(how, R.drawable.ic_charge, "Only while charging", "Easier on the battery", store.chargingOnly) { store.chargingOnly = it }
        ui.switchItem(how, R.drawable.ic_trash, "Deleting here deletes on the NAS too",
            "Into the NAS's trash, for 30 days", store.deleteOnNas) { store.deleteOnNas = it }
        ui.button(page, "Back up these albums", top = 22) {
            if (chosen.isEmpty()) { Snack.show(frame, "Choose at least one album."); return@button }
            store.albums = chosen
            store.backupOn = true
            choosing = false
            albums = null
            SyncWorker.schedule(this)
            SyncWorker.now(this)
            if (Build.VERSION.SDK_INT >= 31 && !gallery.maySilentlyDelete() && !store.askedManageMedia) askManageMedia()
            else show()
        }
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

    /** "At home" for an address in the home network, "Through AlvaOS Link" for the Link door, else "Over the internet". */
    private fun where(): String {
        if (LinkService.isProxy(store.server)) return "Through AlvaOS Link"
        val host = Uri.parse(store.server).host.orEmpty()
        val home = Regex("^(192\\.168\\.|10\\.|172\\.(1[6-9]|2\\d|3[01])\\.|100\\.(6[4-9]|[7-9]\\d|1[01]\\d|12[0-7])\\.)").containsMatchIn(host) ||
            host.endsWith(".local") || host == "localhost"
        return if (home) "At home" else "Over the internet"
    }

    private fun settingsTab() {
        val page = ui.screen(frame, "Settings")

        val account = ui.card(page, 4)
        val who = ui.row(account, 0)
        ui.avatar(who, store.user)
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        who.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(14) })
        ui.title(texts, store.user)
        ui.caption(texts, "on ${store.nasName.ifEmpty { "your NAS" }}", 1)
        ui.caption(account, if (LinkService.isProxy(store.server)) where() else "${where()} · ${Uri.parse(store.server).host.orEmpty()}", 14)
        ui.caption(account, "AlvaOS Link: ${LinkService.describe(store)}", 2)
        val others = store.addresses.filter { it != store.server }
        if (others.isNotEmpty()) ui.caption(account, "Also tries ${others.joinToString(", ") { Uri.parse(it).host.orEmpty() }}", 2)

        val tools = ui.group(page, 14)
        ui.item(tools, R.drawable.ic_phone, "Devices", "See and sign out the connected phones") { hubTool("devices") }
        ui.item(tools, R.drawable.ic_trash, "Trash", "Deleted files, kept for 30 days") { hubTool("trash") }
        ui.item(tools, R.drawable.ic_link, "Shared links", "Links you made to files and folders") { hubTool("links") }

        ui.section(page, "Photo backup")
        val backup = ui.group(page)
        ui.switchItem(backup, R.drawable.ic_tab_backup, "Only on Wi-Fi", "Not on mobile data", store.wifiOnly) {
            store.wifiOnly = it
            if (store.backupOn) SyncWorker.schedule(this)
        }
        ui.switchItem(backup, R.drawable.ic_charge, "Only while charging", "Easier on the battery", store.chargingOnly) {
            store.chargingOnly = it
            if (store.backupOn) SyncWorker.schedule(this)
        }
        ui.switchItem(backup, R.drawable.ic_trash, "Deleting here deletes on the NAS too",
            "Into the NAS's trash, for 30 days", store.deleteOnNas) { store.deleteOnNas = it }
        if (Build.VERSION.SDK_INT >= 31) {
            if (gallery.maySilentlyDelete()) {
                ui.item(backup, R.drawable.ic_check, "Deleting in sync without asking", "On: what is deleted on the NAS goes from this phone")
            } else {
                ui.item(backup, R.drawable.ic_check, "Delete in sync without asking", "Android asks each time until you allow it") { allowManageMedia() }
            }
        }
        if (store.backupOn) {
            ui.item(backup, R.drawable.ic_x, "Stop backing up", "The pictures on the NAS stay there") {
                MaterialAlertDialogBuilder(this)
                    .setTitle("Stop backing up?")
                    .setMessage("Pictures that are on the NAS stay there. You can set it up again at any time.")
                    .setPositiveButton("Stop") { _, _ -> store.backupOn = false; SyncWorker.stop(this); show() }
                    .setNegativeButton("Cancel", null)
                    .show()
            }
        }

        ui.section(page, "This phone")
        val phone = ui.group(page)
        ui.item(phone, R.drawable.ic_logout, "Sign out", "Leave ${store.nasName.ifEmpty { "your NAS" }}", danger = true) {
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
