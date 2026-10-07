package org.alvaos.app

import android.content.Context
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.testing.WorkManagerTestInitHelper
import com.github.takahirom.roborazzi.captureRoboImage
import com.google.android.material.bottomnavigation.BottomNavigationView
import org.alvaos.photos.HubApp
import org.alvaos.photos.StoreTile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Every screen of the app opens without a crash, and leaves a picture of itself in
 * app/build/outputs/roborazzi (light and dark) to look at. CI puts them on the branch app-screens.
 */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w400dp-h880dp-xxhdpi")
class ScreensTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()

    @Before
    fun clean() {
        WorkManagerTestInitHelper.initializeTestWorkManager(ctx)
        ctx.getSharedPreferences("alvaos", Context.MODE_PRIVATE).edit().clear().commit()
        SyncWorker.live = null
    }

    @After
    fun after() { SyncWorker.live = null }

    private fun texts(root: View): List<String> = when (root) {
        is TextView -> listOf(root.text.toString())
        is ViewGroup -> (0 until root.childCount).flatMap { texts(root.getChildAt(it)) }
        else -> emptyList()
    }

    private fun <T : View> find(root: View, type: Class<T>): T? = when {
        type.isInstance(root) -> type.cast(root)
        root is ViewGroup -> (0 until root.childCount).firstNotNullOfOrNull { find(root.getChildAt(it), type) }
        else -> null
    }

    private fun clickText(root: View, label: String): Boolean {
        if (root is TextView && root.text.toString() == label) {
            var v: View? = root
            while (v != null && !v.hasOnClickListeners()) v = v.parent as? View
            v?.performClick()
            return v != null
        }
        if (root is ViewGroup) for (i in 0 until root.childCount) if (clickText(root.getChildAt(i), label)) return true
        return false
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun picture(scenario: ActivityScenario<MainActivity>, name: String) {
        idle()
        scenario.onActivity { it.window.decorView.captureRoboImage("build/outputs/roborazzi/$name.png") }
    }

    private fun signedIn(apps: Int = 2, albums: Set<String> = emptySet()) {
        Store(ctx).apply {
            server = "http://192.168.0.143:8090"
            addresses = listOf(server, "https://nas.timserver.uk")
            token = "test"
            user = "tim"
            nasName = "cygnus"
            hubApps = listOf(HubApp("files", "Files", "folder"), HubApp("photos", "Photos", "image"),
                HubApp("calendar", "Calendar", "calendar"), HubApp("chat", "Chat", "message-circle")).take(apps)
            storeApps = if (apps > 3) listOf(StoreTile("jellyfin", "Jellyfin", 8096, "/")) else emptyList()
            if (albums.isNotEmpty()) {
                this.albums = albums; backupOn = true
                lastSync = System.currentTimeMillis() - 5 * 60_000
                known = (1..16).associate { "i$it" to "Camera" }
            }
        }
    }

    private fun openTab(scenario: ActivityScenario<MainActivity>, id: Int) {
        scenario.onActivity { a ->
            find(a.window.decorView, BottomNavigationView::class.java)!!.selectedItemId = id
        }
        idle()
    }

    @Test
    fun theFirstScreenSaysHowToConnect() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            idle()
            scenario.onActivity { a ->
                val shown = texts(a.window.decorView)
                assertTrue(shown.toString(), "Scan the QR code" in shown)
                assertTrue(shown.toString(), "Type the code instead" in shown)
                assertTrue(shown.toString(), "Sign in with name and password" in shown)
            }
            picture(scenario, "1-connect")
            scenario.onActivity { clickText(it.window.decorView, "Type the code instead") }
            picture(scenario, "1-connect-code")
            scenario.onActivity { a -> clickText(a.window.decorView, "Back") }
            scenario.onActivity { clickText(it.window.decorView, "Sign in with name and password") }
            picture(scenario, "1-connect-password")
        }
    }

    @Test
    @Config(qualifiers = "w400dp-h880dp-night-xxhdpi")
    fun theFirstScreenInTheDark() {
        ActivityScenario.launch(MainActivity::class.java).use { picture(it, "1-connect-dark") }
    }

    @Test
    fun everyTabOpensWithOneBarAtTheBottom() {
        signedIn(apps = 4)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            idle()
            val labels = mutableListOf<String>()
            scenario.onActivity { a ->
                val nav = find(a.window.decorView, BottomNavigationView::class.java)!!
                for (i in 0 until nav.menu.size()) labels += nav.menu.getItem(i).title.toString()
            }
            assertEquals(listOf("Files", "Photos", "Apps", "Backup", "Settings"), labels)
            labels.forEachIndexed { i, label ->
                scenario.onActivity { a ->
                    val nav = find(a.window.decorView, BottomNavigationView::class.java)!!
                    nav.selectedItemId = nav.menu.getItem(i).itemId
                }
                picture(scenario, "2-tab-${i + 1}-${label.lowercase()}")
            }
        }
    }

    @Test
    fun theAppsAndSettingsListWhatThereIs() {
        signedIn(apps = 4)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            openTab(scenario, MainActivity.TAB_APPS)
            scenario.onActivity { a ->
                val shown = texts(a.window.decorView)
                assertTrue(shown.toString(), "Calendar" in shown && "Chat" in shown && "Jellyfin" in shown)
            }
            openTab(scenario, MainActivity.TAB_SETTINGS)
            scenario.onActivity { a ->
                val shown = texts(a.window.decorView)
                assertTrue(shown.toString(), "Phones and devices" in shown && "Trash" in shown && "Sign out" in shown)
                assertTrue(shown.toString(), shown.any { it.startsWith("At home") })
            }
            picture(scenario, "4-settings")
        }
    }

    @Test
    fun theBackupSaysHowFarItIs() {
        signedIn(albums = setOf("Camera", "Screenshots"))
        Store(ctx).pendingDeletes = setOf("i1", "i2")
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            openTab(scenario, MainActivity.TAB_BACKUP)
            scenario.onActivity { a ->
                val shown = texts(a.window.decorView)
                assertTrue(shown.toString(), "Albums" in shown && "Camera" in shown && "Screenshots" in shown)
                assertTrue(shown.toString(), shown.any { it.contains("deleted on your NAS") })
            }
            picture(scenario, "3-backup-on")
            // A backup that runs now: the numbers are live.
            SyncWorker.live = 16 to 32
            scenario.onActivity { a -> clickText(a.window.decorView, "Back up now") }
            openTab(scenario, MainActivity.TAB_SETTINGS)
            openTab(scenario, MainActivity.TAB_BACKUP)
            scenario.onActivity { a ->
                assertTrue(texts(a.window.decorView).toString(), "Backing up 16 of 32" in texts(a.window.decorView))
            }
            picture(scenario, "3-backup-running")
            SyncWorker.live = null
            scenario.onActivity { a -> clickText(a.window.decorView, "Change albums") }
            scenario.onActivity { a ->
                assertTrue(texts(a.window.decorView).toString(), "What to back up" in texts(a.window.decorView))
            }
            picture(scenario, "3-choose-albums")
        }
    }

    @Test
    fun theBackupBeforeItIsSetUp() {
        signedIn()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            openTab(scenario, MainActivity.TAB_BACKUP)
            scenario.onActivity { a ->
                assertTrue(texts(a.window.decorView).toString(), "Set up backup" in texts(a.window.decorView))
            }
            picture(scenario, "3-backup-off")
        }
    }

    @Test
    @Config(qualifiers = "w400dp-h880dp-night-xxhdpi")
    fun theNativeScreensInTheDark() {
        signedIn(apps = 4, albums = setOf("Camera"))
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            openTab(scenario, MainActivity.TAB_BACKUP)
            picture(scenario, "5-dark-backup")
            openTab(scenario, MainActivity.TAB_SETTINGS)
            picture(scenario, "5-dark-settings")
            openTab(scenario, MainActivity.TAB_APPS)
            picture(scenario, "5-dark-apps")
        }
    }
}
