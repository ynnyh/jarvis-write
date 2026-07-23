// src/version.ts — 当前前端 bundle 烤入的部署提交号(构建时由 VITE_APP_COMMIT 注入)。
// 本地开发没烤则为 "dev",更新提醒据此跳过(开发态不弹刷新提示)。
export const APP_COMMIT: string = import.meta.env.VITE_APP_COMMIT || "dev";
