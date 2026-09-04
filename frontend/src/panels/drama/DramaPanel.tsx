// 漫剧工坊面板:把已定稿小说改编成漫剧拍摄手册。
// 四步流水线(每步独立可重跑):美术风格卡/资产卡 → 集规划 → 单集剧本 → 分镜 → 三轨提示词,
// 产物拿去即梦/可灵/Midjourney/剪映出片——沿用「只产提示词」哲学,不接生成模型。
// 一致性靠锚段注入:画风锚(风格卡)+ 人物锚(角色卡)+ 场景锚(场景卡)逐字嵌入每格分镜。
// 阶段 2 补齐出片最后一块:声线选型 + 成片包(配音稿/剪辑清单/SRT 字幕)。
import { useCallback, useEffect, useState } from "react";
import type { DramaCharacterCard, DramaEpisode, DramaMeta, DramaSceneCard, DramaStyleCard, DramaTrailer } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import { useProject } from "../../hooks/queries";
import { errMsg } from "../../pollJob";
import EmptyState from "../../ui/EmptyState";
import StepBar, { Step } from "../../ui/StepBar";
import { DramaGuide, DramaProductionGuide } from "./DramaGuide";
import { nextEpisodeTodo } from "./dramaShared";
import { StyleSection } from "./StyleSection";
import { AssetsSection } from "./AssetsSection";
import { PlanSection } from "./PlanSection";
import { EpisodeDetail } from "./EpisodeDetail";
import { TrailerSection } from "./TrailerSection";

interface Props { pid: number }

