// write/EntityCard.tsx — 正文实体链接的 hover 卡内容(对标 Codex 的实体浮窗)。
// 纯展示:名字 + 类型/退场徽章 + 别名 + 简介 + 前 N 条关键事实 + 出场章;定位由外层 .entity-pop 负责。
import { CharacterCard } from "../../api";
import { entityTypeLabel } from "./entityLink";

const FACT_LIMIT = 3;

export default function EntityCard({ c }: { c: CharacterCard }) {
  const facts = c.key_facts.slice(0, FACT_LIMIT);
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
      {c.appearance_chapters.length > 0 && (
        <div className="muted entity-card-chapters">
          出场:{c.appearance_chapters.slice(0, 8).map((n) => `第${n}章`).join("、")}
          {c.appearance_chapters.length > 8 ? " …" : ""}
        </div>
      )}
    </div>
  );
}
