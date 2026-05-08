package com.konjure.analytics

import android.annotation.SuppressLint
import android.content.res.Configuration
import android.graphics.Color
import android.os.Bundle
import android.view.MenuItem
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

    // Maps nav drawer item ID → Streamlit tab index
    private val tabIndexMap = mapOf(
        R.id.nav_nba_props    to 0,
        R.id.nav_first_basket to 1,
        R.id.nav_bet_sim      to 2,
        R.id.nav_prizepicks   to 3,
        R.id.nav_daily_blog   to 4,
        R.id.nav_mlb          to 5,
        R.id.nav_disclaimer   to 6,
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
        binding.webview.loadUrl(BASE_URL)

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
        binding.webview.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                loadWithOverviewMode = true
                useWideViewPort = true
                builtInZoomControls = false
                setSupportZoom(false)
                cacheMode = WebSettings.LOAD_DEFAULT
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                mediaPlaybackRequiresUserGesture = false
            }
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView?,
                    request: WebResourceRequest?
                ) = false

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
            setBackgroundColor(Color.TRANSPARENT)
        }
    }

    private fun navigateTo(item: MenuItem) {
        val tabIndex = tabIndexMap[item.itemId]
        if (tabIndex != null) {
            val js = """
                (function(){
                    var tabs = document.querySelectorAll('[data-baseweb="tab"]');
                    if (tabs.length > $tabIndex) {
                        tabs[$tabIndex].click();
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                    }
                })();
            """.trimIndent()
            binding.webview.evaluateJavascript(js, null)
        }
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
          <p>Could not reach the dashboard server.<br>Make sure the Streamlit app is running, then pull down to refresh.</p>
          <button onclick="location.reload()">Retry</button>
        </body>
        </html>
    """.trimIndent()

    companion object {
        // Emulator localhost → 10.0.2.2
        // Physical device on same Wi-Fi → use your PC's LAN IP, e.g. "http://192.168.1.X:8501"
        const val BASE_URL = "http://10.0.2.2:8501"
    }
}
