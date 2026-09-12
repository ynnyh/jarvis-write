// write/EntityCard.tsx — 正文实体链接的 hover 卡内容(对标 Codex 的实体浮窗)。
// 纯展示:名字 + 类型/退场徽章 + 别名 + 简介 + 前 N 条关键事实 + 关系(带证据章节)
// + 出场章(可点跳章,onJumpChapter 由外层传;不传则只读)。定位由外层 .entity-pop 负责。
import { CharacterCard } from "../../api";
import { entityTypeLabel } from "./entityLink";

const FACT_LIMIT = 3;
const REL_LIMIT = 3;
const CHAPTER_LIMIT = 8;

export default function EntityCard({ c, onJumpChapter }: { c: CharacterCard; onJumpChapter?: (n: number) => void }) {
  const facts = c.key_facts.slice(0, FACT_LIMIT);
  const relations = c.relations.slice(0, REL_LIMIT);

  function jump(n: number) {
    if (onJumpChapter) onJumpChapter(n);
  }

  return (
    <div className={"entity-card" + (c.retired ? " retired" : "")}>
      <div className="entity-card-head">
        <b className="entity-card-name">{c.name}</b>
        <span className="badge">{entityTypeLabel(c.entity_type)}</span>
        {c.retired && <span className="badge">已退场</span>}
      </div>
      {c.aliases.length > 0 && (
        <div className="muted entity-card-aliases">别名:{c.aliases.join("、")}</div>
      )}
      {c.profile && <div className="entity-card-profile">{c.profile}</div>}
      {facts.length > 0 && (
        <ul className="entity-card-facts">
          {facts.map((f) => (
            <li key={f.id}>
              {f.content}
              <span className="muted">(自第{f.valid_from}章起)</span>
            </li>
          ))}
        </ul>
      )}
      {c.key_facts.length > FACT_LIMIT && (
        <div className="muted entity-card-more">…另有 {c.key_facts.length - FACT_LIMIT} 条事实(见一致性看板)</div>
      )}
      {/* 关系(docs/19 M2):边来自章后抽取,证据是支撑它的原文事实(带章号)——
          「背后的关联关系随时点进去追溯」在浮卡上闭环 */}
      {relations.length > 0 && (
        <div className="entity-card-rels">
          {relations.map((r, i) => (
            <div className="entity-card-rel" key={i}>
              <span>
                {r.other_name}
                {r.other_retired ? "(已退场)" : ""}·{r.description}
                <span className="muted">(自第{r.valid_from}章)</span>
              </span>
              {r.evidence.slice(0, 1).map((ev, j) => (
                <div className="muted entity-card-rel-ev" key={j}>证据·第{ev.chapter}章:{ev.content.slice(0, 30)}</div>
              ))}
            </div>
          ))}
          {c.relations.length > REL_LIMIT && (
            <div className="muted entity-card-more">…另有 {c.relations.length - REL_LIMIT} 条关系(见圣经)</div>
          )}
        </div>
      )}
      {c.appearance_chapters.length > 0 && (
        <div className="muted entity-card-chapters">
          出场:
          {c.appearance_chapters.slice(0, CHAPTER_LIMIT).map((n) => (
            <span key={n}>
              {onJumpChapter ? (
                <span className="entity-card-jump" onClick={() => jump(n)}>第{n}章</span>
              ) : (
                `第${n}章`
              )}
              {"、"}
            </span>
          ))}
          {c.appearance_chapters.length > CHAPTER_LIMIT ? " …" : ""}
        </div>
      )}
    </div>
  );
}
