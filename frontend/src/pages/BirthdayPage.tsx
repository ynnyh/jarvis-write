// 生日祝福入口(/birthday):列表与三选一工作台的路由分发 + 建单表单。
// 与情绪短片/宣传片同一套组织:这里只留路由分发与列表;单条工作台(批产→三选一→
// 手卡→出片)拆在 panels/birthday/ 下。建单表单的核心是「寿星资料」——
// 定制感的全部来源,回忆点是分镜的选材红线。
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { BirthdayWish, WishCard, birthdayApi } from "../birthdayApi";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";
import BirthdayWorkspace from "../panels/birthday/BirthdayWorkspace";
import { useBirthdayMeta, wishStatusTone } from "../panels/birthday/shared";

export default function BirthdayPage() {
  const { id } = useParams();
  return id
    ? <BirthdayWorkspace wid={Number(id)} />
    : <BirthdayList />;
}

// ================= 列表 + 新建 =================
export function BirthdayList() {
  const nav = useNavigate();
  const meta = useBirthdayMeta();
  const maxMem = meta?.max_memories ?? 5;
  const maxChars = meta?.memory_max_chars ?? 120;
  const [rows, setRows] = useState<BirthdayWish[] | null>(null);
  // ---- 寿星资料 ----
  const [honoree, setHonoree] = useState("");
  const [relationship, setRelationship] = useState("friend");
  const [milestone, setMilestone] = useState("");
  const [memories, setMemories] = useState<string[]>(["", ""]);
  const [sender, setSender] = useState("");
  // ---- 创作参数 ----
  const [tone, setTone] = useState("surprise");
  const [customTone, setCustomTone] = useState("");
  const [duration, setDuration] = useState(30);
  // 风格包(儿童向角色世界包):选中后画风与世界以包为准,通用画风不再选
  const [pack, setPack] = useState("");
  const [direction, setDirection] = useState("live");
  const [styleHints, setStyleHints] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { setRows((await birthdayApi.list()).wishes); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  async function create() {
    if (!honoree.trim()) { toast.err("称呼必填", "祝福台词要靠它点名"); return; }
    const mems = memories.map((m) => m.trim()).filter(Boolean);
    if (!mems.length) { toast.err("回忆点必填", "至少 1 条——没有回忆点就没有定制感"); return; }
    if (!tone && !customTone.trim()) { toast.err("先选基调", "选一个基调,或填自定义"); return; }
    setBusy(true);
    try {
      const r = await birthdayApi.create({
        honoree_name: honoree.trim(), relationship, milestone: milestone.trim(),
        memories: mems, sender_desc: sender.trim(),
        tone: tone || undefined, custom_tone: customTone.trim(),
        duration_s: duration, pack, direction, style_hints: styleHints.trim(),
      });
      toast.ok("已建,马上开产三个本子", "进工作台看进度;不合适可带意见换一批");
      // autostart:工作台 mount 时自动触发生成,补上"建完还要再点一次生成"的断档
      nav(`/birthday/${r.wish_row.id}`, { state: { autostart: true } });
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setBusy(false); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);
  const updMem = (i: number, v: string) =>
    setMemories(memories.map((m, j) => (j === i ? v.slice(0, maxChars) : m)));

  return (
    <>
      <div className="page-head">
        <h1>生日祝福</h1>
        <button className="btn" onClick={() => nav("/")}>← 我的小说</button>
      </div>
      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            给寿星定制一支生日片
            <span className="muted">30/60 秒,一次三本子三选一</span>
          </h3>
        </div>
        <p className="card-desc">
          填一份寿星资料,AI 一次给 3 个不同切入的本子:开场点名抛悬念 → 回忆杀落在你给的
          回忆点上 → 高潮收在吹蜡烛/拥抱那一帧。给小朋友过生日选下面的**风格包**——TA
          会作为主角进入动画片世界(变身小英雄/当救援队长…),每一格都有TA;每格带三轨
          提示词与切段,出片时传 TA 的照片当参考图走图生视频,拿去即梦/可灵/剪映直接出片。
        </p>
        <div className="form-grid">
          <div className="field">
            <label className="fl" htmlFor="bday-name">
              寿星称呼<span className="hint">祝福台词靠它点名</span>
            </label>
            <input id="bday-name" value={honoree} maxLength={60}
              onChange={(e) => setHonoree(e.target.value)} placeholder="如「老王 / 糖糖 / 妈」" />
          </div>
          <div className="field">
            <span className="fl">与你的关系<span className="hint">决定视角与口吻</span></span>
            <div className="chips">
              {(meta?.relationships ?? []).map((r) => (
                <button key={r.key} type="button"
                  className={"chip" + (relationship === r.key ? " on" : "")}
                  aria-pressed={relationship === r.key}
                  onClick={() => setRelationship(r.key)}>{r.label}</button>
              ))}
            </div>
          </div>
          <div className="field">
            <label className="fl" htmlFor="bday-milestone">
              里程碑<span className="hint">可选;成人礼/大寿会拍成勋章</span>
            </label>
            <input id="bday-milestone" value={milestone} maxLength={80}
              onChange={(e) => setMilestone(e.target.value)} placeholder="点下方快捷项或自填,可空" />
            <div className="chips" style={{ marginTop: 6 }}>
              {(meta?.milestones ?? []).map((m) => (
                <button key={m} type="button"
                  className={"chip" + (milestone === m ? " on" : "")}
                  onClick={() => setMilestone(m)}>{m}</button>
              ))}
            </div>
          </div>
          <div className="field">
            <label className="fl" htmlFor="bday-sender">送出人<span className="hint">可选;决定「我」还是「我们」</span></label>
            <input id="bday-sender" value={sender} maxLength={80}
              onChange={(e) => setSender(e.target.value)} placeholder="如「全家 / 闺蜜团 / 部门同事」" />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="bday-mem0">
              回忆点<span className="hint">分镜只从这些点选材,建议 2-{maxMem} 条,越多越定制</span>
            </label>
            {memories.map((m, i) => (
              <div key={i} className="clip-edit-line">
                <input value={m} maxLength={maxChars}
                  placeholder={i === 0 ? "如「大学时一起在天台看流星雨」" : "再一条:具体场景 / 梗 / 口头禅"}
                  onChange={(e) => updMem(i, e.target.value)} />
                <button className="btn-sm" disabled={memories.length <= 1}
                  onClick={() => setMemories(memories.filter((_, j) => j !== i))}>✕</button>
              </div>
            ))}
            {memories.length < maxMem && (
              <button className="btn-sm" onClick={() => setMemories([...memories, ""])}>+ 加一条回忆点</button>
            )}
          </div>
          <div className="field field-full">
            <span className="fl">基调<span className="hint">三幕节奏怎么走</span></span>
            <div className="chips">
              {(meta?.tones ?? []).map((t) => (
                <button key={t.key} type="button"
                  className={"chip" + (tone === t.key ? " on" : "")}
                  aria-pressed={tone === t.key}
                  onClick={() => { setTone(t.key); setCustomTone(""); }} title={t.directive}>{t.label}</button>
              ))}
              <button type="button"
                className={"chip custom" + (!tone && customTone ? " on" : "")}
                aria-pressed={!tone && !!customTone}
                onClick={() => setTone("")}>自定义</button>
            </div>
          </div>
          {!tone && (
            <div className="field field-full">
              <label className="fl" htmlFor="bday-custom-tone">自定义基调</label>
              <input id="bday-custom-tone" value={customTone} maxLength={40}
                placeholder="如「又燃又好笑的rap生日歌」"
                onChange={(e) => setCustomTone(e.target.value)} />
            </div>
          )}
          <div className="field field-full">
            <span className="fl">
              风格包<span className="hint">小朋友最爱:寿星会作为主角进入这个动画片世界,每一格都有TA</span>
            </span>
            <div className="chips">
              <button type="button"
                className={"chip custom" + (!pack ? " on" : "")}
                aria-pressed={!pack}
                onClick={() => setPack("")}>不用包(通用画风)</button>
              {(meta?.packs ?? []).map((p) => (
                <button key={p.key} type="button"
                  className={"chip" + (pack === p.key ? " on" : "")}
                  aria-pressed={pack === p.key}
                  title={p.directive}
                  onClick={() => setPack(p.key)}>{p.label}</button>
              ))}
            </div>
            {pack && (
              <div className="hint" style={{ marginTop: 6 }}>
                已选风格包:画风与世界观以包为准,下方「画风」不再生效;出片时传小朋友照片当参考图,TA 就会出现在这个世界里。
              </div>
            )}
          </div>
          <div className="field">
            <label className="fl" htmlFor="bday-duration">时长</label>
            <select id="bday-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              <option value={30}>30 秒</option>
              <option value={60}>60 秒</option>
            </select>
          </div>
          {!pack && (
            <div className="field field-full">
              <span className="fl">画风<span className="hint">决定整套风格卡,候选共用</span></span>
              <div className="chips">
                {(meta?.directions ?? []).map((d) => (
                  <button key={d.key} type="button"
                    className={"chip" + (direction === d.key ? " on" : "")}
                    aria-pressed={direction === d.key}
                    onClick={() => setDirection(d.key)}>{d.label}</button>
                ))}
              </div>
              {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
            </div>
          )}
          <div className="field field-full">
            <label className="fl" htmlFor="bday-hints">
              氛围关键词<span className="hint">可选,并进画风卡;如「烛光、老照片质感」</span>
            </label>
            <input id="bday-hints" value={styleHints} maxLength={80}
              onChange={(e) => setStyleHints(e.target.value)}
              placeholder="不填则 AI 按基调自定氛围" />
          </div>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={busy} onClick={create}>
            {busy ? "正在建…" : "产 3 个本子(三选一)"}
          </button>
          <span className="form-actions-tip">三个本子切入各不相同;不合适可带意见整批换,或单条重拍。</span>
        </div>
      </section>

      {rows === null ? <p className="muted">加载中…</p> : rows.length === 0 ? (
        <EmptyState>还没有祝福片。填一份寿星资料试试,三十秒出三个本子。</EmptyState>
      ) : rows.map((r) => (
        <div key={r.id} className="sub-summary ep-row"
          onClick={() => nav(`/birthday/${r.id}`)}>
          <div className="card-head mb-2">
            <b>{(r.clip as WishCard).take ? `《${(r.clip as WishCard).take}》` : ""}{r.honoree_name || "寿星"}的生日片</b>
            {r.milestone && <span className="badge mute">{r.milestone}</span>}
            <span className="badge mute">{r.tone_display}</span>
            <span className="badge mute">{r.duration_s}s</span>
            <span className={`badge ${wishStatusTone(r.status)}`.trim()}>{r.status_cn}</span>
            <span className="grow" />
            <button className="btn-sm" onClick={(e) => {
              e.stopPropagation();
              void (async () => {
                const ok = await confirmDialog({
                  title: "删除这条祝福片企划?",
                  body: "三个本子与已选定的手卡都会一起删掉,不可恢复。",
                  confirmText: "确认删除",
                  danger: true,
                });
                if (!ok) return;
                try { await birthdayApi.remove(r.id); await reload(); }
                catch (err) { toast.err("删除失败", errMsg(err)); }
              })();
            }}>删除</button>
          </div>
          {(r.clip as WishCard).logline && <div className="muted">{(r.clip as WishCard).logline}</div>}
        </div>
      ))}
    </>
  );
}
