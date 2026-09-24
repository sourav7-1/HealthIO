/*
 * Health Io service worker: shows medication reminders sent with Web Push and opens the
 * app when one is tapped. It stores nothing and caches nothing (health data never goes
 * into the service worker). Acting on a dose always happens inside the signed-in app:
 * a notification action only opens the app with that action preselected.
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "Health Io", body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "Health Io";
  const options = {
    body: payload.body || "",
    tag: payload.tag,
    renotify: Boolean(payload.tag),
    requireInteraction: true,
    icon: "/icon.svg",
    badge: "/icon.svg",
    data: { url: payload.url || "/", ...(payload.data || {}) },
    // Most browsers show at most two actions.
    actions: (payload.actions || []).slice(0, 2),
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = new URL(data.url || "/", self.location.origin);
  if (event.action) target.searchParams.set("action", event.action);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (new URL(client.url).origin === target.origin) {
          await client.focus();
          client.postMessage({ type: "open-reminder", url: target.pathname + target.search });
          return;
        }
      }
      await self.clients.openWindow(target.href);
    })(),
  );
});
