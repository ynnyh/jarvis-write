// 灵感工作区:碎片 → AI 扩展方案 → 采用为主题;主题可随时改
import { useState } from "react";
import { api, Idea, Project, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";

interface Props { project: Project; onChanged: () => Promise<void>; }

export default function InspirePanel({ project, onChanged }: Props) {
  const [topic, setTopic] = useState(project.topic);
  const [spark, setSpark] = useState("");
  const [tendency, setTendency] = useState<Tendency>(project.global_tendency ?? {});
  const [synopsis, setSynopsis] = useState(project.synopsis ?? "");
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [synBusy, setSynBusy] = useState("");
  const [synMsg, setSynMsg] = useState("");
  const [synErr, setSynErr] = useState("");

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
      setMsg(`已采用方案「${idea.title}」为本书主题,已写入主题框,可继续微调后保存。下一步:去「架构」生成顶层设计。`);
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

  async function genSynopsis() {
    setSynBusy("AI 正在撰写书籍简介(约1分钟)…"); setSynErr(""); setSynMsg("");
    try {
      const r = await api.generateSynopsis(project.id);
      setSynopsis(r.synopsis);
      setSynMsg("简介已生成,可继续修改,点「保存简介」写入项目。");
    } catch (e) { setSynErr(String(e)); } finally { setSynBusy(""); }
  }

  async function saveSynopsis() {
    setSynBusy("保存…"); setSynErr(""); setSynMsg("");
    try {
      await api.patchProject(project.id, { synopsis });
      await onChanged();
      setSynMsg("简介已保存。");
    } catch (e) { setSynErr(String(e)); } finally { setSynBusy(""); }
  }

  const hasTopic = !!project.topic;

  return (
    <>
      {!hasTopic && (
        <div className="notice notice-info" style={{ marginTop: 0 }}>
          第一步:让 AI 帮你想几个故事方向(点「给我灵感」),或者直接在下方「本书主题」里写下你的想法。
        </div>
      )}

      <div className="card">
        <h2>灵感工坊</h2>
        <div className="card-desc">
          丢一个碎片进来(一个画面/一个设定/一句话,留空则按倾向自由发挥),AI 给你 4 个差异化的故事方案。
        </div>
        <div className="input-row">
          <input type="text" value={spark} onChange={(e) => setSpark(e.target.value)}
            placeholder='如:"一个能听见建筑物说话的拆迁评估员"'
            onKeyDown={(e) => e.key === "Enter" && !busy && brainstorm()} />
          <button className="primary" disabled={!!busy} onClick={brainstorm}>
            {busy && <span className="spin" />}给我灵感
          </button>
        </div>
        {busy && <div className="muted mt-2">{busy}</div>}
        {err && <div className="msg-err mt-2">{err}</div>}
        {msg && <div className="msg-ok mt-2">{msg}</div>}

        {ideas.length > 0 && (
          <div className="mt-4">
            {ideas.map((idea, i) => (
              <div key={i} className={"idea-card" + (picked === i ? " picked" : "")}>
                <div className="idea-head">
                  <h3>《{idea.title}》</h3>
                  <button className="primary" disabled={!!busy} onClick={() => adopt(i)}>
                    用这个方案
                  </button>
                </div>
                <div className="idea-line">{idea.logline}</div>
                <div className="muted mt-1">
                  <b>钩子:</b>{idea.hook}
                </div>
                <div className="muted"><b>反转方向:</b>{idea.twist}</div>
              </div>
            ))}
            <button disabled={!!busy} onClick={brainstorm}>都不满意,换一批</button>
          </div>
        )}
      </div>

      <div className="card">
        <h2>{hasTopic ? "本书主题" : "本书主题(选定后可随时修改)"}</h2>
        <div className="card-desc">
          {hasTopic
            ? "这是整本书的\"一句话灵魂\",架构、大纲、正文都会围绕它生成,可随时修改后重新保存。"
            : "采用上面的灵感方案后会自动写到这里,也可以直接自己写。这是整本书的\"一句话灵魂\",架构、大纲、正文都会围绕它生成。"}
        </div>
        <textarea rows={3} value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder="如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…" />
        <label className="fl">全局写作倾向(题材/节奏/结构/基调)</label>
        <div className="hint mb-2">可不选——不选则由 AI 自由发挥;选了会影响所有生成环节的题材、节奏、结构与基调。</div>
        <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
        <div className="actions mt-3">
          <button className="primary" disabled={!!busy} onClick={saveTopic}>保存主题与倾向</button>
          {msg && <span className="msg-ok">{msg}</span>}
        </div>
      </div>

      {project.topic && (
        <div className="card">
          <h2>书籍简介</h2>
          {synopsis.trim() ? (
            <>
              <div className="card-desc">
                展示在「阅读全书」目录栏顶部,也可用于书籍页介绍。可随意修改后保存。
              </div>
              <textarea rows={5} value={synopsis} onChange={(e) => setSynopsis(e.target.value)} />
              <div className="actions mt-3">
                <button className="primary" disabled={!!synBusy} onClick={saveSynopsis}>保存简介</button>
                <button disabled={!!synBusy} onClick={genSynopsis}>
                  {synBusy && <span className="spin" />}重新生成
                </button>
                {synMsg && <span className="msg-ok">{synMsg}</span>}
              </div>
            </>
          ) : (
            <>
              <div className="card-desc">
                还没有简介。让 AI 根据主题{project.genre ? `与题材(${project.genre})` : ""}写一段 150-300 字的网文风简介,吸引人但不剧透结局。
              </div>
              <div className="actions mt-3">
                <button className="primary" disabled={!!synBusy} onClick={genSynopsis}>
                  {synBusy && <span className="spin" />}✨ AI 生成简介
                </button>
                {synMsg && <span className="msg-ok">{synMsg}</span>}
              </div>
            </>
          )}
          {synBusy && <div className="muted mt-2">{synBusy}</div>}
          {synErr && <div className="msg-err mt-2">{synErr}</div>}
        </div>
      )}
    </>
  );
}
