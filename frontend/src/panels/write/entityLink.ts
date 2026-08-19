// write/entityLink.ts — 正文内故事圣经实体链接的纯函数层(对标 Novelcrafter Codex)。
// 把「人物名 + 别名」建成索引,在段落文本里非重叠地切出实体命中片段,交给渲染层高亮 + hover 卡。
// 纯字符串运算、不碰 DOM:段内切分不依赖节点结构,故 Prose 的选区偏移计算(offsetInPara 量文本长度)
// 不受高亮 span 影响;这里只产出片段,写回/选区仍以原文字符串为准。
import { CharacterCard } from "../../api";

/** 索引项:一个可匹配词条(名字或别名)→ 其所属人物卡。长词优先排序,避免「张三」被「张」抢先。 */
export interface EntityTerm { term: string; entity: CharacterCard; }

/** 段落切分后的片段:纯文本段 或 命中实体段(携带整张人物卡供 hover 卡渲染)。 */
export type Segment = { text: string } | { text: string; entity: CharacterCard };

/** 片段是否命中了实体(窄化用)。 */
export function isEntitySeg(s: Segment): s is { text: string; entity: CharacterCard } {
  return "entity" in s;
}

const TYPE_CN: Record<string, string> = {
  character: "人物", location: "地点", item: "物品", faction: "势力",
};

/** 实体类型中文名(故事圣经目前只回人物,其余为将来非人物实体预留)。 */
export function entityTypeLabel(t: string): string {
  return TYPE_CN[t] ?? "实体";
}

/** hover 卡/title 兜底用的一行摘要:「名字(类型[· 已退场]):简介前 80 字」。 */
export function entitySummary(c: CharacterCard): string {
  const head = `${c.name}(${entityTypeLabel(c.entity_type)}${c.retired ? " · 已退场" : ""})`;
  const p = (c.profile || "").trim().replace(/\s+/g, " ");
  if (!p) return head;
  return `${head}:${p.length > 80 ? p.slice(0, 80) + "…" : p}`;
}

/** 由人物卡列表建实体索引:名字 + 全部别名。
 *  - 长度 < 2 的词条丢弃(单字名/别名噪声大,会把正文的常用字全高亮);
 *  - 同一词条只留最先出现的(名字先于别名、靠前人物先于靠后);
 *  - 按词条长度降序,匹配时长词优先,防止短词抢占更长专名的前缀。 */
export function buildEntityIndex(characters: CharacterCard[]): EntityTerm[] {
  const seen = new Set<string>();
  const terms: EntityTerm[] = [];
  for (const c of characters) {
    for (const raw of [c.name, ...(c.aliases || [])]) {
      const term = (raw || "").trim();
      if (term.length < 2 || seen.has(term)) continue;
      seen.add(term);
      terms.push({ term, entity: c });
    }
  }
  terms.sort((a, b) => b.term.length - a.term.length);
  return terms;
}

/** 把段落文本切成「纯文本 / 实体命中」交替的片段序列(非重叠、从左到右、长词优先)。
 *  索引为空或无命中时返回单个纯文本片段(渲染层据此省去包裹,零开销)。
 *  首字符分桶只在候选桶内做 startsWith,避免每个字符都遍历全部词条。 */
export function segmentParagraph(text: string, index: EntityTerm[]): Segment[] {
  if (!index.length || !text) return [{ text }];
  // 按词条首字符分桶(索引已按长度降序 → 每个桶内也保持降序)
  const buckets = new Map<string, EntityTerm[]>();
  for (const t of index) {
    const arr = buckets.get(t.term[0]);
    if (arr) arr.push(t); else buckets.set(t.term[0], [t]);
  }

  const segs: Segment[] = [];
  let buf = "";
  let i = 0;
  while (i < text.length) {
    let hit: EntityTerm | null = null;
    const cand = buckets.get(text[i]);
    if (cand) {
      for (const t of cand) {
        if (text.startsWith(t.term, i)) { hit = t; break; }
      }
    }
    if (hit) {
      if (buf) { segs.push({ text: buf }); buf = ""; }
      segs.push({ text: hit.term, entity: hit.entity });
      i += hit.term.length;
    } else {
      buf += text[i];
      i++;
    }
  }
  if (buf) segs.push({ text: buf });
  return segs;
}
