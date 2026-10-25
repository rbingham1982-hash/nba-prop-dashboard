package com.konjure.analytics

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.res.Configuration
import android.net.Uri
import android.os.Bundle
import android.view.MenuItem
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import com.konjure.analytics.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var toggle: ActionBarDrawerToggle

    // Drawer item -> the page it opens. Dashboard sections open by link (?sport=..., read by
    // the sport menu in nba_prop_dashboard.py), not by clicking the Nth tab: the May build
    // did that, and every later change to the tabs sent its menu to the wrong place.
    private val pages = mapOf(
        R.id.nav_nba     to section("nba"),
        R.id.nav_wnba    to section("wnba"),
        R.id.nav_mlb     to section("mlb"),
        R.id.nav_nfl     to section("nfl"),
        R.id.nav_fantasy to section("fantasy"),
        R.id.nav_builder to section("builder"),
        R.id.nav_games   to section("games"),
        R.id.nav_pro     to section("pro"),
        R.id.nav_record  to "$SITE_URL/record/",
        R.id.nav_report  to "$SITE_URL/report/",
        R.id.nav_about   to "$SITE_URL/about.html",
        R.id.nav_terms   to "$SITE_URL/terms.html",
    )

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setSupportActionBar(binding.toolbar)

        // Hamburger / back arrow
        toggle = ActionBarDrawerToggle(
            this,
            binding.drawerLayout,
            binding.toolbar,
            R.string.navigation_drawer_open,
            R.string.navigation_drawer_close
        )
        binding.drawerLayout.addDrawerListener(toggle)
        toggle.syncState()

        setupWebView()
        binding.webview.loadUrl(section("nba"))

        // Pull-to-refresh reloads the WebView
        binding.swipeRefresh.setColorSchemeResources(R.color.konjure_primary)
        binding.swipeRefresh.setOnRefreshListener {
            binding.webview.reload()
        }

        binding.navView.setNavigationItemSelectedListener { item ->
            navigateTo(item)
            true
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        // Remote debugging only in debug builds; a release build should not expose the page.
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        binding.webview.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                databaseEnabled = true
                loadWithOverviewMode = true
                useWideViewPort = true
                builtInZoomControls = false
                setSupportZoom(false)
                // The page is live data over a websocket, so the HTTP cache only holds the
                // app shell and fonts; LOAD_NO_CACHE re-downloaded those on every launch.
                cacheMode = WebSettings.LOAD_DEFAULT
                mixedContentMode = if (BuildConfig.DEBUG) WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                                   else WebSettings.MIXED_CONTENT_NEVER_ALLOW
                mediaPlaybackRequiresUserGesture = false
                allowContentAccess = false
                allowFileAccess = false
                // Identify as Chrome so Streamlit's WebSocket handshake succeeds
                userAgentString = "Mozilla/5.0 (Linux; Android 14; Samsung Galaxy S25) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
            }
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                    android.util.Log.d("KonjureWebView", "${msg.message()} (${msg.sourceId()}:${msg.lineNumber()})")
                    return true
                }
                override fun onProgressChanged(view: WebView?, newProgress: Int) {
                    if (newProgress == 100) binding.swipeRefresh.isRefreshing = false
                }
            }
            webViewClient = object : WebViewClient() {
                // Konjure's own pages stay in the app; anything else (a sportsbook, X,
                // beehiiv) opens in the phone's browser.
                override fun shouldOverrideUrlLoading(
                    view: WebView?,
                    request: WebResourceRequest?
                ): Boolean {
                    val host = request?.url?.host ?: return false
                    if (OWN_HOSTS.any { host == it || host.endsWith(".$it") }) return false
                    if (BuildConfig.DEBUG && host.startsWith("192.168.")) return false   // local dev server
                    return try {
                        startActivity(Intent(Intent.ACTION_VIEW, request.url))
                        true
                    } catch (e: ActivityNotFoundException) {
                        false
                    }
                }

                override fun onPageFinished(view: WebView?, url: String?) {
                    binding.swipeRefresh.isRefreshing = false
                    injectDarkModeHint()
                }

                override fun onReceivedError(
                    view: WebView?,
                    errorCode: Int,
                    description: String?,
                    failingUrl: String?
                ) {
                    view?.loadData(offlinePage(), "text/html", "UTF-8")
                    binding.swipeRefresh.isRefreshing = false
                }
            }
        }
    }

    private fun navigateTo(item: MenuItem) {
        pages[item.itemId]?.let { binding.webview.loadUrl(it) }
        item.isChecked = true
        binding.drawerLayout.closeDrawer(GravityCompat.START)
    }

    // Pass the system dark/light setting into the page as a CSS class
    private fun injectDarkModeHint() {
        val isDark = (resources.configuration.uiMode and
                Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        val js = if (isDark) {
            "document.documentElement.classList.add('android-dark');"
        } else {
            "document.documentElement.classList.remove('android-dark');"
        }
        binding.webview.evaluateJavascript(js, null)
    }

    override fun onBackPressed() {
        when {
            binding.drawerLayout.isDrawerOpen(GravityCompat.START) ->
                binding.drawerLayout.closeDrawer(GravityCompat.START)
            binding.webview.canGoBack() ->
                binding.webview.goBack()
            else ->
                super.onBackPressed()
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        toggle.onConfigurationChanged(newConfig)
    }

    private fun offlinePage() = """
        <!DOCTYPE html>
        <html>
        <head>
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <style>
            body{background:#13151f;color:#9294a8;font-family:sans-serif;
                 display:flex;align-items:center;justify-content:center;
                 height:100vh;margin:0;flex-direction:column;text-align:center;padding:2rem;}
            .logo{font-size:3rem;margin-bottom:1rem;}
            h2{color:#818cf8;margin:0 0 .5rem;font-size:1.3rem;letter-spacing:.12em;text-transform:uppercase;}
            p{font-size:.9rem;line-height:1.6;max-width:300px;}
            button{margin-top:1.5rem;padding:.7rem 2rem;background:#4f46e5;color:#fff;
                   border:none;border-radius:8px;font-size:.9rem;cursor:pointer;}
          </style>
        </head>
        <body>
          <div class="logo">🏆</div>
          <h2>Konjure Analytics</h2>
          <p>The dashboard is waking up or your connection dropped.<br>After a quiet spell the first load can take up to a minute. Pull down or tap Retry.</p>
          <button onclick="location.href='${section("nba")}'">Retry</button>
        </body>
        </html>
    """.trimIndent()

    companion object {
        // The hosted dashboard. Local dev: emulator "http://10.0.2.2:8501", a phone on the same
        // Wi-Fi "http://192.168.X.X:8501" (debug builds allow plain http for this).
        const val BASE_URL = "https://nba-prop-dashboard-dhmu5b4fonzesfkydqwznu.streamlit.app/"
        const val SITE_URL = "https://konjureanalytics.com"
        val OWN_HOSTS = listOf("streamlit.app", "konjureanalytics.com", "10.0.2.2", "localhost")

        // embed=true drops Streamlit's own header and "Manage app" button, as the website does.
        fun section(key: String) = "${BASE_URL}?embed=true&embed_options=dark_theme&sport=$key"
    }
}
