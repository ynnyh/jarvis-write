// 抽卡式选择器(游戏卡选):表单内只放「确认态/触发按钮」,真正的选择在模态舞台上——
// 深色舞台 + 居中卡阵 + 错峰翻牌 + 点卡抬金圈,确认才落。列表模式按气质分组折叠,
// 模式偏好记 localStorage(用户上次用哪个,下次默认哪个)。
// 可复用:props 全部数据驱动,情绪命题等其他口味型选择后续可直接换上。
import { useEffect, useMemo, useState } from "react";

export interface GachaCard {
  key: string;
  label: string;
  desc?: string;
  group?: string;
}
export interface GachaGroup {
  key: string;
  label: string;
  desc?: string;
}

// 动画/交互参数集中一处(质感流基准)
export const GACHA_ANIM = { flipMs: 520, staggerMs: 120, handSize: 4 };
const MODE_KEY = "gacha_mode";
const SKIN_KEY = "gacha_skin";

type Mode = "gacha" | "list";
/** 舞台皮肤:auto=跟随整体主题(默认,亮→纸墨/暗→暗夜) / paper / night / kraft */
export type GachaSkin = "auto" | "paper" | "night" | "kraft";
const SKINS: { key: GachaSkin; label: string }[] = [
  { key: "auto", label: "跟随" },
  { key: "paper", label: "纸墨" },
  { key: "night", label: "暗夜" },
  { key: "kraft", label: "牛皮" },
];

function appThemeDark(): boolean {
  return document.documentElement.dataset.theme === "dark";
}

/** auto 时按整体主题解析:亮→paper,暗→night */
function resolveSkin(skin: GachaSkin): Exclude<GachaSkin, "auto"> {
  if (skin === "auto") return appThemeDark() ? "night" : "paper";
  return skin;
}

function loadMode(): Mode {
  try {
    return localStorage.getItem(MODE_KEY) === "list" ? "list" : "gacha";
  } catch {
    return "gacha";
  }
}

