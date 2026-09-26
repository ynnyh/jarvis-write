// ModeGate — 屏 0「开哪种书」(docs/22 P0):短故事 / 连载 / 漫剧剧本的模式级分叉 + 档位明示。
// 模式不是参数:后续灵感引导语、三问第 3 问、方案卡形态、(P1)生成管线都随它分叉。
// 档位在这里明示可选(★推荐档会出现在方案卡上)——替代旧版 setup 屏的
// 「AI 静默自动选档」(OnboardingFlow 里那个 effect 已删):用户没选过就写「按方案推荐」。
// 手选过档位 → hook 记 scaleManual,拍板时以手选覆盖方案推荐档。
// 漫剧源书(docs/23):第三张卡。选后先拍频道(男频/女频,决定自动挂哪个爽文包),
// 默认 60 章 × 1500 字(一章≈一集 50 秒成片),开放式连载强制开。
import { useState } from "react";
import { SCALE_PRESETS, scaleDisplay } from "./presets";

export type PickMode = "short" | "serial" | "drama";
export type Audience = "male" | "female";

interface Props {
  mode: string;
  chapters: number;
  words: number;
  onPick: (
    mode: PickMode,
    preset?: { chapters: number; words: number },
    audience?: Audience,
  ) => void;
}

const DRAMA_PRESET = { chapters: 60, words: 1500 };

export default function ModeGate({ mode, chapters, words, onPick }: Props) {
  const [picked, setPicked] = useState<PickMode | null>(
    mode === "short" || mode === "serial" || mode === "drama"
      ? (mode as PickMode)
      : null,
  );
  const [scaleKey, setScaleKey] = useState<string | null>(null);
  const [audience, setAudience] = useState<Audience | null>(null);

  const scale = scaleDisplay(chapters, words);

  return (
    <div className="card">
      <h2>今天想写什么?</h2>
      <div className="card-desc">
        先定个大方向——三种是不同的东西,不是章数多少的区别:短故事一次讲完、当天出成品;
        开书连载有长线引擎,一直写下去;漫剧剧本按「一集一个爽点」写,写完直接进漫剧工坊。
      </div>

      <div className="entry-cards mt-3">
        <button type="button" className={"entry-card" + (picked === "serial" ? " on" : "")}
          onClick={() => setPicked("serial")}>
          <h3>📚 开书连载</h3>
          <p>短/中/长/连载四档,长线引擎吊着读者,写到哪续到哪。</p>
        </button>
        <button type="button" className={"entry-card" + (picked === "short" ? " on" : "")}
          onClick={() => setPicked("short")}>
          <h3>📖 短故事</h3>
          <p>一次讲完,3千~2万字,情绪完整——当天就能读到成品。</p>
        </button>
        <button type="button" className={"entry-card" + (picked === "drama" ? " on" : "")}
          onClick={() => setPicked("drama")}>
          <h3>🎬 漫剧剧本</h3>
          <p>一章≈一集,爽点当章兑现+章末钩子,写完一键改编成漫剧拍成片。</p>
        </button>
      </div>

      {picked === "serial" && (
        <div className="mt-3">
          <div className="pref-row">
            <span className="pref-label">写多长<small>不选 = 按方案推荐的档位来</small></span>
            <div className="scale-cards">
              {SCALE_PRESETS.map((p) => (
                <button key={p.key} type="button"
                  className={"scale-card" + (scaleKey === p.key ? " on" : "")}
                  title={p.desc}
                  onClick={() => setScaleKey(scaleKey === p.key ? null : p.key)}>
                  <b>{p.label}</b>
                  <div className="scale-num">{p.chapters} 章 × {p.words} 字</div>
                  <div className="scale-desc">{p.desc}</div>
                </button>
              ))}
            </div>
            {!scaleKey && scale.decided && (
              <div className="fld-hint">当前:{scale.text}(方案拍板时会带上,除非你手选)</div>
            )}
          </div>
        </div>
      )}

      {picked === "drama" && (
        <div className="mt-3">
          <div className="pref-row">
            <span className="pref-label">给谁看<small>频道决定 AI 出点子的题材货架与爽点侧重</small></span>
            <div className="scale-cards">
              <button type="button"
                className={"scale-card" + (audience === "male" ? " on" : "")}
                onClick={() => setAudience("male")}>
                <b>男频</b>
                <div className="scale-desc">扮猪吃虎/系统流/玄幻修仙/赶山种田/末世求生…碾压感与收集感。</div>
              </button>
              <button type="button"
                className={"scale-card" + (audience === "female" ? " on" : "")}
                onClick={() => setAudience("female")}>
                <b>女频</b>
                <div className="scale-desc">大女主复仇/重生虐渣/豪门恩怨/马甲大佬…情感浓度与关系反转。</div>
              </button>
            </div>
            <div className="fld-hint">
              默认 60 章 × 1500 字(一章≈一集 50 秒成片),开放式连载;写完到「漫剧」页签一键改编。
            </div>
          </div>
        </div>
      )}

      <div className="actions mt-3">
        <button className="primary" disabled={!picked || (picked === "drama" && !audience)}
          onClick={() => {
            if (!picked) return;
            // 短故事的篇幅档由方案卡自带(3千字~2万),屏 0 不重复选
            const preset = picked === "serial"
              ? SCALE_PRESETS.find((p) => p.key === scaleKey)
              : picked === "drama" ? DRAMA_PRESET : undefined;
            onPick(picked, preset, picked === "drama" ? audience! : undefined);
          }}>
          {picked === "short" ? "写个短故事 →"
            : picked === "serial" ? "开一本书 →"
            : picked === "drama" ? "开一部漫剧 →"
            : "选一种 →"}
        </button>
        <span className="hint">拿不准?先跳过,方案卡上会带 AI 推荐档,拍板前都能改。</span>
      </div>
    </div>
  );
}
