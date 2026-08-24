// 前端约定门禁:用测试卡住「绕开全站修复」的复发。
//
// 为什么要有这一条:同一类毛病已经犯过多次——新页面自己重写一个 CopyBtn 裸调
// navigator.clipboard(线上是 http://IP:8080,非安全上下文下该 API 根本不存在,
// 于是复制按钮必失效,而全站早在 ui/copy.tsx 里修好了三层兜底);新页面用原生
// confirm 而不是全站确认弹层;新页面拿 .card-head 拼表单行(那是标题行 flex,
// 一挤就把「时长」竖着拆成两行)。公约写在 CLAUDE.md 里靠人记,测试才靠得住。
//
// 源码用 import.meta.glob(?raw) 读进来,不依赖 node fs(本前端没装 @types/node)。
// 四条判据现在都没有豁免清单了(原先 .card-head 那条挂着 DramaPanel 待整改,已在 P1-4 清掉)。
// 以后再加豁免就是给复发开门:要么整改,要么这条判据本身写错了,别加名单。
import { describe, expect, it } from "vitest";

const RAW = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../test/**", "!../**/*.d.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** 路径统一成 src/ 下的相对形式,如 "ui/copy.tsx"、"pages/PromoPage.tsx" */
const FILES = Object.entries(RAW).map(([p, text]) => ({ path: p.replace(/^\.\.\//, ""), text }));

const isUiLayer = (path: string) => path.startsWith("ui/");

/** 逐行扫描用:整行注释不算违规(公约本身就要在注释里被提到) */
function codeLines(text: string): { no: number; line: string }[] {
  return text.split("\n").map((line, i) => ({ no: i + 1, line })).filter(({ line }) => {
    const t = line.trim();
    return !(t.startsWith("//") || t.startsWith("*") || t.startsWith("/*"));
  });
}

describe("前端约定门禁", () => {
  it("自检:确实扫到了源码", () => {
    expect(FILES.length).toBeGreaterThan(30);
    expect(FILES.map((f) => f.path)).toContain("ui/copy.tsx");
  });

  it("除 ui/copy.tsx 外不许出现裸 navigator.clipboard", () => {
    // 理由:http:// 页面下 navigator.clipboard 是 undefined,裸调必失败。
    // 复制一律走 ui/copy 的 CopyBtn / copyText / copyOrPrompt(三层兜底)。
    const bad = FILES
      .filter((f) => f.path !== "ui/copy.tsx")
      .flatMap((f) => codeLines(f.text).filter(({ line }) => /navigator\.clipboard/.test(line)).map(({ no }) => `${f.path}:${no}`));
    expect(bad, `这些文件在裸调 navigator.clipboard,请改用 ui/copy 的 CopyBtn / copyOrPrompt:\n${bad.join("\n")}`)
      .toEqual([]);
  });

  it("不许在业务层重定义与 ui/ 导出同名的组件(影子组件)", () => {
    // 理由:同名本地组件会静默绕开全站已修好的实现——CopyBtn 就这么被绕过两次。
    const uiExports = new Set<string>();
    for (const f of FILES.filter((f) => isUiLayer(f.path))) {
      for (const m of f.text.matchAll(/export\s+(?:default\s+)?function\s+([A-Z]\w+)/g)) uiExports.add(m[1]);
      // 也认 const 箭头组件形态:ui/ 哪天换写法,判据不能跟着失明
      for (const m of f.text.matchAll(/export\s+(?:default\s+)?const\s+([A-Z]\w+)\s*[=:]/g)) uiExports.add(m[1]);
    }
    expect(uiExports.size).toBeGreaterThan(5); // 自检:正则确实匹配到了 ui/ 的导出
    // 自检:关键共用件必须在清单里——正则失效时这里先红,而不是判据静默放空
    for (const key of ["CopyBtn", "Banner", "EmptyState", "StepBar"]) {
      expect(uiExports.has(key), `ui/ 里没扫到 ${key}(导出清单失效?)`).toBe(true);
    }

    const bad: string[] = [];
    for (const f of FILES.filter((f) => !isUiLayer(f.path))) {
      for (const m of f.text.matchAll(/(?:^|\n)\s*(?:export\s+)?function\s+([A-Z]\w+)/g)) {
        if (uiExports.has(m[1])) bad.push(`${f.path} 重定义了 ${m[1]}`);
      }
    }
    expect(bad, `影子组件——请直接 import ui/ 里的实现:\n${bad.join("\n")}`).toEqual([]);
  });

  it("不许用原生 confirm/alert,一律走 ui/ConfirmDialog", () => {
    // 理由:原生弹窗在 Tauri 桌面端观感割裂,也带不了正文说明与 danger 语义。
    // 两段正则:后者专堵 `window.confirm(`/`window.alert(`——前者的 `[^.\w]` 排掉了
    // 属性调用前缀,对最直白的 window. 写法反而失明。
    const bad: string[] = [];
    for (const f of FILES) {
      for (const { no, line } of codeLines(f.text)) {
        if (/(?:^|[^.\w])(?:confirm|alert)\s*\(/.test(line)
          || /window\s*\.\s*(?:confirm|alert)\s*\(/.test(line)) bad.push(`${f.path}:${no}`);
      }
    }
    expect(bad, `请改用 confirmDialog({ title, body, danger }):\n${bad.join("\n")}`).toEqual([]);
  });

  it("不许拿 .card-head 拼表单行(用 .form-grid/.field/.form-actions)", () => {
    // .card-head 是「标题 + 右侧按钮」的一行 flex:标签与控件在里面抢宽度,
    // 窄窗口下中文标签会被竖着断字,主按钮也混进字段中间。
    //
    // 判据分两条:
    // 1) 旧写法 .card-head + .plan-form 已全部整改、CSS 也删了,这条防它复活;
    // 2) 结构判据——card-head 开头的元素里紧接着出现字段标记(.fl / .field / .field-full),
    //    就是在拿标题行当表单行。只看 card-head 之后 3 行(表单行的标签与控件必然贴着写),
    //    同一行就闭合的 card-head 直接跳过(那 3 行是它的兄弟节点,不算)。
    const bad: string[] = [];
    for (const f of FILES) {
      if (/className="[^"]*card-head[^"]*plan-form/.test(f.text)) bad.push(`${f.path}(.plan-form 复活了)`);
      const lines = f.text.split("\n");
      lines.forEach((line, i) => {
        const head = /className="([^"]*)"/.exec(line);
        if (!head || !head[1].split(/\s+/).includes("card-head")) return;
        if (line.includes("</div>")) return; // 单行闭合,后面是兄弟节点
        for (let j = i; j < Math.min(i + 4, lines.length); j++) {
          for (const m of lines[j].matchAll(/className="([^"]*)"/g)) {
            if (j === i && m.index === head.index) continue;
            const t = m[1].split(/\s+/);
            if (t.includes("fl") || t.includes("field") || t.includes("field-full")) {
              bad.push(`${f.path}:${i + 1}(第 ${j + 1} 行的 .${m[1]} 是字段,不该在 card-head 里)`);
            }
          }
        }
      });
    }
    expect(bad, `表单请用 .form-grid/.field/.form-actions 骨架:\n${bad.join("\n")}`).toEqual([]);
  });
});