function loadSkin(): GachaSkin {
  try {
    const v = localStorage.getItem(SKIN_KEY);
    return SKINS.some((s) => s.key === v) ? (v as GachaSkin) : "auto";
  } catch {
    return "auto";
  }
}

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export default function GachaPicker({ groups, cards, value, onChange }: {
  groups: GachaGroup[];
  cards: GachaCard[];
  value: string;
  onChange: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>(loadMode);
  const [skin, setSkin] = useState<GachaSkin>(loadSkin);
  // 舞台实际渲染用的皮肤;auto 时跟随整体主题(data-theme),且在弹层开着时实时响应切换
  const [effectiveSkin, setEffectiveSkin] = useState<"paper" | "night" | "kraft">(() => resolveSkin(loadSkin()));
  // 舞台内状态:group=null → 大方向步;group=组 key → 抽卡步
  const [group, setGroup] = useState<string | null>(null);
  const [hand, setHand] = useState<GachaCard[]>([]);
  const [drawSeq, setDrawSeq] = useState(0);
  const [staged, setStaged] = useState<string>(""); // 舞台内点选,确认才落
  const [customText, setCustomText] = useState("");

  const chosen = cards.find((c) => c.key === value) ?? null;
  const groupLabel = useMemo(
    () => groups.find((g) => g.key === group)?.label ?? "",
    [groups, group],
  );

  useEffect(() => {
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch { /* 忽略 */ }
  }, [mode]);

  useEffect(() => {
    try {
      localStorage.setItem(SKIN_KEY, skin);
    } catch { /* 忽略 */ }
  }, [skin]);

  useEffect(() => {
    setEffectiveSkin(resolveSkin(skin));
    if (skin !== "auto") return;
    // 跟随模式:观察 <html data-theme> 变化,弹层开着时切主题也实时跟随
    const obs = new MutationObserver(() => setEffectiveSkin(resolveSkin("auto")));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, [skin]);

  function openStage() {
    setOpen(true);
    setGroup(null);
    setStaged("");
  }

  function drawHandCards(g: string, avoid: string): GachaCard[] {
    const inGroup = shuffle(cards.filter((c) => c.group === g));
    const outside = shuffle(cards.filter((c) => c.group !== g && c.key !== avoid));
    const handCards = [...inGroup];
    if (outside.length) handCards.push(outside[0]); // 跨界灵感:换一批永远有新意
    return handCards.slice(0, GACHA_ANIM.handSize);
  }

  function enterGroup(g: string) {
    setGroup(g);
    setHand(drawHandCards(g, staged || value));
    setDrawSeq((n) => n + 1);
    setStaged("");
  }

  function confirmPick(key: string) {
    onChange(key);
    setOpen(false);
    setGroup(null);
    setHand([]);
    setStaged("");
    setCustomText("");
  }

  // ---------- 关闭态:确认态(已选)或触发按钮 ----------
  if (!open) {
    return (
      <div className="gacha-chosen">
        {chosen ? (
          <span className="gacha-chosen-card">
            <b>{chosen.label}</b>
            {chosen.desc && <span className="muted">{chosen.desc}</span>}
          </span>
        ) : (
          <span className="muted" style={{ alignSelf: "center" }}>还没选玩法——抽一手试试运气。</span>
        )}
        <button type="button" className="btn-sm primary" onClick={openStage}>
          {chosen ? "🎴 换一个玩法" : "🎴 抽卡选玩法"}
        </button>
      </div>
    );
  }

  // ---------- 舞台(模态) ----------
  return (
    <div className="gacha-overlay" onClick={() => setOpen(false)}>
      <div className="gacha-modal" data-skin={effectiveSkin} onClick={(e) => e.stopPropagation()}>
        <div className="gacha-modal-head">
          <div className="seg">
            <button type="button" className={mode === "gacha" ? "on" : ""}
              onClick={() => setMode("gacha")}>🎴 抽卡</button>
            <button type="button" className={mode === "list" ? "on" : ""}
              onClick={() => setMode("list")}>☰ 列表</button>
          </div>
          <span className="gacha-modal-title">
            {mode === "gacha" ? (group ? `「${groupLabel}」` : "选个气质大方向") : "全部玩法"}
          </span>
          <div className="gacha-skins" role="group" aria-label="舞台皮肤">
            {SKINS.map((sk) => (
              <button key={sk.key} type="button"
                className={"gacha-skin-dot" + (skin === sk.key ? " on" : "")}
                title={`皮肤:${sk.label}`}
                aria-pressed={skin === sk.key}
                onClick={() => setSkin(sk.key)}>
                {sk.label}
              </button>
            ))}
          </div>
          <button type="button" className="gacha-close" aria-label="关闭"
            onClick={() => setOpen(false)}>×</button>
        </div>

        {mode === "gacha" && group === null && (
          <div className="gacha-groups">
            {groups.map((g, i) => (
              <button key={g.key} type="button" className="gacha-dir-card"
                style={{ animationDelay: `${i * 70}ms` }}
                onClick={() => enterGroup(g.key)}>
                <b>{g.label}</b>
                {g.desc && <span>{g.desc}</span>}
              </button>
            ))}
          </div>
        )}

        {mode === "gacha" && group !== null && (
          <>
            <div className="gacha-stage-head">
              <span className="gacha-stage-hint">
                {staged
                  ? "已扣下这张——满意就「就选它」,或点别的再换"
                  : `一手 ${hand.length} 张${hand.some((c) => c.group !== group) ? ",最后一张是跨界灵感" : ""},点一张扣下`}
              </span>
              <div className="actions">
                <button type="button" className="btn-sm"
                  onClick={() => { setHand(drawHandCards(group, staged || value)); setDrawSeq((n) => n + 1); setStaged(""); }}>
                  换一批
                </button>
                <button type="button" className="btn-sm" onClick={() => { setGroup(null); setHand([]); setStaged(""); }}>
                  ← 换个大方向
                </button>
              </div>
            </div>
            <div className="gacha-hand" key={drawSeq}>
              {hand.map((c, i) => (
                <button key={c.key} type="button"
                  className={"gacha-card" + (staged === c.key ? " staged" : "")}
                  style={{ animationDelay: `${i * GACHA_ANIM.staggerMs}ms`, animationDuration: `${GACHA_ANIM.flipMs}ms` }}
                  onClick={() => setStaged(c.key)}>
                  <span className="gacha-card-inner">
                    <b>{c.label}</b>
                    {c.desc && <span className="gacha-card-desc">{c.desc}</span>}
                    {c.group !== group && <i className="gacha-wild">跨界灵感</i>}
                  </span>
                </button>
              ))}
            </div>
            <div className="gacha-modal-foot">
              <span className="gacha-stage-hint">
                {staged ? `选定:「${cards.find((c) => c.key === staged)?.label ?? staged}」` : "点一张卡片扣下它"}
              </span>
              <div className="actions">
                <button type="button" className="btn-sm" onClick={() => { setGroup(null); setStaged(""); }}>
                  ← 大方向
                </button>
                <button type="button" className="btn-sm primary" disabled={!staged}
                  onClick={() => confirmPick(staged)}>
                  就选它 →
                </button>
              </div>
            </div>
          </>
        )}

        {mode === "list" && (
          <div className="gacha-list">
            {groups.map((g) => (
              <div key={g.key} className="gacha-list-group">
                <div className="gacha-list-head">
                  <b>{g.label}</b>
                  {g.desc && <span className="muted">{g.desc}</span>}
                </div>
                <div className="gacha-all">
                  {cards.filter((c) => c.group === g.key).map((c) => (
                    <button key={c.key} type="button"
                      className={"chip" + (staged === c.key ? " on" : "")}
                      title={c.desc}
                      onClick={() => setStaged(c.key)}>
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            <div className="gacha-list-custom">
              <input type="text" value={customText} maxLength={40}
                placeholder="自定义玩法,如「一只当上店长的猫」"
                onChange={(e) => setCustomText(e.target.value)} />
              <button type="button" className="btn-sm primary"
                disabled={!customText.trim()} onClick={() => confirmPick(customText.trim())}>
                用这个
              </button>
            </div>
            <div className="gacha-modal-foot">
              <span className="gacha-stage-hint">
                {staged ? `选定:「${cards.find((c) => c.key === staged)?.label ?? staged}」` : "点玩法标签扣下它"}
              </span>
              <button type="button" className="btn-sm primary" disabled={!staged}
                onClick={() => confirmPick(staged)}>
                就选它 →
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
