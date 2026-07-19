// TendencySelector — 「预设 chips + 我要输入」倾向选择器(全站统一交互)
import { useEffect, useState } from "react";
import { api, Dimension, Tendency } from "../api";

interface Props {
  node: "outline" | "chapter" | "polish";
  value: Tendency;
  onChange: (t: Tendency) => void;
  compact?: boolean;
}

export default function TendencySelector({ node, value, onChange, compact }: Props) {
  const [dims, setDims] = useState<Dimension[]>([]);
  const [customFor, setCustomFor] = useState<string | null>(null);
  const [customText, setCustomText] = useState("");

  useEffect(() => {
    api.tendencyCatalog(node).then((c) => setDims(c.dimensions)).catch(() => setDims([]));
  }, [node]);

  const customs = (value._custom as Record<string, string> | undefined) ?? {};

  function toggle(dim: Dimension, label: string) {
    const next: Tendency = { ...value };
    if (dim.select === "multi") {
      const cur = Array.isArray(next[dim.key]) ? ([...(next[dim.key] as string[])]) : [];
      const i = cur.indexOf(label);
      if (i >= 0) cur.splice(i, 1); else cur.push(label);
      if (cur.length) next[dim.key] = cur; else delete next[dim.key];
    } else {
      if (next[dim.key] === label) delete next[dim.key]; else next[dim.key] = label;
    }
    onChange(next);
  }

  function saveCustom(dimKey: string) {
    const next: Tendency = { ...value };
    const c = { ...customs };
    if (customText.trim()) c[dimKey] = customText.trim(); else delete c[dimKey];
    if (Object.keys(c).length) next._custom = c; else delete next._custom;
    onChange(next);
    setCustomFor(null);
    setCustomText("");
  }

  function isOn(dim: Dimension, label: string): boolean {
    const v = value[dim.key];
    return dim.select === "multi" ? Array.isArray(v) && v.includes(label) : v === label;
  }

  return (
    <div>
      {dims.map((dim) => (
        <div key={dim.key} style={{ marginBottom: compact ? 8 : 14 }}>
          <div className="muted" style={{ marginBottom: 6 }}>
            {dim.label}
            {dim.select === "multi" && <span className="badge">可多选</span>}
          </div>
          <div className="chips">
            {dim.chips.map((c) => (
              <span
                key={c.label}
                className={"chip" + (isOn(dim, c.label) ? " on" : "")}
                title={c.directive}
                onClick={() => toggle(dim, c.label)}
              >
                {c.label}
              </span>
            ))}
            <span
              className={"chip custom" + (customs[dim.key] ? " on" : "")}
              onClick={() => {
                setCustomFor(customFor === dim.key ? null : dim.key);
                setCustomText(customs[dim.key] ?? "");
              }}
            >
              {customs[dim.key] ? `✎ ${customs[dim.key]}` : "+ 我要输入"}
            </span>
          </div>
          {customFor === dim.key && (
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <input
                type="text"
                autoFocus
                placeholder={`自定义${dim.label},如:带一点黑色幽默`}
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveCustom(dim.key)}
              />
              <button className="primary" onClick={() => saveCustom(dim.key)}>确定</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
