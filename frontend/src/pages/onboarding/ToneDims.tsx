// 节奏 / 结构 / 基调三个通用维度的 chips(从目录动态取)。拆自 OnboardingFlow.tsx。
import { useEffect, useState } from "react";
import { api, Dimension, Tendency } from "../../api";

export function ToneDims({ tendency, onSet }: {
  tendency: Tendency;
  onSet: (key: string, value: string | string[]) => void;
}) {
  const [dims, setDims] = useState<Dimension[]>([]);
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setDims(cat.dimensions.filter((d) => ["pace", "structure", "tone"].includes(d.key)));
    }).catch(() => undefined);
  }, []);
  return (
    <>
      {dims.map((dim) => (
        <div key={dim.key} className="mt-2">
          <div className="hint">{dim.label}</div>
          <div className="title-chips mt-1">
            {dim.chips.map((c) => {
              const cur = tendency[dim.key];
              const on = dim.select === "multi"
                ? Array.isArray(cur) && cur.includes(c.label)
                : cur === c.label;
              return (
                <button key={c.label} type="button"
                  className={"title-chip sm" + (on ? " on" : "")}
                  onClick={() => {
                    if (dim.select === "multi") {
                      const arr = Array.isArray(cur) ? [...cur] : [];
                      onSet(dim.key, on ? arr.filter((x) => x !== c.label) : [...arr, c.label]);
                    } else {
                      onSet(dim.key, on ? "" : c.label);
                    }
                  }}>{c.label}</button>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );
}
