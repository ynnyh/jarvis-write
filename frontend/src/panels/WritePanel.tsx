// write 区主场(「正文即界面」P1+P2,docs/10):单列居中正文为视觉重心——
// 章首=状态卡(三态人话 + 「更多」过渡入口)+ 蓝图卡(<details> 默认收起),
// 中=可点选正文(Prose 段落气泡:改这段/手改),章尾=下一章卡。
// 目录=覆盖式抽屉(章题按钮/Ctrl+B 唤出,双端同一份 CatalogDrawer);StageBar/底部动作条/
// FAB/动作扇/参考抽屉已退场(见 docs/10 §8 迁移映射)。
// P2:右侧 AI 窄栏(AiDock,移动端=底部输入条+全屏 sheet)承接「随便聊/这章整体不满意」
// 两个对话通道;③整章优化结果在正文区顶部出 PolishCompareCard 对照;④整章重生成走
// generate 链路。整章重写卡(ReviseCard)/润色工作台(PolishPanel)/研讨(ReviseChat)已退场。
// 「当前章」以 URL ch 参数为唯一来源(useChapterContext),正文走 qk.chapter 共享缓存。
// 状态逻辑已按域拆到 write/use*.ts(useImmersive/useReader/useChapterVersions/useConsistencySync/
// useChapterGeneration:沉浸/阅读器/版本对比/多章同步并发/生成连写队列),壳回归编排+布局;
// 仍内联的是纯 UI:act= 校对/评分两张动作卡、GenResultCard、版本对比与全屏 Reader。
// 移动端:m-topbar(←/章题/阅读)+ 左右滑切章 + act 卡全屏 sheet。
import { Outline } from "../api";
import { qk } from "../hooks/queries";
import Reader from "../components/Reader";
import GenResultCard from "./chapters/GenResultCard";
import VersionCompare from "./chapters/VersionCompare";
import ChapterRail from "./write/ChapterRail";
import CatalogDrawer from "./write/CatalogDrawer";
import StoryMapOverlay from "./write/StoryMapOverlay";
import Prose from "./write/Prose";
import ChapterStatusCard from "./write/ChapterStatusCard";
import NextChapterCard from "./write/NextChapterCard";
import ReviewCard from "./write/ReviewCard";
import ProofreadCard from "./write/ProofreadCard";
import AiDock from "./write/AiDock";
import PolishCompareCard from "./write/PolishCompareCard";
import AnnotatedReviseCard from "./write/AnnotatedReviseCard";
import { useWritePanel, ACT_TITLE } from "./write/useWritePanel";

interface Props {
  pid: number; outlines: Outline[];
}

