/* Buddy Island service worker: opens offline, and receives screenshots from the Android share sheet. */
const SHELL = "buddy-island-v2";
const SHARE_CACHE = "buddy-share";
const FILES = ["./", "index.html", "island.css", "island.js", "manifest.webmanifest",
               "icons/icon-192.png", "icons/icon-512.png", "../assets/buddy.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("buddy-island-") && key !== SHELL)
                                      .map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function receiveShare(request) {
  const form = await request.formData();
  const cache = await caches.open(SHARE_CACHE);
  const image = form.get("image");
  if (image && typeof image !== "string" && image.size) {
    await cache.put("shared-image", new Response(image, { headers: { "Content-Type": image.type || "image/png" } }));
  }
  const meta = { title: form.get("title") || "", text: form.get("text") || "", url: form.get("url") || "" };
  await cache.put("shared-meta", new Response(JSON.stringify(meta), { headers: { "Content-Type": "application/json" } }));
  return Response.redirect("./?shared=1", 303);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.pathname.endsWith("/share")) {
    event.respondWith(receiveShare(event.request));
    return;
  }
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;                                            // the brain always goes to the network
  }
  // App shell: network first (fresh after updates), the cached copy when offline
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: true }).then((hit) => hit || caches.match("index.html")))
  );
});
