/* Weft — the service worker, which does one thing: notifications.
 *
 * There is deliberately no fetch handler. Weft has no build step and no hashed
 * filenames, and the server's no-cache headers are what keep a change from being
 * deployed but not running; a worker that cached the app would undo that.
 *
 * A tap on a notification brings back a Weft window, or opens one, and asks it to show
 * the notifications page. See js/notify.js.
 */

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
