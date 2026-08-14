// 写作手法卡:本书自己的手法库,勾选启用即拼成一块注入生成/润色/重写
// 与「创作偏好档案」的分工——档案是整书主张(必须遵循),手法卡是具体写法技巧(可勾选组合)
import { useState } from "react";
import { api, WritingCard } from "../api";
import { useCards, useCardMutations } from "../hooks/queries";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";

interface Props { pid: number; }

const MAX_BODY = 600; // 与后端 engines/tendency/cards.py 的 MAX_BODY_CHARS 一致

// 常用手法预设:点一下填进新建表单,省得从空白开始想
const SAMPLES: { title: string; body: string }[] = [
  {
    title: "冷峻硬汉对峙",
    body: "短句为主,不写心理活动,用动作和物件细节代替情绪词;对话简短,允许答非所问。",
  },
  {
    title: "对话留白与潜台词",
    body: "关键信息不由人物直说,靠停顿、转移话题、动作打断来暗示;每段对话至少留一句不解释的。",
  },
  {
    title: "五感白描环境",
    body: "写环境优先给触觉/听觉/嗅觉的具体物象,不用「仿佛」「宛如」类比喻堆叠,不做总结性抒情。",
  },
  {
    title: "章末钩子",
    body: "结尾落在一个未解的动作或一句未答的话上,不做本章总结,不预告下一章。",
  },
];

