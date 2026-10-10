# The Sod Father — iOS wrapper

A thin Capacitor shell that loads the live site (https://the-sod-father.onrender.com)
directly -- no local web assets are bundled, same pattern as the Dony and
IT-KB iOS wrappers.

## Opening it

```bash
cd capacitor-app
open ios/App/App.xcodeproj
```

Capacitor 8 uses Swift Package Manager instead of CocoaPods for this
project, so there's no `.xcworkspace` and no `pod install` step -- just
open the `.xcodeproj` directly and Xcode resolves the Capacitor packages
automatically the first time it builds.

- **Bundle ID:** com.thesodfather.app
- **Display name:** The Sod Father

## After changing the Render URL or app config

If the live URL ever changes, update `capacitor.config.ts` and run:

```bash
npx cap sync ios
```

## Next steps (same as the other two apps)

1. Open the project in Xcode, sign it with your Apple Developer account
   under Signing & Capabilities.
2. Add a real app icon (currently using Capacitor's default placeholder).
3. Run on a simulator or your own device to confirm it loads correctly.
4. Archive and upload to App Store Connect / TestFlight when ready --
   double check the bundle ID and app name are free (IT-KB hit a
   collision on both before).

## Splash screen (added)

Uses `@capacitor/splash-screen` with `launchAutoHide: false` -- a fixed
timer doesn't work well here since Render's free tier can take 50s+ to
wake from a cold start. Instead, `templates/base.html` on the web app side
calls `Capacitor.Plugins.SplashScreen.hide()` once the page finishes
loading (with an 8s timeout fallback so it can never get stuck). That
script is a no-op outside the native app, so it's safe to ship on the
regular website too.

If you ever rename the splash background, update both
`capacitor.config.ts` (`backgroundColor`) and the default splash image
under `ios/App/App/Assets.xcassets/Splash.imageset` to match.
