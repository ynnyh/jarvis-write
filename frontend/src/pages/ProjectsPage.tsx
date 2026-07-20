// 项目列表 + 新建(带全局倾向标签)
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Project, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";
import TitleSuggest from "../components/TitleSuggest";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  // 数字输入用字符串保存原始输入(允许清空),提交时才解析校验
  const [chapters, setChapters] = useState("10");
  const [words, setWords] = useState("3000");
  const [tendency, setTendency] = useState<Tendency>({});

  // 重命名编辑态:editingId 为正在改名的项目
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  // 删除二次确认:deletingId 为待确认项目,chapCount 异步拉取
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [chapCount, setChapCount] = useState<number | null>(null);

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setErr(String(e)));
  }, []);

  async function create() {
    if (!title.trim()) { setErr("请填写书名"); return; }
    // 数字字段提交时解析:空→默认值,非法/越界→提示,不在输入过程中强制弹回
    const chNum = chapters.trim() === "" ? 10 : Number(chapters);
    if (!Number.isInteger(chNum) || chNum < 1 || chNum > 2000) {
      setErr("目标章节数需为 1-2000 的整数"); return;
    }
    const wNum = words.trim() === "" ? 3000 : Number(words);
    if (!Number.isInteger(wNum) || wNum < 200 || wNum > 20000) {
      setErr("每章目标字数需为 200-20000 的整数"); return;
    }
    setBusy(true); setErr("");
    try {
      const p = await api.createProject({
        title: title.trim(),
        topic: topic.trim(),
        genre: (tendency.genre as string) ?? "",
        target_chapters: chNum,
        target_words_per_chapter: wNum,
        global_tendency: tendency,
      });
      nav(`/project/${p.id}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  function startRename(p: Project) {
    setDeletingId(null);
    setEditingId(p.id);
    setEditTitle(p.title);
  }

  async function saveRename(id: number) {
    const t = editTitle.trim();
    if (!t) { setErr("标题不能为空"); return; }
    setBusy(true); setErr("");
    try {
      const updated = await api.renameProject(id, t);
      setProjects((ps) => ps.map((p) => (p.id === id ? updated : p)));
      setEditingId(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  function startDelete(p: Project) {
    setEditingId(null);
    setDeletingId(p.id);
    setChapCount(null);
    api.listChapters(p.id)
      .then((chs) => setChapCount(chs.length))
      .catch(() => setChapCount(null));
  }

  async function confirmDelete(id: number) {
    setBusy(true); setErr("");
    try {
      await api.deleteProject(id);
      setProjects((ps) => ps.filter((p) => p.id !== id));
      setDeletingId(null);
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
              <div className="input-row">
                <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如:雾都诡事" />
                <TitleSuggest topic={topic} genre={(tendency.genre as string) ?? ""} onPick={setTitle} />
              </div>
            </div>
            <div>
              <label className="fl">目标章节数</label>
              <input type="number" value={chapters} min={1} max={2000}
                onChange={(e) => setChapters(e.target.value)} />
            </div>
            <div>
              <label className="fl">每章目标字数</label>
              <input type="number" value={words} min={200} max={20000} step={500}
                onChange={(e) => setWords(e.target.value)} />
            </div>
          </div>
          <label className="fl">核心主题 / 一句话灵感(可留空,建好后到「灵感工坊」让 AI 帮你找)</label>
          <textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder="如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…(没想法就留空)" />
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
          <div key={p.id} className="proj-card">
            {editingId === p.id ? (
              <div className="proj-rename">
                <input
                  type="text"
                  value={editTitle}
                  autoFocus
                  maxLength={100}
                  onChange={(e) => setEditTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveRename(p.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                />
                <div className="actions mt-2">
                  <button className="btn-sm primary" disabled={busy} onClick={() => saveRename(p.id)}>保存</button>
                  <button className="btn-sm" disabled={busy} onClick={() => setEditingId(null)}>取消</button>
                  <TitleSuggest topic={p.topic} genre={p.genre} onPick={setEditTitle} />
                </div>
              </div>
            ) : (
              <Link to={`/project/${p.id}`} className="proj-main">
                <h2 className="proj-title">{p.title}
                  <span className="badge">{p.status}</span>
                  {p.genre && <span className="badge">{p.genre}</span>}
                </h2>
                <div className="proj-meta">
                  {p.topic || "(未填主题)"} · 目标 {p.target_chapters} 章 × {p.target_words_per_chapter} 字
                </div>
              </Link>
            )}

            {deletingId === p.id ? (
              <div className="notice notice-err proj-confirm">
                <div>将删除《{p.title}》及全部 {chapCount ?? "…"} 章正文,不可恢复。确认删除?</div>
                <div className="actions mt-2">
                  <button className="btn-sm danger" disabled={busy} onClick={() => confirmDelete(p.id)}>
                    {busy && <span className="spin" />}确认删除
                  </button>
                  <button className="btn-sm" disabled={busy} onClick={() => setDeletingId(null)}>取消</button>
                </div>
              </div>
            ) : (
              <div className="proj-actions">
                <Link to={`/project/${p.id}`} className="proj-go">进入 →</Link>
                <button className="btn-sm" onClick={() => startRename(p)}>重命名</button>
                <button className="btn-sm danger" onClick={() => startDelete(p)}>删除</button>
              </div>
            )}
          </div>
        ))}
      </div>
      {!projects.length && !creating && (
        <div className="card muted">还没有项目。点右上角「新建小说」开始。</div>
      )}
      {err && !creating && <div className="msg-err">{err}</div>}
    </>
  );
}