export default function CardsPanel({ pid }: Props) {
  const { data: cards = [], isLoading, error } = useCards(pid);
  const { create, update, remove } = useCardMutations(pid);
  const [open, setOpen] = useState(false);          // 新建表单展开
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState<number | null>(null); // 正在编辑的卡 id
  const [draft, setDraft] = useState({ title: "", body: "" });
  const [preview, setPreview] = useState<string | null>(null);

  const enabledCount = cards.filter((c) => c.enabled).length;

  async function add() {
    if (!title.trim() || !body.trim()) { toast.err("卡名和手法内容都要填"); return; }
    try {
      await create.mutateAsync({ title: title.trim(), body: body.trim(), enabled: true });
      setTitle(""); setBody(""); setOpen(false);
      toast.ok("手法卡已新建并启用");
    } catch (e) { toast.err("新建失败", errMsg(e)); }
  }

  async function toggle(c: WritingCard) {
    try {
      await update.mutateAsync({ id: c.id, patch: { enabled: !c.enabled } });
    } catch (e) { toast.err("切换失败", errMsg(e)); }
  }

  async function saveEdit(c: WritingCard) {
    if (!draft.title.trim() || !draft.body.trim()) { toast.err("卡名和手法内容都要填"); return; }
    try {
      await update.mutateAsync({
        id: c.id,
        patch: { title: draft.title.trim(), body: draft.body.trim() },
      });
      setEditing(null);
      toast.ok("已保存");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  async function move(c: WritingCard, dir: -1 | 1) {
    // 交换相邻两张卡的 sort(列表已按 sort 升序),注入顺序随之改变
    const idx = cards.findIndex((x) => x.id === c.id);
    const other = cards[idx + dir];
    if (!other) return;
    try {
      await update.mutateAsync({ id: c.id, patch: { sort: other.sort } });
      await update.mutateAsync({ id: other.id, patch: { sort: c.sort } });
    } catch (e) { toast.err("调序失败", errMsg(e)); }
  }

  async function del(c: WritingCard) {
    const ok = await confirmDialog({
      title: `删除手法卡「${c.title}」?`,
      body: "删除后不可恢复(手法卡是轻量文本,可随时重建)。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(c.id);
      toast.ok("已删除");
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  async function showPreview() {
    try {
      const r = await api.cardsPreview(pid);
      setPreview(r.block || "(当前没有启用的手法卡,不会注入任何内容)");
    } catch (e) { toast.err("预览失败", errMsg(e)); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">写作手法卡 {enabledCount > 0 && <span className="badge">已启用 {enabledCount}</span>}</h3>
        <button className="btn-sm" onClick={showPreview}>看注入内容</button>
        <button className="btn-sm primary" onClick={() => setOpen((v) => !v)}>
          {open ? "收起" : "+ 新建手法卡"}
        </button>
      </div>
      <div className="card-desc">
        一张卡 = 一个具体写法(怎么写对话、怎么白描、怎么收尾)。勾选启用的卡会拼成一块注入
        <b>正文生成、重写、润色</b>——像印章一样盖上去。手法卡是软约束:与情节事实、润色铁律冲突时以后者为准。
      </div>

      {open && (
        <div className="mt-3">
          <div className="profile-field">
            <label className="fl">卡名</label>
            <input value={title} maxLength={100} placeholder="如:冷峻硬汉对峙"
              onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="profile-field">
            <label className="fl">手法内容(注入给 AI 的原话,越具体越有效)</label>
            <textarea rows={3} value={body} maxLength={MAX_BODY}
              placeholder="如:短句为主,不写心理活动,用动作和物件细节代替情绪词"
              onChange={(e) => setBody(e.target.value)} />
            <div className="hint">{body.length}/{MAX_BODY} 字</div>
          </div>
          <div className="hint mt-1">没想法?点一个预设填进来再改:</div>
          <div className="wcard-samples">
            {SAMPLES.map((s) => (
              <button key={s.title} className="btn-sm"
                onClick={() => { setTitle(s.title); setBody(s.body); }}>
                {s.title}
              </button>
            ))}
          </div>
          <button className="primary mt-2" disabled={create.isPending} onClick={add}>
            {create.isPending && <span className="spin" />}新建并启用
          </button>
        </div>
      )}

      {isLoading && <div className="muted mt-2">加载中…</div>}
      {error && <div className="msg-err mt-2">{errMsg(error)}</div>}

      <div className="mt-3">
        {!isLoading && cards.length === 0 && (
          <div className="muted">还没有手法卡。写下你反复要求 AI 的那几条写法,以后不必每次重说。</div>
        )}
        {cards.map((c, i) => (
          <div key={c.id} className={"wcard" + (c.enabled ? " on" : "")}>
            <div className="wcard-head">
              <label className="wcard-title">
                <input type="checkbox" checked={c.enabled} onChange={() => toggle(c)} />
                <b>{c.title}</b>
                {!c.enabled && <span className="muted">(未启用)</span>}
              </label>
              <div className="wcard-ops">
                <button className="btn-sm" disabled={i === 0} title="上移(注入顺序靠前)"
                  onClick={() => move(c, -1)}>↑</button>
                <button className="btn-sm" disabled={i === cards.length - 1} title="下移"
                  onClick={() => move(c, 1)}>↓</button>
                <button className="btn-sm" onClick={() => {
                  setEditing(editing === c.id ? null : c.id);
                  setDraft({ title: c.title, body: c.body });
                }}>{editing === c.id ? "取消" : "编辑"}</button>
                <button className="btn-sm danger" onClick={() => del(c)}>删除</button>
              </div>
            </div>
            {editing === c.id ? (
              <div className="mt-2">
                <input value={draft.title} maxLength={100}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
                <textarea className="mt-1" rows={3} value={draft.body} maxLength={MAX_BODY}
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
                <button className="primary btn-sm mt-1" disabled={update.isPending}
                  onClick={() => saveEdit(c)}>
                  {update.isPending && <span className="spin spin-sm" />}保存
                </button>
              </div>
            ) : (
              <div className="wcard-body muted">{c.body}</div>
            )}
          </div>
        ))}
      </div>

      {preview !== null && (
        <div className="mt-3">
          <div className="rp-label">AI 实际读到的内容(启用卡按当前顺序拼成)</div>
          <pre className="wcard-preview">{preview}</pre>
          <button className="btn-sm" onClick={() => setPreview(null)}>收起</button>
        </div>
      )}
    </div>
  );
}
