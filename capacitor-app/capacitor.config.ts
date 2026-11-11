import type { CapacitorConfig } from '@capacitor/cli';

// Thin native shell: the app loads the live Render site directly rather than
// bundling local web assets, same pattern as the IT-KB and Mutual Eats
// (Dony) iOS wrappers. The www/ folder only exists because Capacitor
// requires one to be configured -- it's never actually shown.
const config: CapacitorConfig = {
  appId: 'com.mansfield.app',
  appName: 'Mansfield',
  webDir: 'www',
  server: {
    url: 'https://the-sod-father.onrender.com',
    cleartext: false,
    // Shown instead of an error when the live site can't be reached (no
    // signal, or Render is down). It lists the stories saved for offline
    // reading. The file lives in www/. If the site address above ever
    // changes, update SITE_URL inside www/offline.html too.
    errorPath: 'offline.html'
  },
  plugins: {
    SplashScreen: {
      // launchAutoHide is OFF on purpose: Render's free tier can take 50s+
      // to wake up from a cold start, so a fixed timer would either hide
      // the splash before the site has actually loaded (back to a black
      // screen) or hang around pointlessly on fast loads. Instead the site
      // itself calls Capacitor.Plugins.SplashScreen.hide() once it's ready
      // (see the inline script in templates/base.html), with a timeout
      // fallback there too so the splash can never get stuck forever.
      launchAutoHide: false,
      backgroundColor: '#1b4332',
      androidSplashResourceName: 'splash',
      showSpinner: true,
      spinnerColor: '#faf6ec'
    }
  }
};

export default config;
