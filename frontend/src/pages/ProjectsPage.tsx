// 项目列表;新建走 /new 创作起步流(建书即建草稿,五步走到点火)
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Project } from "../api";
import TitleSuggest from "../components/TitleSuggest";
import { confirmDialog } from "../ui/ConfirmDialog";
import EmptyState from "../ui/EmptyState";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";

// 项目状态英文值 → 中文徽标(未知值原样兜底)
const PROJECT_STATUS_CN: Record<string, string> = { draft: "草稿", writing: "连载中" };

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  // 整本导入(TXT/DOCX):选文件 + 可改书名,后端解析分卷/章节建为新项目
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importTitle, setImportTitle] = useState("");
  const [importing, setImporting] = useState(false);

  // 重命名编辑态:editingId 为正在改名的项目
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setErr(errMsg(e)));
  }, []);

  async function submitImport() {
    if (!importFile) { setErr("先选一个 .txt / .docx 文件"); return; }
    setImporting(true); setErr("");
    try {
      const r = await api.importBook(importFile, importTitle);
      setImportOpen(false);
      setImportFile(null);
      setImportTitle("");
      toast.ok(`已导入《${r.title}》`, `共 ${r.chapters} 章,可直接阅读、检索或跑已有书翻新`);
      const fresh = await api.listProjects();
      setProjects(fresh);
    } catch (e) {
      toast.err("导入失败", errMsg(e));
    } finally {
      setImporting(false);
    }
  }

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
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function startDelete(p: Project) {
    setEditingId(null);
    if (p.finished) return; // 完本后已置灰,双保险
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
      toast.err("删除失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  // 完本标记:标完本后重命名/删除置灰,需先取消完本才能操作(防误删误改)
  async function toggleFinished(p: Project) {
    const turningOn = !p.finished;
    if (turningOn) {
      const ok = await confirmDialog({
        title: `标记《${p.title}》为「完本」?`,
        body: "标记后该书的「重命名」「删除」「清空正文」将锁定,需先取消完本才能操作。",
        confirmText: "标记完本",
      });
      if (!ok) return;
    }
    setBusy(true); setErr("");
    try {
      const updated = await api.patchProject(p.id, { finished: turningOn });
      setProjects((ps) => ps.map((x) => (x.id === p.id ? updated : x)));
      toast.ok(turningOn ? "已标记为完本" : "已取消完本",
        turningOn ? "重命名/删除已锁定,如需改动先取消完本" : "已恢复可重命名/删除");
    } catch (e) {
      toast.err("保存失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>我的小说</h1>
        {/* 三个工坊的入口在左侧全局导航(ui/Sidebar);这里只留本书创作的主行动 */}
        <div className="actions">
          <button onClick={() => setImportOpen(true)}>导入旧书</button>
          <button className="primary" onClick={() => nav("/new")}>+ 新建小说</button>
        </div>
      </div>

      {importOpen && (
        <div className="dlg-overlay" onClick={() => !importing && setImportOpen(false)}>
          <div className="dlg-content" onClick={(e) => e.stopPropagation()}>
            <h2 className="dlg-title">导入旧书</h2>
            <p className="dlg-body">
              支持 .txt / .docx(≤20MB)。自动识别分卷与章节标题(第X章/序章/番外/后记…),
              没有章标题就按段落长度切章;导入的正文按「已定稿」入库,可直接阅读、检索或跑已有书翻新。
            </p>
            <div className="actions mt-2" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <input
                type="file"
                accept=".txt,.docx,.text"
                onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
              />
              <input
                type="text"
                placeholder="书名(留空用文件名)"
                value={importTitle}
                maxLength={100}
                onChange={(e) => setImportTitle(e.target.value)}
              />
              {err && <span className="badge err">{err}</span>}
            </div>
            <div className="dlg-actions">
              <button disabled={importing} onClick={() => setImportOpen(false)}>取消</button>
              <button className="primary" disabled={importing || !importFile} onClick={submitImport}>
                {importing ? "解析导入中…" : "开始导入"}
              </button>
            </div>
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
                  <span className="badge">{PROJECT_STATUS_CN[p.status] ?? p.status}</span>
                  {p.finished && <span className="badge badge-finished">完本</span>}
                  {p.genre && <span className="badge">{p.genre}</span>}
                </h2>
                <div className="proj-meta">
                  {p.topic || "(未填主题)"}
                </div>
                <div className="proj-progress">
                  <div className="pp-bar">
                    <div className="pp-fill" style={{
                      width: `${Math.min(100, Math.round(((p.written_chapters ?? 0) / Math.max(1, p.target_chapters)) * 100))}%`,
                    }} />
                  </div>
                  <span className="pp-text">
                    {p.written_chapters ?? 0}/{p.target_chapters} 章
                    {(p.total_words ?? 0) > 0 && ` · ${((p.total_words ?? 0) / 10000).toFixed(1)} 万字`}
                  </span>
                </div>
              </Link>
            )}

            <div className="proj-actions">
              {p.setup_state
                ? <Link to={`/new/${p.id}/${p.setup_state}`} className="proj-go">继续创建 →</Link>
                : <Link to={`/project/${p.id}`} className="proj-go">进入 →</Link>}
              <button
                className="btn-sm"
                disabled={p.finished || busy}
                title={p.finished ? "已完本,需先取消完本才能重命名" : undefined}
                onClick={() => startRename(p)}
              >重命名</button>
              <button
                className={`btn-sm ${p.finished ? "" : "danger"}`}
                disabled={p.finished || busy}
                title={p.finished ? "已完本,需先取消完本才能删除" : undefined}
                onClick={() => startDelete(p)}
              >{p.finished ? "已锁定" : "删除"}</button>
              <button className="btn-sm" disabled={busy} onClick={() => toggleFinished(p)}>
                {p.finished ? "取消完本" : "标完本"}
              </button>
            </div>
          </div>
        ))}
      </div>
      {!projects.length && (
        <EmptyState>
          还没有项目。点右上角「新建小说」开始;第一次用可先看 <Link to="/help">「使用指南」</Link>。
        </EmptyState>
      )}
      {err && <div className="msg-err">{err}</div>}
    </>
  );
}
