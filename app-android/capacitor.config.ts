import { CapacitorConfig } from '@capacitor/cli';

/**
 * Theta Quant Android wrapper.
 *
 * We DON'T hardcode server.url here. Instead the bundled launcher (www/index.html)
 * asks for / remembers the server URL at runtime and navigates the WebView to it.
 * That keeps the same APK working if the tunnel hostname ever changes, and lets us
 * show a friendly offline screen instead of a WebView error.
 *
 * `allowNavigation` is the ONE compile-time list of hosts the WebView may load
 * in-app. Add your real tunnel hostname here, then rebuild. `*.trycloudflare.com`
 * is included so a quick test tunnel works out of the box.
 */
const config: CapacitorConfig = {
  appId: 'com.thetaquant.app',
  appName: 'Theta Quant',
  webDir: 'www',
  // Static server config for the bundled launcher (capacitor://localhost origin).
  server: {
    androidScheme: 'https',
    allowNavigation: [
      '*.trycloudflare.com',        // free test tunnel
      // 'theta.YOURDOMAIN.com',    // <-- add your Cloudflare named-tunnel host, then rebuild
    ],
  },
  android: {
    // Allow the WebView to keep the login cookie / mixed content off.
    allowMixedContent: false,
  },
};

export default config;
