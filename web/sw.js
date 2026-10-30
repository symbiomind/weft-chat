/* Weft — the service worker, which does one thing: notifications.
 *
 * There is deliberately no fetch handler. Weft has no build step and no hashed
 * filenames, and the server's no-cache headers are what keep a change from being
 * deployed but not running; a worker that cached the app would undo that.
 *
 * A push from the server (app/push.py) is drawn here, with Weft closed or open. Only
 * its fixed shape is drawn: a weft's name, capped, and a count. Nothing else in the
 * payload is read, and no link is taken from it, so even a server that was taken over
 * can only show a notification that looks like Weft's and opens Weft.
 *
 * A tap on a notification brings back a Weft window, or opens one, and asks it to show
 * the notifications page. See js/notify.js.
 */

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data?.json() ?? {};
  } catch {
    /* not ours to read: still shown, as the browser requires of every push */
  }
  const name = typeof data.name === "string" && data.name.trim()
    ? data.name.trim().slice(0, 60) : "Weft";
  const count = Number.isInteger(data.count) && data.count > 0 ? Math.min(data.count, 999) : 0;
  const weft = typeof data.weft === "string" ? data.weft.slice(0, 64) : "";
  event.waitUntil(self.registration.showNotification(name, {
    body: count
      ? `${count} notification${count === 1 ? "" : "s"} waiting`
      : "Something is waiting in Weft",
    // The same tag the open window uses, so one weft is one notification either way.
    tag: weft ? `weft-${weft}` : "weft",
    renotify: true,
    icon: "/icons/icon-192.png",
  }));
});

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((client) => "focus" in client);
    if (open) {
      await open.focus();
      open.postMessage({ open: "notifications" });
    } else {
      await self.clients.openWindow("/");
    }
  })());
});
