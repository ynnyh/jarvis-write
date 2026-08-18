// 概念卡的字段网格展示 + 稳定 key。拆自 OnboardingFlow.tsx。
import { Concept, CONCEPT_FIELDS } from "../../api";

export function ConceptBrief({ c }: { c: Concept }) {
  return (
    <div className="concept-grid">
      {CONCEPT_FIELDS.filter((f) => c[f.key]?.trim()).map((f) => (
        <div key={f.key} className="concept-field">
          <span className="cf-label">{f.label}</span>
          <span className="cf-value">{c[f.key]}</span>
        </div>
      ))}
    </div>
  );
}

// 概念卡的稳定 key(logline 可能为空,拼主角字段兜底)
export function conceptKey(c: Concept) {
  return (c.logline || "").slice(0, 24) + "|" + (c.protagonist || "").slice(0, 8);
}
