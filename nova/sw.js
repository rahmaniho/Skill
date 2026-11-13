/* نُوا — سرویس‌ورکر: پوستهٔ اپ آفلاین + کش شبکه‌ای-اول برای API
   فقط روی https ثبت می‌شه؛ داخل پیش‌نمایش سندباکس (iframe بدون شبکه) بی‌ضرر است. */
var CACHE = "nova-shell-v2";
var SHELL = ["./", "./index.html", "./styles.css", "./ui.js", "./app.js",
  "./manifest.webmanifest", "./icons/icon.svg", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return Promise.all(SHELL.map(function (u) {
      return c.add(u).catch(function () { /* فایل اختیاری */ });
    }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;

  /* پوستهٔ app: فقط از کش (آفلاین-اول) */
  var path = url.pathname;
  var shellHit = SHELL.some(function (s) { return path.endsWith(s.replace("./", "/")) || path === s; });
  if (req.mode === "navigate" || shellHit) {
    e.respondWith(
      caches.match(req.mode === "navigate" ? "./index.html" : req).then(function (hit) {
        var fresh = fetch(req).then(function (res) {
          if (res.ok) caches.open(CACHE).then(function (c) { c.put(req, res.clone()); });
          return res;
        }).catch(function () { return hit || caches.match("./index.html"); });
        return hit || fresh;
      })
    );
    return;
  }

  /* API: شبکه اول، در دسترس نبودن → کش کوتاه‌مدت */
  if (path.indexOf("/api/") >= 0) {
    e.respondWith(
      fetch(req).then(function (res) {
        if (res.ok) caches.open(CACHE).then(function (c) { c.put(req, res.clone()); });
        return res;
      }).catch(function () { return caches.match(req); })
    );
  }
});
