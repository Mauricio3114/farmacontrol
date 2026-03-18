const CACHE_NAME = "pharmaflow-v2";
const urlsToCache = [
  "/",
  "/login",
  "/entregador/app",
  "/static/style.css",
  "/static/app.js",
  "/manifest.json"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});

self.addEventListener("push", event => {
  let data = {
    title: "FarmaControl",
    body: "Você recebeu uma nova notificação.",
    url: "/entregador/app",
    tag: "farmacontrol-push"
  };

  try {
    if (event.data) {
      const payload = event.data.json();
      data = {
        ...data,
        ...payload
      };
    }
  } catch (e) {
    console.error("Erro ao ler payload do push:", e);
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      tag: data.tag || "farmacontrol-push",
      renotify: true,
      requireInteraction: true,
      vibrate: [200, 100, 200, 100, 300],
      data: {
        url: data.url || "/entregador/app"
      }
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();

  const destino = event.notification?.data?.url || "/entregador/app";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if ("focus" in client) {
          client.navigate(destino);
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(destino);
      }
    })
  );
});