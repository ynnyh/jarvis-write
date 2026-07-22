// 项目列表;新建走 /new 创作起步流(建书即建草稿,五步走到点火)
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Project } from "../api";
import TitleSuggest from "../components/TitleSuggest";
import { confirmDialog } from "../ui/ConfirmDialog";
import { toast } from "../ui/Toaster";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  // 重命名编辑态:editingId 为正在改名的项目
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setErr(String(e)));
  }, []);

  function startRename(p: Project) {
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

  async function startDelete(p: Project) {
    setEditingId(null);
    // 拉真实章节数给确认弹层,让用户知道要删掉多少东西
    const count = await api.listChapters(p.id).then((chs) => chs.length).catch(() => null);
    const ok = await confirmDialog({
      title: `删除《${p.title}》?`,
      body: `将删除该项目及全部 ${count ?? "?"} 章正文、大纲、故事圣经,不可恢复。`,
      confirmText: "确认删除",
      danger: true,
    });
    if (!ok) return;
    setBusy(true); setErr("");
    try {
      await api.deleteProject(p.id);
      setProjects((ps) => ps.filter((x) => x.id !== p.id));
      toast.ok(`已删除《${p.title}》`);
    } catch (e) {
      toast.err("删除失败", String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>我的小说</h1>
        <button className="primary" onClick={() => nav("/new")}>+ 新建小说</button>
      </div>

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
            ) : p.setup_state ? (
              // 未完成起步流的草稿:引导继续
              <Link to={`/new/${p.id}/${p.setup_state}`} className="proj-main">
                <h2 className="proj-title">{p.title}
                  <span className="badge badge-draft">创建未完成</span>
                </h2>
                <div className="proj-meta">{p.topic || "还没定概念"} · 继续创建 →</div>
              </Link>
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

            <div className="proj-actions">
              {p.setup_state
                ? <Link to={`/new/${p.id}/${p.setup_state}`} className="proj-go">继续创建 →</Link>
                : <Link to={`/project/${p.id}`} className="proj-go">进入 →</Link>}
              <button className="btn-sm" onClick={() => startRename(p)}>重命名</button>
              <button className="btn-sm danger" disabled={busy} onClick={() => startDelete(p)}>删除</button>
            </div>
          </div>
        ))}
      </div>
      {!projects.length && (
        <div className="card muted">
          还没有项目。点右上角「新建小说」开始;第一次用可先看 <Link to="/help">「使用指南」</Link>。
        </div>
      )}
      {err && <div className="msg-err">{err}</div>}
    </>
  );
}
