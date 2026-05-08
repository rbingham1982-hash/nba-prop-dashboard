# Konjure Analytics — Android App

## Open in Android Studio

1. Open **Android Studio** → **File → Open** → select this `android/` folder
2. Let Gradle sync finish (downloads dependencies automatically)
3. **Run on emulator**: the app connects to `http://10.0.2.2:8501` (emulator's alias for your PC localhost)
4. **Run on physical device**: edit `MainActivity.kt` line `BASE_URL` to your PC's LAN IP:
   ```kotlin
   const val BASE_URL = "http://192.168.1.X:8501"   // your PC's IP
   ```
5. Make sure `streamlit run nba_prop_dashboard.py` is running before launching

## Build a release APK (sideload onto any Android phone)

1. **Build → Generate Signed Bundle / APK → APK**
2. Create a keystore if you don't have one (follow the wizard)
3. Select `release` build variant → Finish
4. APK lands in `app/release/app-release.apk`
5. Host that file anywhere (Google Drive, GitHub Releases, Dropbox) and share the download link
6. On the Android phone: **Settings → Apps → Install unknown apps** → allow your browser → open the link → install

## Dark / Light theme
The app reads the phone's system setting automatically.  
`values/themes.xml` = light · `values-night/themes.xml` = dark.

---

## iOS — Options (no App Store)

### Option A — Progressive Web App (FREE, no developer account needed)
iOS users can install the Streamlit site directly from Safari:
1. Deploy the Streamlit app to a public HTTPS URL (Streamlit Community Cloud is free)
2. On iPhone/iPad: open the URL in **Safari** → tap the **Share** button → **Add to Home Screen**
3. The site appears as an app icon on the home screen with full-screen mode

> To enable the "Add to Home Screen" banner and icon, add this to the `<head>` of your site.
> Since Streamlit controls the HTML, add this CSS/meta injection near the top of `nba_prop_dashboard.py`:
> ```python
> st.markdown("""
> <link rel="apple-touch-icon" href="https://your-domain.com/icon.png">
> <meta name="apple-mobile-web-app-capable" content="yes">
> <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
> <meta name="apple-mobile-web-app-title" content="Konjure">
> """, unsafe_allow_html=True)
> ```

### Option B — AdHoc / TestFlight (requires Apple Developer account — $99/year)
1. Enrol at developer.apple.com
2. Use **Capacitor** (`npm install @capacitor/core @capacitor/ios`) to wrap this project into an iOS IPA
3. Distribute via **TestFlight** (up to 10,000 testers) — Apple still hosts the binary but it does NOT appear in the public App Store
4. Or use **AdHoc** distribution + an `.ipa` + `.plist` manifest hosted on HTTPS for a direct install link

### Option C — Enterprise Certificate ($299/year)
Allows a public download link (`itms-services://`) installable by anyone on any device, no App Store.