export default function DramaPanel({ pid }: Props) {
  const [meta, setMeta] = useState<DramaMeta | null>(null);
  const [style, setStyle] = useState<DramaStyleCard | null>(null);
  const [cards, setCards] = useState<DramaCharacterCard[]>([]);
  const [scenes, setScenes] = useState<DramaSceneCard[]>([]);
  const [episodes, setEpisodes] = useState<DramaEpisode[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [trailer, setTrailer] = useState<DramaTrailer | null>(null);
  const [loadErr, setLoadErr] = useState("");
  // 逐格出片进度(展开的集上报:视频出好了 N/M):步骤条「逐格出片」步的数据源
  const [shootProg, setShootProg] = useState({ done: 0, total: 0 });
  // 出片模式(项目级):完整档才亮「合成成片」区
  const { data: proj } = useProject(pid);
  const renderMode = proj?.render_mode === "full" ? "full" : "lite";

  const reloadBase = useCallback(async () => {
    try {
      const [m, s, c, e, t] = await Promise.all([
        dramaApi.meta(pid),
        dramaApi.getStyle(pid),
        dramaApi.getCharacters(pid),
        dramaApi.getEpisodes(pid),
        dramaApi.getTrailer(pid),
      ]);
      setMeta(m);
      setStyle(s.style);
      setCards(c.cards);
      setScenes(c.scenes);
      setEpisodes(e.episodes);
      setTrailer(t.trailer);
    } catch (e) {
      setLoadErr(errMsg(e));
    }
  }, [pid]);

  useEffect(() => { void reloadBase(); }, [reloadBase]);

  if (loadErr) return <div className="msg-err">{loadErr}</div>;
  if (meta === null) return <p className="muted">加载中…</p>;

  if (meta.approved_chapters.length === 0) {
    return (
      <EmptyState>
        <b>漫剧工坊还开不了工:一章都还没定稿。</b>
        <div className="mt-2">
          这里改编的是小说<b>成稿</b>——先去<b>写作区</b>生成章节并点「定稿」,有一章就能开工。
          定稿后回来照着 ①→⑤ 走:定画风 → 出角色卡 → 切集 → 单集(剧本/分镜/提示词/成片包)→ 预告片,
          最后导出一份能直接照着出图、配音、剪辑的拍摄手册。
        </div>
      </EmptyState>
    );
  }

  // 管线步骤状态(步骤条 + 各区小标);第一个未完成的就是「现在做这步」
  const selected = episodes.find((e) => e.id === selectedId) ?? null;
  const steps: Step[] = [
    { key: "style", label: "风格卡", done: !!style?.style_cn,
      todo: "定全片画风:选个方向,点「AI 定美术风格」。" },
    { key: "assets", label: "角色/场景卡", done: cards.length > 0,
      todo: "出角色的锁定外貌段:点「AI 生成资产卡」,人物才不会换脸。" },
    { key: "plan", label: "切集", done: episodes.length > 0,
      todo: "选章节范围,点「切集」——把小说切成一集集短剧。" },
    { key: "episode", label: "单集流水线", done: episodes.some((e) => e.status === "ready"),
      todo: episodes.length === 0
        ? "先切集,再做单集。"
        : selected === null
          ? "在下面的集列表里点任意一集展开,再走 ④-1 → ④-4。"
          : nextEpisodeTodo(selected) },
    { key: "shoot", label: "逐格出片", done: shootProg.total > 0 && shootProg.done >= shootProg.total,
      todo: shootProg.total === 0
        ? "点开一集,在每格分镜的「本站直接出片」出视频草片(先挂静帧再出片,长相更稳)。"
        : `已出 ${shootProg.done}/${shootProg.total} 格:分镜格里点「本站直接出片」,不满意的点「重出一版」再挑。` },
    { key: "trailer", label: "预告片", done: !!trailer,
      todo: "可选:从各集高能素材混剪一条宣传片。" },
  ];

  return (
    <div className="wb-shell">
      <DramaGuide />

      <StepBar steps={steps} anchorPrefix="drama-step" allDone={<>
        五步都走完了 👏 导出拍摄手册,照下面的「出片指引」去出图/配音/剪辑;
        想做下一批章节就回 ③ 换个章节范围再切集。
      </>} />

      <div className="wb-cols">
        {/* 资产侧栏:桌面常驻左侧(sticky),移动端随流单列 */}
        <aside className="wb-rail">
          <div id="drama-step-style">
            <StyleSection pid={pid} style={style} directions={meta.directions} onSaved={setStyle} />
          </div>
          <div id="drama-step-assets">
            <AssetsSection pid={pid} cards={cards} scenes={scenes}
              onChanged={(c, s) => { setCards(c); setScenes(s); }} />
          </div>
        </aside>

        {/* 主区:切集 → 单集流水线 → 预告片 */}
        <div className="wb-main">
          <div id="drama-step-plan">
            <PlanSection pid={pid} approved={meta.approved_chapters}
              episodes={episodes}
              onChanged={setEpisodes}
              selectedId={selectedId}
              onSelect={setSelectedId} />
          </div>
          <div id="drama-step-episode">
            {selectedId !== null ? (
              <EpisodeDetail pid={pid} eid={selectedId}
                hasStyle={!!style?.style_cn}
                ratio={style?.ratio || "9:16"}
                renderMode={renderMode}
                onEpisodesChanged={setEpisodes}
                onShootProgress={setShootProg}
                onDeselect={() => setSelectedId(null)} />
            ) : episodes.length > 0 && (
              <div className="card drama-pick-hint">
                <h3>④ 单集流水线 <span className="muted">先选一集</span></h3>
                <p className="card-desc">
                  ↑ 在上面的集列表里<b>点任意一集</b>,这里就会展开那一集的流水线:
                  ④-1 写剧本 → ④-2 拆分镜 → ④-3 出提示词 → ④-4 出成片包 → 导出手册。
                  建议从第 1 集开始,一集走通了再批量做后面的。
                </p>
                <button className="primary" onClick={() => setSelectedId(episodes[0].id)}>
                  从第 {episodes[0].ep_index} 集开始
                </button>
              </div>
            )}
          </div>
          <div id="drama-step-trailer">
            <TrailerSection pid={pid} episodes={episodes}
              trailer={trailer} onGenerated={setTrailer} />
          </div>
          <DramaProductionGuide />
        </div>
      </div>
    </div>
  );
}
