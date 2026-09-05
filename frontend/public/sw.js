/* PWA 最小 Service Worker:只做「存在」以满足安装条件,不做任何缓存。
   不缓存的原因:前端资源由 FastAPI 托管且带 hash 文件名,后端 /api 全是动态请求;
   贸然缓存壳会引入「发版后旧壳+新接口」的错配。更新策略 = 每次打开直接走网络,
   壳资源本身有 HTTP 缓存与版本提醒(前端与 /api/version 比对 commit)。 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(clients.claim()));
self.addEventListener("fetch", (event) => event.respondWith(fetch(event.request)));
