// TitleStyleControl — 章节标题风格:预设档 chips(单选,默认朴素)+ 可选自由文本细化。
// 蓝图批量生成(Pillar 2)与「换一批标题」(Pillar 3)共用;value 直接喂后端 resolve。
import { useState } from "react";

export interface TitleStyle {
  style: string;      // 预设 key:plain/hook/suspense/poetic
  directive: string;  // 可选自由文本细化(追加在预设之后)
}

export const DEFAULT_TITLE_STYLE: TitleStyle = { style: "plain", directive: "" };

// key 与后端 TITLE_STYLE_PRESETS 一一对应;label/hint 是给作者看的人话
const PRESETS: { key: string; label: string; hint: string }[] = [
  { key: "plain", label: "朴素出版风", hint: "准确、不夸张,像正经出版小说的目录" },
  { key: "hook", label: "网文钩子感", hint: "带悬念钩子、有记忆点,让人想点开(不剧透反转)" },
  { key: "suspense", label: "悬念冷峻", hint: "冷峻克制,用具体意象营造悬疑不安" },
  { key: "poetic", label: "诗意留白", hint: "含蓄、有意境,善用留白与具象意象" },
];

interface Props {
  value: TitleStyle;
  onChange: (v: TitleStyle) => void;
  compact?: boolean;
}

export default function TitleStyleControl({ value, onChange, compact }: Props) {
  const [showExtra, setShowExtra] = useState(!!value.directive);
  return (
    <div className={compact ? "dim compact" : "dim"}>
      <div className="fl">章节标题风格</div>
      <div className="chips">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            className={"chip" + (value.style === p.key ? " on" : "")}
            title={p.hint}
            onClick={() => onChange({ ...value, style: p.key })}
          >
            {p.label}
          </button>
        ))}
        <button
          type="button"
          className={"chip custom" + (value.directive.trim() ? " on" : "")}
          onClick={() => setShowExtra((s) => !s)}
        >
          {value.directive.trim() ? `✎ ${value.directive.trim()}` : "+ 细化要求"}
        </button>
      </div>
      {showExtra && (
        <div className="input-row mt-2">
          <input
            type="text"
            autoFocus
            placeholder="额外要求(可选),如:多用四字短语、可带地名、别用问句"
            value={value.directive}
            onChange={(e) => onChange({ ...value, directive: e.target.value })}
          />
        </div>
      )}
    </div>
  );
}
