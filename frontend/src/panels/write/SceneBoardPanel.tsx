// 场景看板(§2.6):让作者在生成过程中直接接手。
//
// 为什么需要它:场景级生成把「生成单元」从章降到了场景,好处是着力面变小——
// 但这只在作者能落到那个面上时才兑现。此前看到第 3 场写歪了,唯一的手段是重写
// 整章,于是写得好的两场也跟着重抽一次签。
//
// 这个面板把四个动作给出来:
//   · 看:本章每场的卡片 + 状态 + 字数 + 验收记录
//   · 改卡:改目标/冲突/情绪指令/张力档——最有价值的干预点,因为卡是 prompt 的输入
//   · 改正文:逐场手动修订(存版本快照)
//   · 定点重生成:只重抽这一场,不动别的
import { useCallback, useEffect, useState } from "react";
import { SceneCard, api } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

interface Props {
  pid: number;
  chapterNumber: number;
}

// 场景状态 → 人话标签 + 语义色。作者不该看 planned/drafted 这类英文枚举。
const STATUS_LABEL: Record<string, string> = {
  planned: "待写",
  drafting: "写作中",
  drafted: "已写(未验收)",
  accepted: "通过",
  rejected: "未通过",
  discarded: "已废弃",
};
const STATUS_CLASS: Record<string, string> = {
  planned: "muted",
  drafting: "muted",
  drafted: "stat-alert",
  accepted: "msg-ok",
  rejected: "msg-err",
  discarded: "muted",
};

// 张力档 → 中文简述(与后端 tension_bus/scene_plan 同口径,仅用于展示)
const TENSION_LABEL: Record<number, string> = {
  1: "蓄力", 2: "收紧", 3: "推进", 4: "高点", 5: "爆发",
};

