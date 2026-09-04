import { useEffect, useState } from "react";
import type { DramaCharacterCard, DramaSceneCard } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { RefThumb } from "../../ui/RefThumb";
import { CharacterCard, api } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { PasteBox } from "./PasteBox";

// ================= 角色卡 / 场景卡 =================
export function AssetsSection({ pid, cards, scenes, onChanged }: {
  pid: number;
  cards: DramaCharacterCard[];
  scenes: DramaSceneCard[];
  onChanged: (cards: DramaCharacterCard[], scenes: DramaSceneCard[]) => void;
}) {
  const { run } = useJob();
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  // 「选角色生成」:从故事圣经勾人(按出场章数排序,默认勾前 12),勾谁出谁——
  // 治「圣经里第 13 个之后的角色永远没卡」;不勾走上面按钮全自动按戏份取前 12
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [bibleChars, setBibleChars] = useState<CharacterCard[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());

  async function generate(entityIds?: number[]) {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{
        cards: DramaCharacterCard[]; skipped_locked: number; scenes: DramaSceneCard[];
        characters_total?: number | null; characters_shown?: number | null;
      }>(
        () => dramaApi.generateCharacters(pid, entityIds),
        { kind: `drama-chars-${pid}`, onStage: setStage },
      );
      if (r) {
        const fresh = await dramaApi.getCharacters(pid);
        onChanged(fresh.cards, fresh.scenes);
        const lockedNote = r.skipped_locked ? `,${r.skipped_locked} 张锁定卡未动` : "";
        const truncNote =
          !entityIds?.length && r.characters_total && r.characters_shown
          && r.characters_total > r.characters_shown
            ? `;圣经共 ${r.characters_total} 个角色,已按戏份取前 ${r.characters_shown} 个`
            : "";
        toast.ok("资产卡已生成", `角色 ${r.cards.length} 张${lockedNote}${truncNote}`);
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  // 展开/收起选角色面板:展开时拉一次故事圣经人物卡(按出场章数降序,默认勾前 12)
  async function togglePicker() {
    if (pickerOpen) { setPickerOpen(false); return; }
    setPickerOpen(true); setPickerLoading(true);
    try {
      const r = await api.characters(pid);
      const sorted = [...r.characters]
        .filter((c) => c.entity_type === "character" && !c.retired)
        .sort((a, b) =>
          (b.appearance_chapters?.length ?? 0) - (a.appearance_chapters?.length ?? 0) || a.id - b.id);
      setBibleChars(sorted);
      setPicked(new Set(sorted.slice(0, 12).map((c) => c.id)));
    } catch (e) { setErr(errMsg(e)); } finally { setPickerLoading(false); }
  }

  function togglePick(id: number) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 20) next.add(id);
      return next;
    });
  }

  async function castVoices() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{ cards: DramaCharacterCard[]; skipped_locked: number }>(
        () => dramaApi.generateVoiceCast(pid),
        { kind: `drama-voice-${pid}`, onStage: setStage },
      );
      if (r) {
        const fresh = await dramaApi.getCharacters(pid);
        onChanged(fresh.cards, scenes);
        toast.ok("声线选型已生成", "每个角色带 TTS 平台选型建议与朗读指示");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  /** 批量出定妆照提示词:只补还没有的,不覆盖手改过的(要覆盖点单卡「重出提示词」)。 */
  async function refPrompts() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{ cards: DramaCharacterCard[]; generated: number; assembled?: number }>(
        () => dramaApi.genRefPrompts(pid),
        { kind: `drama-refsheet-${pid}-all`, onStage: setStage },
      );
      if (r) {
        onChanged(r.cards, scenes);
        // assembled = 模型没给、由引擎按「构图+外貌锚+画风锚」确定性拼的条数。
        // 如实说出来:它照样能用,但用户有权知道哪几条不是 AI 写的、想重出可以重出。
        const made = r.generated + (r.assembled || 0);
        toast.ok(
          made ? `${made} 张定妆照提示词已就绪` : "都已经有了",
          made
            ? (r.assembled
                ? `其中 ${r.assembled} 张由引擎按外貌锚+画风锚拼好(模型这次没给),照样能用;想换写法点那张卡的「重出提示词」`
                : "拿去生图站先出参考图,再上传回来")
            : "想重写某一张,点那张卡上的「重出提示词」",
        );
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">② 角色卡与场景卡 <span className="muted">人物一致性</span></h3>
        <button disabled={busy || cards.length === 0} onClick={refPrompts}
          title="先出一张角色参考图,后面每格拿它当参考图,才真锁得住脸">出定妆照</button>
        <button disabled={busy || cards.length === 0} onClick={castVoices}>声线选型</button>
        <button disabled={busy} onClick={togglePicker}>
          {pickerOpen ? "收起选角色" : "从圣经选角色"}
        </button>
        <button className="primary" disabled={busy} onClick={() => generate()}>
          {cards.length ? "重新生成" : "AI 生成资产卡"}
        </button>
      </div>
      <p className="card-desc">
        从故事圣经批量出「锁定外貌段」:角色出场的那格分镜会逐字嵌入这段描述,
        同一个人不换脸;「出定妆照」再给每个角色出一条参考图提示词——先出一张正面半身定妆照
        上传回来,之后每格「参考图 + 提示词」出图,人物一致性才从文字层落到像素层。
        「声线选型」给每个角色补 TTS 平台选型建议与朗读指示;手动调过的卡点「锁定」,重跑不覆盖。
        每张卡的<b>性别单列一栏</b>——女角色被 AI 写成男的,就在那儿点「女」拍板,
        再点那张卡的「重出这张卡」让 AI 按性别重写(定妆照提示词要一起换,再点「重出提示词」)。
      </p>
      {busy && <Banner stage={stage} text="AI 正在设计角色视觉卡…" />}
      {err && <div className="msg-err">{err}</div>}
      {pickerOpen && (
        <div className="sub-summary mb-2">
          <div className="card-head mb-2">
            <b>从故事圣经选角色(已选 {picked.size}/20)</b>
            <span className="muted">按出场章数排序;不选就用「AI 生成资产卡」全自动按戏份取前 12</span>
          </div>
          {pickerLoading && <p className="muted">正在读故事圣经…</p>}
          <div className="chips">
            {bibleChars.map((c) => (
              <label key={c.id} className={"chip" + (picked.has(c.id) ? " on" : "")}>
                <input type="checkbox" checked={picked.has(c.id)}
                  onChange={() => togglePick(c.id)} />
                {" "}{c.name}
                {c.appearance_chapters?.length ? (
                  <span className="muted">({c.appearance_chapters.length}章)</span>
                ) : null}
              </label>
            ))}
          </div>
          <div className="rp-actions mt-2">
            <button className="primary btn-sm" disabled={busy || picked.size === 0}
              onClick={() => generate(bibleChars.filter((c) => picked.has(c.id)).map((c) => c.id))}>
              {busy && <span className="spin spin-sm" />}按所选生成({picked.size})
            </button>
          </div>
        </div>
      )}
      {cards.map((c) => <CharCardRow key={c.id} pid={pid} card={c} onSaved={(nc) => {
        onChanged(cards.map((x) => (x.id === nc.id ? nc : x)), scenes);
      }} />)}
      {scenes.length > 0 && (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>场景卡({scenes.length})</b></div>
          {scenes.map((s) => (
            <div key={s.id} className="mb-2">
              <b>{s.name}</b>
              <div className="muted">{s.appearance_cn}</div>
            </div>
          ))}
        </div>
      )}
      {cards.length === 0 && !busy && (
        <p className="hint">还没生成。角色来自故事圣经——写了几章、圣经里有角色后即可生成。</p>
      )}
      <p className="hint drama-compliance">
        配音合规:请用 TTS 平台的<b>正版音色库</b>;声音受法律保护,<b>切勿克隆他人声音</b>
        (包括「像某明星/某配音演员」的模仿);商用发布前先确认所选音色的<b>商用授权</b>。
      </p>
    </div>
  );
}

