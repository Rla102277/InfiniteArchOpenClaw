// Minimal service worker so the OS installs as an app. It caches nothing: the OS is local and
// every view must be live. It only exists so Chrome and Safari offer "Install".
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
