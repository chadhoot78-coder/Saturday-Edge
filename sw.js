/* Football Edge service worker: exists so the browser can show line-move notifications and treat the page as an installable app.
   It deliberately caches nothing — the app must always load the newest index.html from Vercel. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type:"window", includeUncontrolled:true}).then(list => {
    const c = list.find(x => "focus" in x); return c ? c.focus() : self.clients.openWindow("./"); }));
});
