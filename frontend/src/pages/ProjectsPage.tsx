// 项目列表 + 新建(带全局倾向标签)
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Project, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [chapters, setChapters] = useState(10);
  const [words, setWords] = useState(3000);
  const [tendency, setTendency] = useState<Tendency>({});

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setErr(String(e)));
  }, []);

  async function create() {
    if (!title.trim()) { setErr("请填写书名"); return; }
    setBusy(true); setErr("");
    try {
      const p = await api.createProject({
        title: title.trim(),
        topic: topic.trim(),
        genre: (tendency.genre as string) ?? "",
        target_chapters: chapters,
        target_words_per_chapter: words,
        global_tendency: tendency,
      });
      nav(`/project/${p.id}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>我的小说</h1>
        <button className="primary" onClick={() => setCreating(!creating)}>
          {creating ? "收起" : "+ 新建小说"}
        </button>
      </div>

      {creating && (
        <div className="card">
          <h2>新建小说项目</h2>
          <div className="row">
            <div>
              <label className="fl">书名 *</label>
              <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如:霓虹深渊" />
            </div>
            <div>
              <label className="fl">目标章节数</label>
              <input type="number" value={chapters} min={1} max={2000}
                onChange={(e) => setChapters(Number(e.target.value) || 10)} />
            </div>
            <div>
              <label className="fl">每章目标字数</label>
              <input type="number" value={words} min={200} max={20000} step={500}
                onChange={(e) => setWords(Number(e.target.value) || 3000)} />
            </div>
          </div>
          <label className="fl">核心主题 / 一句话灵感(可留空,建好后到「灵感工坊」让 AI 帮你找)</label>
          <textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder="如:底层义体维修师捡到一枚藏着企业罪证的芯片,被卷入猎杀…(没想法就留空)" />
          <label className="fl">全局写作倾向(整本书的默认基调,单次生成时还可临时调整)</label>
          <TendencySelector node="outline" value={tendency} onChange={setTendency} />
          <div className="actions mt-4">
            <button className="primary" disabled={busy} onClick={create}>
              {busy && <span className="spin" />}创建项目
            </button>
            {err && <span className="msg-err">{err}</span>}
          </div>
        </div>
      )}

      <div className="proj-grid">
        {projects.map((p) => (
          <Link key={p.id} to={`/project/${p.id}`} className="proj-card">
            <h2 className="proj-title">{p.title}
              <span className="badge">{p.status}</span>
              {p.genre && <span className="badge">{p.genre}</span>}
            </h2>
            <div className="proj-meta">
              {p.topic || "(未填主题)"} · 目标 {p.target_chapters} 章 × {p.target_words_per_chapter} 字
            </div>
            <span className="proj-go">进入 →</span>
          </Link>
        ))}
      </div>
      {!projects.length && !creating && (
        <div className="card muted">还没有项目。点右上角「新建小说」开始。</div>
      )}
      {err && !creating && <div className="msg-err">{err}</div>}
    </>
  );
}