export default function WritePanel({ pid, outlines }: Props) {
  const {
    // RQ / 当前章
    chapters, chapterNum, setChapterNum, current, chapterQuery, currentBrief,
    currentOutline, activeOutline, setCurrent, reload, qc, nav,
    // act= 动作卡
    act, setAct, openDock,
    // 环境 / 壳 / 滑动切章
    isMobile, immersive, railOpen, setRailOpen, mapOpen, setMapOpen, onMainTouchStart, onMainTouchEnd,
    // 顶部错误 & 派生
    err, stage, nextChapterNum, genBlocked, genHint,
    // 本地 UI 态:AI 窄栏 / 选段 / 对照 / 批注 / 脏标记
    dockCollapsed, setDockCollapsed, dockPrefill,
    selectedPara, setSelectedPara,
    polishCompare, setPolishCompare,
    annotations, setAnnotations,
    reviseResult, setReviseResult,
    proseDirtyRef,
    // 审核 / 放行
    approve, releaseChapter,
    // useConsistencySync
    syncJobs, pendingSync, setPendingSync, triggerSync,
    // useChapterVersions
    versionsFor, versions, compareVer, closeVersions, openVersions, selectVersion, restoreVersion,
    // useChapterGeneration
    genJob, genResult, setGenResult, genTendency, setGenTendency,
    queueMode, setQueueMode, queuePicked, setQueuePicked,
    generate, startQueue, pickNextBatch,
    // useReader
    reader, readerLoading, setReader, openReader, prevNum, nextNum, readerOutline,
    // 目录抽屉选章
    openFromRail,
  } = useWritePanel({ pid, outlines });

  // 动作卡(act= 进 URL,可刷新/分享):桌面在中栏内联,移动端换全屏 sheet 容器(组件同一份)
  const actCards = (
    <>
      {act === "proofread" && current && chapterNum !== null && (
        <ProofreadCard pid={pid} chapterNum={chapterNum} />
      )}
      {act === "review" && current && chapterNum !== null && (
        <ReviewCard pid={pid} chapterNum={chapterNum}
          onRevise={(text) => openDock({ text, mode: "revise" })} />
      )}
    </>
  );

  return (
    <div className={"write-zone" + (immersive ? " immersive" : "")}>
      {genJob && (
        <div className="gen-banner">
          <span className="spin" />
          <span className="gen-banner-text">
            {genJob.num === 0 || genJob.stage.startsWith("[")
              ? `连写队列进行中(${genJob.stage})`
              : `第 ${genJob.num} 章生成中(${genJob.stage}),完成后可继续操作其他章节`}
          </span>
        </div>
      )}
      {[...syncJobs.entries()].map(([num, stage]) => (
        <div className="sync-badge" key={num}>
          <span className="spin spin-sm" />
          <span>第 {num} 章同步一致性引擎中({stage})· 不影响继续操作</span>
        </div>
      ))}
      {err && <div className="msg-err">{err}</div>}

      {/* ---- 移动端顶栏:←返回项目列表 + 当前章(点击开目录抽屉)+ 阅读入口 ---- */}
      {isMobile && (
        <div className="m-topbar">
          <button type="button" className="m-topbar-btn" title="返回项目列表"
            onClick={() => nav("/")}>←</button>
          <button type="button" className="m-topbar-title" title="打开目录选章"
            onClick={() => setRailOpen(true)}>
            {chapterNum !== null
              ? `第${chapterNum}章${activeOutline?.title ? ` · ${activeOutline.title}` : ""}`
              : "选择章节"}
          </button>
          <button type="button" className="m-topbar-btn" disabled={!currentBrief}
            title={currentBrief ? "阅读本章(全屏阅读器,可选段润色)" : "先在目录选一章已生成的章节"}
            onClick={() => chapterNum !== null && void openReader(chapterNum)}>
            阅读
          </button>
        </div>
      )}

      {/* ---- 主场:正文列 + 右侧 AI 窄栏(P2);移动端 AiDock 为 fixed 元素,位置无关 ---- */}
      <div className="write-body">
      {/* ---- 主场正文列:状态卡 + 蓝图卡 + 正文(段落气泡)+ 章尾下一章卡(移动端支持左右滑切章) ---- */}
      <div className="write-main" onTouchStart={onMainTouchStart} onTouchEnd={onMainTouchEnd}>
        {versionsFor !== null && versions !== null && (
          <VersionCompare
            chapterNumber={versionsFor}
            versions={versions}
            compareVer={compareVer}
            current={current}
            busy={!!genJob}
            onClose={closeVersions}
            onSelectVersion={(v) => selectVersion(versionsFor, v)}
            onRestore={(vid) => restoreVersion(versionsFor, vid)}
          />
        )}

        {genResult && (
          <GenResultCard
            pid={pid}
            result={genResult}
            genBlocked={genBlocked}
            genHint={genHint}
            onClose={() => setGenResult(null)}
            onChanged={() => {
              reload();
              // 修订/放行后同步刷新打开的正文(失效缓存,共享 qk.chapter 自动重拉)
              qc.invalidateQueries({ queryKey: qk.chapter(pid, genResult.chapter_number) });
            }}
            onRewrite={() => {
              // 「去重写本章」:切到该章并打开 AI 栏梳理修改意见
              setChapterNum(genResult.chapter_number);
              openDock({ mode: "revise" });
            }}
          />
        )}

        {/* 「更多」/快捷键打开的动作卡(act= 进 URL);移动端为全屏 sheet */}
        {isMobile && act && current ? (
          <div className="m-sheet-overlay">
            <div className="m-sheet">
              <div className="m-sheet-head">
                <h3 className="grow">{ACT_TITLE[act]}</h3>
                <button className="btn-sm" onClick={() => setAct(null)}>关闭</button>
              </div>
              {actCards}
            </div>
          </div>
        ) : actCards}

        {/* AI 栏③整章优化的对照结果(原文/润色稿,可微调后应用) */}
        {polishCompare && current && (
          <PolishCompareCard
            pid={pid}
            chapterNum={current.chapter_number}
            original={polishCompare.original}
            result={polishCompare.result}
            onApplied={() => setPolishCompare(null)}
            onDiscard={() => setPolishCompare(null)}
          />
        )}

        {/* AI 栏②按批注改的验收卡(逐条 diff,接受/拒绝走 paraEdit 快照守卫写回) */}
        {reviseResult && current && (
          <AnnotatedReviseCard
            pid={pid}
            chapter={current}
            pairs={reviseResult}
            onSaved={(updated) => { setCurrent(updated); void reload(); }}
            onClose={() => setReviseResult(null)}
          />
        )}

        {current ? (
          <>
            {/* 章首状态卡:三态人话(冲突拍板/过目通过/大纲已变)+ 「更多」过渡入口 */}
            <ChapterStatusCard
              pid={pid}
              stage={stage}
              currentBrief={currentBrief}
              current={current}
              genBlocked={genBlocked}
              genHint={genHint}
              onApprove={() => approve(current.chapter_number)}
              onRelease={() => releaseChapter(current.chapter_number)}
              onAct={(a) => {
                // P2:重写/整章优化由 AI 窄栏承接;校对/评分仍走 act= 动作卡
                if (a === "revise") openDock({ mode: "revise" });
                else setAct(a);
              }}
              onVersions={() => { void openVersions(current.chapter_number); }}
              onChanged={() => {
                reload();
                qc.invalidateQueries({ queryKey: qk.chapter(pid, current.chapter_number) });
              }}
            />

            {/* 章首蓝图卡:默认收起一行,展开看摘要/伏笔 */}
            {currentOutline && (
              <details className="card card-info blueprint-card">
                <summary>
                  <b>本章蓝图</b> 第{currentOutline.chapter_number}章《{currentOutline.title}》
                  <span className="badge">{currentOutline.chapter_role}</span>
                </summary>
                <div className="muted mt-1">{currentOutline.summary}</div>
                <div className="meta-line">
                  伏笔:{currentOutline.foreshadowing || "无"}
                </div>
                <button className="btn-sm mt-1" onClick={() => setMapOpen(true)}
                  title="打开故事地图,纵览全书脉络与伏笔(Ctrl+M)">看全书脉络 →</button>
              </details>
            )}

            <div className="card">
              <div className="content-head mb-2">
                <div className="content-head-title">
                  <h2>第{current.chapter_number}章</h2>
                  <span className="content-head-meta">正文 · {current.word_count}字</span>
                  {currentBrief?.is_stale && <span className="badge err">大纲已变</span>}
                </div>
              </div>
              {pendingSync === current.chapter_number && (
                <div className="sync-ask mb-2">
                  <span className="sync-ask-text">
                    第 {current.chapter_number} 章已保存,要同步一致性引擎吗?
                    <b className="hint">同步会更新人物状态、伏笔与后续章节的前情摘要;只改了几处措辞可以跳过。</b>
                  </span>
                  <span className="sync-ask-actions">
                    <button className="primary btn-sm" disabled={syncJobs.has(current.chapter_number)}
                      onClick={() => triggerSync(current.chapter_number)}>立即同步</button>
                    <button className="btn-sm"
                      onClick={() => setPendingSync(null)}>跳过</button>
                  </span>
                </div>
              )}
              {/* 正文即界面:段落点选 → 气泡(改这段/手改);整章 textarea 编辑态已删除 */}
              <Prose
                pid={pid}
                chapter={current}
                genBlocked={genBlocked}
                genHint={genHint}
                onSaved={(updated) => { setCurrent(updated); void reload(); }}
                onSyncAsk={(num) => setPendingSync(num)}
                onDirtyChange={(dirty) => { proseDirtyRef.current = dirty; }}
                onSelectChange={setSelectedPara}
                annotations={annotations}
                onAnnotate={(idx, snapshot, note) =>
                  setAnnotations((prev) => [...prev, { paraIdx: idx, snapshot, note }])}
              />
            </div>

            {/* 章尾下一章卡:唯一常驻的「推进」入口 */}
            <NextChapterCard
              pid={pid}
              nextNum={nextChapterNum}
              nextTitle={nextChapterNum !== null
                ? outlines.find((o) => o.chapter_number === nextChapterNum)?.title
                : undefined}
              genBlocked={genBlocked}
              genHint={genHint}
              onGenerate={(n) => { void generate(n); }}
            />
          </>
        ) : chapterNum !== null ? (
          chapterQuery.isPending ? (
            <div className="card muted"><span className="spin spin-sm" /> 加载正文…</div>
          ) : (
            /* 空态大卡:选中了未写的章 */
            <div className="card empty-chapter">
              <h2>第{chapterNum}章{activeOutline ? `《${activeOutline.title}》` : ""}</h2>
              <div className="muted mt-1">这章还没写。</div>
              {activeOutline && (
                <div className="muted mt-1">{activeOutline.summary}</div>
              )}
              <div className="mt-2">
                <button className="primary" disabled={genBlocked}
                  title={genBlocked ? genHint : "按蓝图生成本章,生成时自动注入:本章蓝图、前情摘要、人物状态、到期伏笔"}
                  onClick={() => void generate(chapterNum)}>
                  让 AI 写这一章
                </button>
              </div>
            </div>
          )
        ) : (
          /* 未选章引导(双端同一份):目录抽屉是唯一选章入口 */
          <div className="card empty-chapter">
            <h2>先选一章</h2>
            <div className="muted mt-1">打开目录挑一章:已写的直接读、点段落就能改;没写的让 AI 写。</div>
            <div className="mt-2">
              <button className="primary" onClick={() => setRailOpen(true)}>打开目录</button>
            </div>
          </div>
        )}
      </div>

      {/* ---- AI 窄栏(P2):桌面右侧 320px/40px 细条;移动端底部输入条/全屏 sheet ---- */}
      {current && chapterNum !== null && (
        <AiDock
          pid={pid}
          chapterNum={chapterNum}
          current={current}
          selectedPara={selectedPara}
          genBlocked={genBlocked}
          genHint={genHint}
          collapsed={dockCollapsed}
          onCollapsedChange={setDockCollapsed}
          prefill={dockPrefill}
          onSaved={(updated) => { setCurrent(updated); void reload(); }}
          onRegenerate={(revision) => {
            // ④:移动端收起 sheet 让出生成横幅;桌面横幅在正文列顶部,无需收
            if (isMobile) setDockCollapsed(true);
            void generate(chapterNum, revision);
          }}
          onPolishResult={(original, result) => {
            if (isMobile) setDockCollapsed(true); // 同上:让出正文区顶部的对照卡
            setPolishCompare({ original, result });
          }}
          annotations={annotations}
          onRemoveAnnotation={(i) =>
            setAnnotations((prev) => prev.filter((_, idx) => idx !== i))}
          onReviseResult={(pairs) => {
            if (isMobile) setDockCollapsed(true); // 让出正文区顶部的验收卡
            setReviseResult(pairs);
            setAnnotations([]); // 已成批发出,批注退场;结果由验收卡逐条处理
          }}
        />
      )}
      </div>

      {/* ---- 目录抽屉(双端同一份):搜索/筛选/连写队列/正文倾向;选章后自动关闭 ---- */}
      {railOpen && (
        <CatalogDrawer onClose={() => setRailOpen(false)}>
          <ChapterRail
            outlines={outlines}
            chapters={chapters}
            genJob={genJob}
            queueMode={queueMode}
            queuePicked={queuePicked}
            onToggleQueueMode={() => { setQueueMode(!queueMode); setQueuePicked(new Set()); }}
            onToggleQueuePick={(n, checked) => {
              const next = new Set(queuePicked);
              if (checked) next.add(n); else next.delete(n);
              setQueuePicked(next);
            }}
            onPickNextBatch={pickNextBatch}
            onStartQueue={startQueue}
            genTendency={genTendency}
            onTendencyChange={setGenTendency}
            onOpen={(n) => { void openFromRail(n); }}
            onClose={() => setRailOpen(false)}
          />
        </CatalogDrawer>
      )}

      {/* ---- 故事地图覆盖层(Ctrl+M/命令面板/章首入口):全书脉络纵览,点章即跳并关本层 ---- */}
      {mapOpen && (
        <StoryMapOverlay
          outlines={outlines}
          chapters={chapters}
          currentNum={chapterNum}
          onOpen={(n) => { setMapOpen(false); void openFromRail(n); }}
          onClose={() => setMapOpen(false)}
        />
      )}

      {(reader || readerLoading) && (
        <Reader
          loading={readerLoading}
          chapter={reader}
          title={readerOutline?.title}
          hasPrev={prevNum != null}
          hasNext={nextNum != null}
          onPrev={() => prevNum != null && openReader(prevNum)}
          onNext={() => nextNum != null && openReader(nextNum)}
          onClose={() => setReader(null)}
          polishCtx={{
            pid,
            chapterNumber: reader?.chapter_number ?? 0,
            onApplied: (updated) => { setReader(updated); reload(); },
          }}
        />
      )}
    </div>
  );
}
