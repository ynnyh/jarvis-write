// 正文分段与段落渲染:阅读器渲染、片段替换、正文点选各处共用同一套分段逻辑。拆自 Reader.tsx。
import type { ReactNode } from "react";

/** 正文分段:按空行/换行切开,去空白;阅读器渲染与片段替换共用同一套分段逻辑 */
export function splitParas(text: string): string[] {
  return text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
}

/** 定位第 idx 个非空段落在原文中的字符区间(与 splitParas 同口径:按 \n 分行、trim、过滤空行)。
 *  用段落序号而非全文 indexOf(selText),避免正文里有重复段落时替换改错位置。 */
export function nthParaSpan(
  source: string, idx: number,
): { start: number; end: number; text: string } | null {
  const re = /[^\n]+/g;
  let count = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const trimmed = m[0].trim();
    if (!trimmed) continue; // 纯空白行不算段落,与 filter(Boolean) 对齐
    if (count === idx) {
      // 收窄到 trim 后的区间,使 text 与 splitParas 产出的段落逐字一致(替换只动正文、不吞行内缩进)
      const lead = m[0].length - m[0].trimStart().length;
      const start = m.index + lead;
      return { start, end: start + trimmed.length, text: trimmed };
    }
    count++;
  }
  return null;
}

/** 正文按空行/换行分段渲染成 <p>,保证可读性;传 onSelect 时段落可点选(片段润色用)。
 *  markedIdx/staleIdx:②档批注的左侧标记(已批注/批注失效标灰),不传则无标记。
 *  renderText:段落文本的自定义渲染(正文实体高亮用),不传则原样渲染纯文本。
 *  实体高亮只在段内插入内联 span,不改段落文本本身,故选区偏移(offsetInPara 量文本长度)与写回不受影响。 */
export function Paragraphs({ text, selectedIdx, onSelect, markedIdx, staleIdx, renderText }: {
  text: string;
  selectedIdx?: number | null;
  onSelect?: (idx: number) => void;
  markedIdx?: Set<number>;
  staleIdx?: Set<number>;
  renderText?: (text: string) => ReactNode;
}) {
  const paras = splitParas(text);
  if (!paras.length) return <div className="muted">(空)</div>;
  return <>{paras.map((p, i) => {
    const cls = [
      onSelect ? "pickable" : "",
      onSelect && selectedIdx === i ? "sel" : "",
      markedIdx?.has(i) ? "para-annotated" : "",
      staleIdx?.has(i) ? "para-annotated-stale" : "",
    ].filter(Boolean).join(" ");
    // 可点选段落 = 按钮语义:键盘可聚焦(Tab)、回车/空格触发、aria-pressed 播报选中态。
    // 空格默认会滚动页面,需 preventDefault。只读阅读模式(无 onSelect)则是纯 <p>,不加任何交互属性。
    return (
      <p
        key={i}
        className={cls || undefined}
        role={onSelect ? "button" : undefined}
        tabIndex={onSelect ? 0 : undefined}
        aria-pressed={onSelect ? selectedIdx === i : undefined}
        onClick={onSelect ? (e) => { e.stopPropagation(); onSelect(i); } : undefined}
        onKeyDown={onSelect ? (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            onSelect(i);
          }
        } : undefined}
      >{renderText ? renderText(p) : p}</p>
    );
  })}</>;
}
