/* Cache the headquarters shell only. API responses stay uncached. */
const CACHE = "arch-hq-shell-v1";
const FILES = [
  "/command",
  "/command.css",
  "/command.js",
  "/themes/themes.css",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) {
    return;
  }
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
