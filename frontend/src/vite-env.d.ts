/// <reference types="vite/client" />

// 构建时由 Dockerfile 的 ENV VITE_APP_COMMIT 烤入当前部署的 git 提交号,
// 用于更新提醒:与后端 /api/version 返回的 commit 比对,不一致说明浏览器还是旧缓存。
interface ImportMetaEnv {
  readonly VITE_APP_COMMIT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