export default function SceneBoardPanel({ pid, chapterNumber }: Props) {
  const [scenes, setScenes] = useState<SceneCard[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  // 展开的场景 id → 正文(详情端点单独拉,列表不带正文)
  const [openId, setOpenId] = useState<number | null>(null);
  const [text, setText] = useState("");
  // 正在编辑的场景卡草稿
  const [editing, setEditing] = useState<SceneCard | null>(null);

  const load = useCallback(() => {
    api.scenes(pid, chapterNumber)
      .then((d) => { setScenes(d.scenes); setErr(""); })
      .catch((e) => setErr(errMsg(e)));
  }, [pid, chapterNumber]);

  useEffect(() => { load(); }, [load]);

  async function openScene(s: SceneCard) {
    if (openId === s.id) { setOpenId(null); return; }
    setBusy("读取正文…");
    try {
      const d = await api.sceneDetail(pid, s.id);
      setOpenId(s.id);
      setText(d.content || "");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  async function saveText() {
    if (openId == null) return;
    setBusy("保存正文…");
    try {
      const r = await api.updateSceneText(pid, openId, text);
      if (r.changed) {
        toast.ok("已保存该场正文", "存了版本快照。章正文由各场拼接而成,重新拼接后才同步。");
        load();
      } else {
        toast.info("内容没变", "未产生新版本。");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  async function saveCard() {
    if (!editing) return;
    setBusy("保存场景卡…");
    try {
      const r = await api.updateSceneCard(pid, editing.id, {
        title: editing.title,
        goal: editing.goal,
        conflict: editing.conflict,
        emotion_target: editing.emotion_target,
        tension_level: editing.tension_level,
        location: editing.location,
      });
      if (r.changed.length) {
        toast.ok(`已改场景卡:${r.changed.join("、")}`, "该场若是已写会标回「待写」,等您决定是否重生成。");
        setEditing(null);
        load();
      } else {
        toast.info("没有改动");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  async function regen(s: SceneCard) {
    setBusy(`重生成第 ${s.seq} 场…`);
    try {
      await api.regenerateScene(pid, s.id);
      toast.ok(`第 ${s.seq} 场已重生成`, "只动了这一场,其他场未变。");
      load();
      if (openId === s.id) {
        const d = await api.sceneDetail(pid, s.id);
        setText(d.content || "");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  if (!scenes.length) {
    return (
      <div className="card mb-3">
        <div className="card-head"><h3 className="grow">场景看板</h3></div>
        <div className="muted mt-2">
          第 {chapterNumber} 章还没有场景卡。开启「场景级生成」后,生成这一章时会先切分场景。
        </div>
      </div>
    );
  }

  return (
    <div className="card mb-3">
      <div className="card-head">
        <h3 className="grow">场景看板 · 第 {chapterNumber} 章({scenes.length} 场)</h3>
        {busy && <span className="muted"><span className="spin spin-sm" />{busy}</span>}
      </div>
      <div className="card-desc mt-1">
        场景是生成的最小单元。写歪的通常只是某一场——改这一场的卡(卡是输入,改卡最有效)
        或定点重生成,比整章重写划算得多:不会把写得好的场一起换掉。
      </div>

      <div className="mt-2">
        {scenes.map((s) => (
          <div key={s.id} className="card mb-2" style={{ padding: "10px 12px" }}>
            <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
              <b>第 {s.seq} 场 · {s.title || "(未命名)"}</b>
              <span className={STATUS_CLASS[s.status] || "muted"}>
                {STATUS_LABEL[s.status] || s.status}
              </span>
              <span className="muted">
                张力 {s.tension_level}/5({TENSION_LABEL[s.tension_level] || ""})
                · {s.word_count} 字
                {s.rewrite_count > 0 && ` · 改过 ${s.rewrite_count} 次`}
              </span>
            </div>

            {editing?.id === s.id ? (
              <div className="mt-2">
                <label className="fl">标题</label>
                <input className="input" value={editing.title}
                  onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                <label className="fl mt-2">本场目标(读者读完这一场知道了什么 / 谁变了)</label>
                <textarea className="input" rows={2} value={editing.goal}
                  onChange={(e) => setEditing({ ...editing, goal: e.target.value })} />
                <label className="fl mt-2">张力来源(没有张力来源的场景就是白水)</label>
                <textarea className="input" rows={2} value={editing.conflict}
                  onChange={(e) => setEditing({ ...editing, conflict: e.target.value })} />
                <label className="fl mt-2">情绪指令(这一场要让读者感受到什么)</label>
                <input className="input" value={editing.emotion_target}
                  onChange={(e) => setEditing({ ...editing, emotion_target: e.target.value })} />
                <label className="fl mt-2">张力档(1 蓄力 - 5 爆发)</label>
                <input className="input" type="number" min={1} max={5}
                  value={editing.tension_level}
                  onChange={(e) => setEditing({
                    ...editing, tension_level: Number(e.target.value),
                  })} />
                <div className="actions mt-2">
                  <button className="primary btn-sm" disabled={!!busy} onClick={saveCard}>
                    保存场景卡
                  </button>
                  <button className="btn-sm" onClick={() => setEditing(null)}>取消</button>
                </div>
              </div>
            ) : (
              <div className="mt-1">
                <div className="fact-line">目标:{s.goal || "(未指定)"}</div>
                <div className="fact-line">张力来源:{s.conflict || "(未指定)"}</div>
                <div className="fact-line">
                  情绪指令:{s.emotion_target || "(未指定)"} · 地点:{s.location || "(未指定)"}
                </div>
                {s.accept_note && (
                  <div className="fact-line muted">验收:{s.accept_note}</div>
                )}
              </div>
            )}

            <div className="actions mt-2">
              <button className="btn-sm"
                onClick={() => setEditing(editing?.id === s.id ? null : { ...s })}>
                {editing?.id === s.id ? "收起编辑" : "改场景卡"}
              </button>
              <button className="btn-sm" disabled={!!busy} onClick={() => openScene(s)}>
                {openId === s.id ? "收起正文" : "看/改正文"}
              </button>
              <button className="btn-sm" disabled={!!busy} onClick={() => regen(s)}>
                定点重生成
              </button>
            </div>

            {openId === s.id && (
              <div className="mt-2">
                <textarea className="input" rows={10} value={text}
                  onChange={(e) => setText(e.target.value)} />
                <div className="actions mt-2">
                  <button className="primary btn-sm" disabled={!!busy} onClick={saveText}>
                    保存这一场
                  </button>
                  <span className="muted">
                    手改会存版本快照;章正文由各场拼接而成,重新拼接后才同步。
                  </span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
