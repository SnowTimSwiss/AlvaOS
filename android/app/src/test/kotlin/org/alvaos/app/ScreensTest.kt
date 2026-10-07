package org.alvaos.app

import android.content.Context
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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import android.os.Looper

/**
 * Every screen of the app opens without a crash, and leaves a picture of
 * itself in app/build/outputs/roborazzi (light and dark) to look at.
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
    }

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

    private fun picture(scenario: ActivityScenario<MainActivity>, name: String) {
        shadowOf(Looper.getMainLooper()).idle()
        scenario.onActivity { it.window.decorView.captureRoboImage("build/outputs/roborazzi/$name.png") }
    }

    @Test
    fun theFirstScreenSaysHowToConnect() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            shadowOf(Looper.getMainLooper()).idle()
            scenario.onActivity { a ->
                val shown = texts(a.window.decorView)
                assertTrue(shown.toString(), "Scan the QR code" in shown)
                assertTrue(shown.toString(), "Type the code instead" in shown)
            }
            picture(scenario, "1-connect")
        }
    }

    @Test
    @Config(qualifiers = "w400dp-h880dp-night-xxhdpi")
    fun theFirstScreenInTheDark() {
        ActivityScenario.launch(MainActivity::class.java).use { picture(it, "1-connect-dark") }
    }

    @Test
    fun everyTabOpensWithOneBarAtTheBottom() {
        Store(ctx).apply {
            server = "http://127.0.0.1:9"
            addresses = listOf(server)
            token = "test"
            user = "tim"
            nasName = "cygnus"
            hubApps = listOf(HubApp("files", "Files", "folder"), HubApp("photos", "Photos", "image"),
                HubApp("calendar", "Calendar", "calendar"), HubApp("chat", "Chat", "message-circle"))
            storeApps = listOf(StoreTile("jellyfin", "Jellyfin", 8096, "/"))
        }
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            shadowOf(Looper.getMainLooper()).idle()
            val labels = mutableListOf<String>()
            scenario.onActivity { a ->
                val nav = find(a.window.decorView, BottomNavigationView::class.java)!!
                for (i in 0 until nav.menu.size()) labels += nav.menu.getItem(i).title.toString()
            }
            assertTrue(labels.toString(), labels == listOf("Files", "Photos", "Apps", "Backup", "Settings"))
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
    fun theBackupWithAlbums() {
        Store(ctx).apply {
            server = "http://127.0.0.1:9"; token = "test"; user = "tim"; nasName = "cygnus"
            hubApps = listOf(HubApp("files", "Files", "folder"), HubApp("photos", "Photos", "image"))
            albums = setOf("Camera", "Screenshots"); backupOn = true
            lastSync = System.currentTimeMillis() - 5 * 60_000
            pendingDeletes = setOf("i1", "i2")
        }
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            shadowOf(Looper.getMainLooper()).idle()
            scenario.onActivity { a ->
                val nav = find(a.window.decorView, BottomNavigationView::class.java)!!
                nav.selectedItemId = MainActivity.TAB_BACKUP
            }
            picture(scenario, "3-backup-on")
        }
    }
}
