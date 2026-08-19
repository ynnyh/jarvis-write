// write/paraEdit.ts — 段落定点替换的纯函数 + 写回链路(「正文即界面」:Prose 气泡与
// AI 窄栏「采用此改写」共用,只有这一份)。
// 定位复用 Reader 导出的 nthParaSpan(按段落序号而非全文 indexOf,正文有重复段落也不串位);
// expected 原文快照做守卫:正文已被别处改动时返回 null,不写回脏数据(与 Reader.applyReplacement 同思想)。
import { api, ChapterDetail } from "../../api";
import { nthParaSpan } from "../../components/Reader";

/** ②档批注(docs/10 §4):段号 + 原文快照(正文变动后据此判失效)+ 一句话意见。
 *  身份=下标+快照,不引入新 id 体系,多处批注并存靠快照比对兜底失效(与写回守卫同思想)。 */
export interface Annotation { paraIdx: number; snapshot: string; note: string }

/** 把 source 正文中第 idx 个非空段落替换为 replacement,返回拼接后的新全文。
 *  段落不存在、replacement 为空、或传了 expected 而原文已对不上(正文已被修改)时返回 null。 */
export function spliceParagraph(
  source: string, idx: number, replacement: string, expected?: string,
): string | null {
  const span = nthParaSpan(source, idx);
  if (!span) return null;
  if (expected !== undefined && span.text !== expected) return null;
  const next = replacement.trim();
  if (!next) return null;
  return source.slice(0, span.start) + next + source.slice(span.end);
}

/** 段内选区定点替换:把第 idx 段文本的 [from,to) 子串替换为 replacement,返回拼接后的新全文。
 *  复用段落级守卫——expectedPara 是选区时该段的原文快照:段落已被别处改动(≠快照)则返回 null,
 *  段落未变时段内 from/to 仍然有效,无需再单独守卫选区文字。
 *  段落不存在、快照对不上、from/to 越界或空、replacement 为空时返回 null。 */
export function spliceSelectionInParagraph(
  source: string, idx: number, from: number, to: number,
  replacement: string, expectedPara: string,
): string | null {
  const span = nthParaSpan(source, idx);
  if (!span || span.text !== expectedPara) return null;
  if (from < 0 || to > span.text.length || from >= to) return null;
  const next = replacement.trim();
  if (!next) return null;
  const newPara = span.text.slice(0, from) + next + span.text.slice(to);
  return source.slice(0, span.start) + newPara + source.slice(span.end);
}

/** 段落定点写回:spliceParagraph 守卫定位 → PUT content 整章写回。
 *  返回更新后的章节详情;守卫失败(正文已被别处改动)返回 null,由调用方提示重选。 */
export async function applyParaReplacement(
  pid: number, chapter: ChapterDetail, idx: number, expected: string, replacement: string,
): Promise<ChapterDetail | null> {
  const source = chapter.final_content || chapter.draft_content;
  const next = spliceParagraph(source, idx, replacement, expected);
  if (next === null) return null;
  return api.editChapterContent(pid, chapter.chapter_number, next);
}

/** 选区定点写回:spliceSelectionInParagraph 守卫定位 → PUT content 整章写回。
 *  返回更新后的章节详情;守卫失败(段落已被别处改动)返回 null,由调用方提示重选。 */
export async function applySelectionReplacement(
  pid: number, chapter: ChapterDetail, idx: number, from: number, to: number,
  expectedPara: string, replacement: string,
): Promise<ChapterDetail | null> {
  const source = chapter.final_content || chapter.draft_content;
  const next = spliceSelectionInParagraph(source, idx, from, to, replacement, expectedPara);
  if (next === null) return null;
  return api.editChapterContent(pid, chapter.chapter_number, next);
}

/** 章尾续写接受:把 continuation 作为新段落追加到正文末尾,PUT content 写回。
 *  expectedFull 是发起续写时的整章正文快照:正文已被别处改动(≠快照,如续写期间手改/多窗口)
 *  则返回 null,由调用方提示重新续写,避免用旧基线覆盖他人改动。continuation 为空也返回 null。 */
export async function appendParagraph(
  pid: number, chapter: ChapterDetail, continuation: string, expectedFull: string,
): Promise<ChapterDetail | null> {
  const source = chapter.final_content || chapter.draft_content;
  if (source.trim() !== expectedFull.trim()) return null;
  const next = continuation.trim();
  if (!next) return null;
  return api.editChapterContent(pid, chapter.chapter_number, source.trimEnd() + "\n\n" + next);
}
