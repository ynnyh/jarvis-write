// 抽卡式选择器(灵感工坊「玩法」选择):先选大方向(视觉气质组)→ 每次抽 4 张卡
// (本组洗牌 + 1 张跨界灵感)→ 点卡选中;「换一批」重洗重翻,「全部玩法」是逃生门。
// 动画走质感流:3D 翻牌 + 错峰入场 + 旧金描边微光;纯 CSS,无第三方依赖。
// 可复用:props 全部数据驱动,情绪命题等其他口味型选择后续可直接换上。
import { useMemo, useState } from "react";

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

// 动画参数集中一处(质感流基准):翻牌时长/错峰间隔/手牌数,想加码改这里即可
export const GACHA_ANIM = { flipMs: 520, staggerMs: 110, handSize: 4 };

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
  // phase: group=null → 选大方向;group=组 key → 抽卡。
  // drawSeq 变化重挂手牌容器(翻牌动画从零重放);showAll = 全部玩法逃生门。
  const [group, setGroup] = useState<string | null>(null);
  const [hand, setHand] = useState<GachaCard[]>([]);
  const [drawSeq, setDrawSeq] = useState(0);
  const [showAll, setShowAll] = useState(false);

  const chosen = cards.find((c) => c.key === value) ?? null;

  function drawHandCards(g: string, avoid: string): GachaCard[] {
    const inGroup = shuffle(cards.filter((c) => c.group === g));
    const outside = shuffle(cards.filter((c) => c.group !== g && c.key !== avoid));
    const handCards = [...inGroup];
    if (outside.length) handCards.push(outside[0]); // 跨界灵感:换一批永远有新意
    return handCards.slice(0, Math.max(GACHA_ANIM.handSize, Math.min(inGroup.length + 1, GACHA_ANIM.handSize)));
  }

  function enterGroup(g: string) {
    setGroup(g);
    setHand(drawHandCards(g, value));
    setDrawSeq((n) => n + 1);
    setShowAll(false);
  }

  function pick(key: string) {
    onChange(key);
    setGroup(null);
    setHand([]);
    setShowAll(false);
  }

  const groupLabel = useMemo(
    () => groups.find((g) => g.key === group)?.label ?? "",
    [groups, group],
  );

  // —— 已选中且未在抽卡流里:收起为确认态 ——
  if (chosen && group === null && !showAll) {
    return (
      <div className="gacha-chosen">
        <span className="gacha-chosen-card">
          <b>{chosen.label}</b>
          {chosen.desc && <span className="muted">{chosen.desc.slice(0, 30)}{chosen.desc.length > 30 ? "…" : ""}</span>}
        </span>
        <button type="button" className="btn-sm" onClick={() => setShowAll(true)}>换一个玩法</button>
      </div>
    );
  }

  // —— 第一步:大方向(视觉气质组) ——
  if (!group) {
    return (
      <div className="gacha-groups">
        {groups.map((g, i) => (
          <button key={g.key} type="button"
            className="gacha-dir-card"
            style={{ animationDelay: `${i * 70}ms` }}
            onClick={() => enterGroup(g.key)}>
            <b>{g.label}</b>
            {g.desc && <span>{g.desc}</span>}
          </button>
        ))}
        {showAll && (
          <div className="gacha-all">
            {cards.map((c) => (
              <button key={c.key} type="button" className="chip" onClick={() => pick(c.key)}>
                {c.label}
              </button>
            ))}
            <button type="button" className="chip custom"
              onClick={() => { onChange(""); setGroup(null); setShowAll(false); }}>
              自定义…
            </button>
          </div>
        )}
        {!showAll && (
          <button type="button" className="linkbtn" onClick={() => setShowAll(true)}>
            不抽了,直接看全部玩法 →
          </button>
        )}
      </div>
    );
  }

  // —— 第二步:抽卡 ——
  const wildcard = hand.find((c) => c.group !== group);
  return (
    <div className="gacha-stage">
      <div className="gacha-stage-head">
        <span className="muted">
          「{groupLabel}」一手 {hand.length} 张{wildcard ? ",最后一张是跨界灵感,别急着换" : ""}
        </span>
        <div className="actions">
          <button type="button" className="btn-sm"
            onClick={() => { setHand(drawHandCards(group, value)); setDrawSeq((n) => n + 1); }}>
            换一批
          </button>
          <button type="button" className="btn-sm" onClick={() => { setGroup(null); setHand([]); }}>
            ← 换个大方向
          </button>
        </div>
      </div>
      <div className="gacha-hand" key={drawSeq}>
        {hand.map((c, i) => (
          <button key={c.key} type="button"
            className={"gacha-card" + (value === c.key ? " picked" : "")}
            style={{ animationDelay: `${i * GACHA_ANIM.staggerMs}ms`, animationDuration: `${GACHA_ANIM.flipMs}ms` }}
            onClick={() => pick(c.key)}>
            <span className="gacha-card-inner">
              <b>{c.label}</b>
              {c.desc && <span>{c.desc.slice(0, 34)}{c.desc.length > 34 ? "…" : ""}</span>}
              {c.group !== group && <i className="gacha-wild">跨界灵感</i>}
            </span>
          </button>
        ))}
      </div>
      {showAll ? (
        <div className="gacha-all">
          {cards.map((c) => (
            <button key={c.key} type="button" className="chip" onClick={() => pick(c.key)}>
              {c.label}
            </button>
          ))}
          <button type="button" className="chip custom"
            onClick={() => { onChange(""); setGroup(null); setShowAll(false); }}>
            自定义…
          </button>
        </div>
      ) : (
        <button type="button" className="linkbtn" onClick={() => setShowAll(true)}>
          全部玩法列表 →
        </button>
      )}
    </div>
  );
}