// 性别是硬约束:生成/重出时逐字下发给模型,定妆照与每格提示词都跟着它走。
// 单列一栏而不是埋在外貌文本里——埋着的话英文轨(appearance_en)那边根本改不到。
const GENDERS: { key: DramaCharacterCard["gender"]; label: string }[] = [
  { key: "female", label: "女" },
  { key: "male", label: "男" },
  { key: "other", label: "其他" },
  { key: "", label: "未定" },
];
const GENDER_LABEL: Record<string, string> = { female: "女", male: "男", other: "其他", "": "未定" };

function CharCardRow({ pid, card, onSaved }: {
  pid: number; card: DramaCharacterCard; onSaved: (c: DramaCharacterCard) => void;
}) {
  const { run } = useJob();
  const [draft, setDraft] = useState(card);
  const [dirty, setDirty] = useState(false);
  const [refBusy, setRefBusy] = useState(false);
  const [cardBusy, setCardBusy] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState("");

  useEffect(() => { setDraft(card); setDirty(false); }, [card]);

  async function save(extra?: Partial<DramaCharacterCard>, note?: string) {
    try {
      const r = await dramaApi.patchCharacter(pid, card.id, { ...draft, ...extra });
      onSaved(r.card);
      setDirty(false);
      if (note) toast.ok(note);
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  /** 性别点一下就存:它是硬约束,不该跟别的编辑一起攒在「保存」里。 */
  async function pickGender(g: DramaCharacterCard["gender"]) {
    if (g === draft.gender) return;
    setDraft({ ...draft, gender: g });
    await save({ gender: g }, `性别已改为「${GENDER_LABEL[g]}」`);
  }

  /** 只重出这一张卡:按上面拍板的性别重写外貌/服饰/声线(锁定的卡也覆盖)。 */
  async function regenCard() {
    if (dirty && !await confirmDialog({
      title: "这张卡有还没保存的修改",
      body: "重出会用 AI 的新版本覆盖你改的内容。",
      confirmText: "覆盖重出",
      danger: true,
    })) return;
    setCardBusy(true);
    try {
      const r = await run<{ card: DramaCharacterCard }>(
        () => dramaApi.regenCharacter(pid, card.id),
        { kind: `drama-charcard-${pid}-${card.id}` },
      );
      if (r?.card) {
        onSaved(r.card);
        toast.ok(
          `${r.card.name} 的角色卡已重写`,
          (r.card.gender ? `按「${GENDER_LABEL[r.card.gender]}」重写了外貌/服饰/声线;` : "外貌/服饰/声线已重写;")
          + "定妆照提示词不会自动跟着变,要一起换就再点「重出提示词」",
        );
      }
    } catch (e) { toast.err("重出角色卡失败", errMsg(e)); } finally { setCardBusy(false); }
  }

  /** 只给这一张角色重出定妆照提示词(批量按钮只补缺,不覆盖手改过的)。 */
  async function regenRef() {
    setRefBusy(true);
    try {
      const r = await run<{ cards: DramaCharacterCard[]; generated: number; assembled?: number }>(
        () => dramaApi.genRefPrompts(pid, [card.name]),
        { kind: `drama-refsheet-${pid}-${card.name}` },
      );
      const fresh = r?.cards.find((c) => c.id === card.id);
      if (fresh) {
        onSaved(fresh);
        toast.ok(
          `${card.name} 的定妆照提示词已就绪`,
          r?.assembled ? "模型这次没给,已由引擎按外貌锚+画风锚拼好" : "",
        );
      }
    } catch (e) { toast.err("出定妆照提示词失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function pickFile(file: File | undefined) {
    if (!file) return;
    setRefBusy(true);
    try {
      const r = await dramaApi.uploadRef(pid, card.id, file);
      onSaved(r.card);
      toast.ok("定妆照已上传", "这一格出图时把它当参考图传给生图站");
    } catch (e) { toast.err("上传失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function addLink() {
    const url = link.trim();
    if (!url) { setLinkOpen(false); return; }
    setRefBusy(true);
    try {
      const r = await dramaApi.linkRef(pid, card.id, url);
      onSaved(r.card);
      setLink(""); setLinkOpen(false);
      toast.ok("已记下外链", "生图站链接常带时效,建议下载后改用上传");
    } catch (e) { toast.err("保存外链失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function removeRef(index: number) {
    if (!await confirmDialog({ title: "删掉这张定妆照?", confirmText: "删除", danger: true })) return;
    try {
      const r = await dramaApi.deleteRef(pid, card.id, index);
      onSaved(r.card);
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  // ===== 音色参考(完整档对白出片)=====
  const [voiceBusy, setVoiceBusy] = useState(false);

  async function pickVoice(file: File | undefined) {
    if (!file) return;
    setVoiceBusy(true);
    try {
      const r = await dramaApi.uploadVoice(pid, card.id, file);
      onSaved(r.card);
      toast.ok(`${card.name} 的音色参考已上传`, "对白格点「本站直接出片」就会按这个声音配音对口型");
    } catch (e) { toast.err("音色上传失败", errMsg(e)); } finally { setVoiceBusy(false); }
  }

  async function removeVoice() {
    if (!await confirmDialog({
      title: "删掉这段音色参考?",
      body: "删后这个角色的对白格出片回退普通出片(不配音)。",
      confirmText: "删除", danger: true,
    })) return;
    setVoiceBusy(true);
    try {
      const r = await dramaApi.deleteVoice(pid, card.id);
      onSaved(r.card);
    } catch (e) { toast.err("删除失败", errMsg(e)); } finally { setVoiceBusy(false); }
  }

  return (
    <div className="sub-summary">
      <div className="card-head mb-2">
        <b>{draft.name}</b>
        <span className="muted">性别</span>
        <span className="gender-pick">
          {GENDERS.map((g) => (
            <button key={g.key || "none"}
              className={"btn-sm" + (draft.gender === g.key ? " primary" : "")}
              disabled={cardBusy}
              title="性别是硬约束:重出这张卡、出定妆照、出每格提示词都照它写"
              onClick={() => void pickGender(g.key)}>{g.label}</button>
          ))}
        </span>
        <span className="grow" />
        <button className="btn-sm" disabled={cardBusy} onClick={() => void regenCard()}
          title="按上面拍板的性别,让 AI 重写这张卡的外貌/服饰/声线(锁定的卡也会覆盖)">
          {cardBusy ? "重出中…" : "重出这张卡"}
        </button>
        <button className={"btn-sm" + (card.locked ? " primary" : "")}
          onClick={() => save({ locked: !card.locked }, card.locked ? "已解锁" : "已锁定")}>
          {card.locked ? "🔒 已锁定" : "锁定"}
        </button>
        {dirty && <button className="btn-sm primary" onClick={() => save(undefined, "已保存")}>保存</button>}
      </div>
      {/* 描述与拍板的性别打架(常见:女角色被写成「剑眉入鬓/青年男声」)。
          不自动改文字——女扮男装是正当写法,只能提示,由用户判断改哪边。 */}
      {card.gender_conflict && <div className="msg-err mb-2">⚠ {card.gender_conflict}</div>}
      <p className="hint">下面每一栏都能直接改,改完点右上「保存」;性别点一下就存。</p>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">锁定外貌段(注入每格分镜)</span><CopyBtn text={draft.appearance_cn} /></div>
        <textarea rows={3} value={draft.appearance_cn}
          onChange={(e) => { setDraft({ ...draft, appearance_cn: e.target.value }); setDirty(true); }} />
      </div>
      {/* 英文轨也得能改:生图站吃的是这条,只改中文那条等于没改 */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">英文外貌关键词(生图站实际吃这条)</span>
          <CopyBtn text={draft.appearance_en} />
        </div>
        <textarea rows={2} value={draft.appearance_en}
          placeholder="young woman, oval face, pale blue robe…"
          onChange={(e) => { setDraft({ ...draft, appearance_en: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">标志服饰</span><CopyBtn text={draft.outfit_cn} /></div>
        <input value={draft.outfit_cn}
          onChange={(e) => { setDraft({ ...draft, outfit_cn: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">声线(配音用)</span><CopyBtn text={draft.voice_desc} /></div>
        <input value={draft.voice_desc}
          onChange={(e) => { setDraft({ ...draft, voice_desc: e.target.value }); setDirty(true); }} />
      </div>
      {/* 音色参考(完整档对白出片):indextts2 克隆这段人声,让该角色在分镜里开口说话 */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">音色参考(完整档对白出片:按这个声音开口说话)</span>
          <span className="grow" />
          <label className="btn-sm" style={{ cursor: "pointer" }}>
            {draft.voice_ref ? "换一段" : "上传音色"}
            <input type="file" accept="audio/mpeg,audio/wav,.mp3,.wav" hidden disabled={voiceBusy}
              onChange={(e) => { void pickVoice(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {!!draft.voice_ref && (
            <button className="btn-sm" disabled={voiceBusy} onClick={() => void removeVoice()}>删除</button>
          )}
        </div>
        <VoicePreview pid={pid} cid={card.id} src={draft.voice_ref} />
        <p className="hint">
          5-10 秒<b>干净人声</b>(别带背景音乐),MP3/WAV ≤8MB,重传即换。
          对白格点「本站直接出片」时按它克隆嗓音再对口型;没传的对白格回退普通出片(不说话)。
        </p>
      </div>
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">定妆照(锁脸的关键一步)</span>
          <span className="grow" />
          <label className="btn-sm" style={{ cursor: "pointer" }}>
            上传参考图
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={refBusy}
              onChange={(e) => { void pickFile(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          <button className="btn-sm" disabled={refBusy} onClick={() => setLinkOpen(!linkOpen)}>贴外链</button>
          <button className="btn-sm" disabled={refBusy} onClick={() => void regenRef()}>
            {refBusy ? "处理中…" : card.ref_prompt_cn ? "重出提示词" : "出定妆照提示词"}
          </button>
        </div>
        <p className="hint">
          文字描述只能把「不像」压小,压不到零。正确做法:先用下面这段生成<b>一张</b>正面半身定妆照,
          上传到这里存好;之后每一格出图,都在生图站点「上传参考图」把它传上去 + 粘这一格的提示词,
          人物才真的前后一致。
        </p>
        {linkOpen && (
          <div className="card-head mb-2">
            <input value={link} placeholder="粘生图站的图片地址(http/https)"
              onChange={(e) => setLink(e.target.value)} />
            <button className="btn-sm primary" disabled={refBusy} onClick={() => void addLink()}>保存</button>
          </div>
        )}
        {draft.ref_images.length > 0 && (
          <div className="ref-thumbs">
            {draft.ref_images.map((img, i) => (
              <RefThumb key={`${img.src}-${i}`} image={img} alt="定妆照"
                loadBlob={() => dramaApi.refBlobUrl(pid, card.id, i)}
                footLeft={<span className="muted">{img.kind === "url" ? "外链" : "已上传"}</span>}
                onDelete={() => void removeRef(i)} />
            ))}
          </div>
        )}
        {draft.ref_prompt_cn
          ? <PasteBox paste={draft.ref_paste} rows={4} />
          : <p className="hint">还没有定妆照提示词——点上面「出定妆照提示词」(需要先定好画风)。</p>}
      </div>
      {(draft.tts_hint || draft.reading_notes) && (
        <div className="media-field">
          <div className="card-head mb-2"><span className="muted">TTS 选型建议</span><CopyBtn text={draft.tts_hint} /></div>
          <div className="hint">{draft.tts_hint}</div>
          {draft.reading_notes && <div className="hint">朗读指示:{draft.reading_notes}</div>}
        </div>
      )}
    </div>
  );
}

/** 音色参考试听:<audio src> 带不了 Authorization 头,取 blob 转本地 URL(同图片缩略图)。 */
function VoicePreview({ pid, cid, src }: { pid: number; cid: number; src?: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!src) { setUrl(""); return; }
    let revoke = "";
    let alive = true;
    dramaApi.voiceBlobUrl(pid, cid)
      .then((u) => { if (alive) { revoke = u; setUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => { if (alive) setUrl(""); });
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [pid, cid, src]);
  if (!src || !url) return null;
  return <audio controls src={url} preload="none" style={{ width: "100%", maxWidth: 320 }} />;
}
