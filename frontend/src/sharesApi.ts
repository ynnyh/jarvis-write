// 公开分享 API:作者面(创建/列表/撤销,带鉴权)+ 公开面(免登录读,只读)。
// 全部走相对路径;安卓壳(PWA/热更)内页面与 API 同源,无需特殊处理。
import { req } from "./http";

export interface ShareInfo {
  id: number;
  token: string;
  scope: "book" | "chapter";
  chapter_number: number | null;
  revoked: boolean;
  view_count: number;
}

export const sharesApi = {
  create: (pid: number, scope: "book" | "chapter", chapterNumber?: number) =>
    req<ShareInfo>("POST", `/api/projects/${pid}/shares`, {
      scope, chapter_number: scope === "chapter" ? chapterNumber : null,
    }),
  list: (pid: number) => req<ShareInfo[]>("GET", `/api/projects/${pid}/shares`),
  revoke: (pid: number, shareId: number) =>
    req<{ revoked: boolean }>("DELETE", `/api/projects/${pid}/shares/${shareId}`),
};

export interface PublicShare {
  scope: string;
  book_title: string;
  genre: string;
  chapters: { number: number; title?: string; content: string }[];
}
