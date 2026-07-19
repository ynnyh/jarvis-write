// 灵感工作区:碎片 → AI 扩展方案 → 采用为主题;主题可随时改
import { useState } from "react";
import { api, Idea, Project, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";

interface Props { project: Project; onChanged: () => Promise<void>; }

export default function InspirePanel({ project, onChanged }: Props) {
  const [topic, setTopic] = useState(project.topic);
  const [spark, setSpark] = useState("");
  const [tendency, setTendency] = useState<Tendency>(project.global_tendency ?? {});
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function brainstorm() {
    setBusy("AI 正在扩展灵感方案(约1-2分钟)…"); setErr(""); setMsg("");
    try {
      const r = await api.inspire(spark || topic, tendency, 4);
      setIdeas(r.ideas); setPicked(null);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function adopt(i: number) {
    const idea = ideas[i];
    setPicked(i);
    const newTopic = `${idea.logline}(核心钩子:${idea.hook})`;
    setTopic(newTopic);
    setBusy("写入项目主题…");
    try {
      await api.patchProject(project.id, {
        topic: newTopic,
        title: project.title || idea.title,
        global_tendency: tendency,
      });
      await onChanged();
      setMsg(`已采用方案「${idea.title}」为本书主题。下一步:去「架构」生成顶层设计。`);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function saveTopic() {
    setBusy("保存…"); setErr(""); setMsg("");
    try {
      await api.patchProject(project.id, { topic, global_tendency: tendency });
      await onChanged();
      setMsg("主题与全局倾向已保存。");
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card">
        <h2>本书主题</h2>
        <div className="muted" style={{ marginBottom: 8 }}>
          这是整本书的"一句话灵魂",架构、大纲、正文都会围绕它生成。可以直接写,也可以用下面的灵感工坊帮你找。
        </div>
        <textarea rows={3} value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder="如:底层义体维修师捡到一枚藏着企业罪证的芯片…" />
        <label className="fl">全局写作倾向(题材/节奏/结构/基调,影响所有生成环节)</label>
        <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
        <div style={{ marginTop: 10 }}>
          <button className="primary" disabled={!!busy} onClick={saveTopic}>保存主题与倾向</button>
          {msg && <span className="msg-ok" style={{ marginLeft: 10 }}>{msg}</span>}
        </div>
      </div>

      <div className="card">
        <h2>灵感工坊</h2>
        <div className="muted" style={{ marginBottom: 8 }}>
          丢一个碎片进来(一个画面/一个设定/一句话,留空则按倾向自由发挥),AI 给你 4 个差异化的故事方案。
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input type="text" value={spark} onChange={(e) => setSpark(e.target.value)}
            placeholder='如:"一个能听见建筑物说话的拆迁评估员"'
            onKeyDown={(e) => e.key === "Enter" && !busy && brainstorm()} />
          <button className="primary" disabled={!!busy} onClick={brainstorm} style={{ flexShrink: 0 }}>
            {busy && <span className="spin" />}给我灵感
          </button>
        </div>
        {busy && <div className="muted" style={{ marginTop: 8 }}>{busy}</div>}
        {err && <div className="msg-err" style={{ marginTop: 8 }}>{err}</div>}

        {ideas.length > 0 && (
          <div style={{ marginTop: 14 }}>
            {ideas.map((idea, i) => (
              <div key={i} className={"idea-card" + (picked === i ? " picked" : "")}>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <h3 style={{ flex: 1, margin: 0 }}>《{idea.title}》</h3>
                  <button className="primary" disabled={!!busy} onClick={() => adopt(i)}>
                    用这个方案
                  </button>
                </div>
                <div style={{ marginTop: 6 }}>{idea.logline}</div>
                <div className="muted" style={{ marginTop: 4 }}>
                  <b>钩子:</b>{idea.hook}
                </div>
                <div className="muted"><b>反转方向:</b>{idea.twist}</div>
              </div>
            ))}
            <button disabled={!!busy} onClick={brainstorm}>都不满意,换一批</button>
          </div>
        )}
      </div>
    </>
  );
}
