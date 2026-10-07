package org.alvaos.app

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Environment
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import com.google.android.material.progressindicator.LinearProgressIndicator

/**
 * The Hub of the NAS inside the app: every Hub app the person may use (Files,
 * Photos, Calendar, Chat and the store apps), as on the computer, signed in
 * with the app's own session. Links to other places open in the browser.
 */
@SuppressLint("SetJavaScriptEnabled")
class HubPage(
    private val ctx: Context,
    private val store: Store,
    private val ui: Ui,
    private val pickFiles: (Intent, ValueCallback<Array<Uri>>) -> Unit,
    private val unreachable: () -> Unit,
) {
    val view = FrameLayout(ctx)
    private val web = WebView(ctx)
    private val bar = LinearProgressIndicator(ctx).apply { isIndeterminate = false; max = 100; visibility = View.GONE }
    private val offline = FrameLayout(ctx).apply { visibility = View.GONE }
    private var loadedFor = ""

    init {
        view.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        view.addView(bar, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        view.addView(offline, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        offline.setBackgroundColor(ui.color(com.google.android.material.R.attr.colorSurface))

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            // The Hub hides what belongs to the app here (its own Sign out).
            userAgentString = "$userAgentString AlvaOSApp/${appVersion(ctx)}"
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val nas = Uri.parse(store.server)
                if (url.host == nas.host) return false                   // the Hub and the NAS's apps
                ctx.startActivity(Intent(Intent.ACTION_VIEW, url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return true
            }
            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                offline.visibility = View.GONE
            }
            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showOffline()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                bar.visibility = if (newProgress < 100) View.VISIBLE else View.GONE
                bar.setProgressCompat(newProgress, true)
            }
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>,
                                           params: FileChooserParams): Boolean {
                pickFiles(params.createIntent().putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE), callback)
                return true
            }
        }
        web.setDownloadListener { url, userAgent, disposition, mime, _ ->
            val name = URLUtil.guessFileName(url, disposition, mime)
            val request = DownloadManager.Request(Uri.parse(url))
                .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url).orEmpty())
                .addRequestHeader("User-Agent", userAgent)
                .setMimeType(mime)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            ctx.getSystemService(DownloadManager::class.java).enqueue(request)
            Toast.makeText(ctx, "Downloading $name…", Toast.LENGTH_SHORT).show()
        }
    }

    /** Opens the Hub, signed in with the app's session (again only if the address or session changed). */
    fun load(force: Boolean = false) {
        val key = store.server + store.token
        if (!force && key == loadedFor) return
        loadedFor = key
        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        for (address in (store.addresses + store.server).distinct()) {
            cookies.setCookie(address, "${org.alvaos.photos.HubClient.COOKIE}=${store.token}; Path=/")
        }
        cookies.flush()
        web.loadUrl(store.server.trimEnd('/') + "/")
    }

    private fun showOffline() {
        offline.removeAllViews()
        offline.visibility = View.VISIBLE
        val box = ui.page(offline)
        box.gravity = Gravity.CENTER_HORIZONTAL
        ui.space(box, 60)
        ui.badge(box, R.drawable.ic_wifi_off, 64)
        ui.headline(box, "${store.nasName.ifEmpty { "Your NAS" }} cannot be reached", 16).gravity = Gravity.CENTER
        ui.body(box, "Is the phone online? At home it uses ${store.addresses.firstOrNull() ?: store.server}; " +
            "away it needs the NAS's internet address (Cloudflare Tunnel) or Tailscale.").gravity = Gravity.CENTER
        ui.button(box, "Try again", top = 20) { unreachable() }
    }

    fun canGoBack() = web.canGoBack()
    fun goBack() = web.goBack()

    /** The Hub's own "Phones and devices" window. */
    fun openDevices() = web.evaluateJavascript("document.getElementById('devices-nav')?.click()", null)

    fun destroy() {
        CookieManager.getInstance().removeAllCookies(null)
        (view.parent as? ViewGroup)?.removeView(view)
        web.destroy()
    }

    companion object {
        fun appVersion(ctx: Context): String = try {
            ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: ""
        } catch (e: Exception) {
            ""
        }
    }
}
