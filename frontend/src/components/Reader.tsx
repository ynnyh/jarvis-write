// 全屏阅读器:写作页单章阅读与「阅读全书」共用。
// 内含:偏好设置(背景/字体/字号,localStorage 持久化)、定稿/草稿 tab、上一章/下一章、Esc 关闭;
// 传入 toc 时变为全书模式 —— PC 左侧目录栏,窄屏(≤640px)收成「目录」抽屉;
// 传入 polishCtx 时开启段落点选润色(选段 → 输方向 → 对照 → 替换并同步一致性引擎);
// 传入 restoreScroll / onScrollPos 时支持全书阅读位置记忆(恢复与上报)。
import { ChapterDetail } from "../api";
import { Paragraphs } from "./reader/paragraphs";
import { STATUS_BADGE, STATUS_CN } from "./reader/status";
import { DIRECTION_CHIPS, FONT_OPTIONS, SIZE_OPTIONS, THEME_OPTIONS } from "./reader/prefs";
import { PolishCtx, useReaderState } from "./reader/useReaderState";

// 历史上正文工具(splitParas/nthParaSpan/Paragraphs)与状态映射(STATUS_CN/STATUS_BADGE)
// 从 Reader 导出,外部多处引用,保留 re-export 兼容;PolishCtx 定义已下沉到 useReaderState
export { splitParas, nthParaSpan } from "./reader/paragraphs";
export { Paragraphs, STATUS_BADGE, STATUS_CN };
export type { PolishCtx };

/** 全书目录条目:disabled 表示该章尚未生成正文(置灰不可点) */
export interface ReaderTocItem { num: number; label: string; disabled?: boolean; }

interface Props {
  loading: boolean;            // 翻章/加载中:禁用翻页按钮;chapter 为空时显示加载态
  chapter: ChapterDetail | null;
  title?: string;              // 章节标题(来自大纲)
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  toc?: {
    items: ReaderTocItem[];
    current: number | null;
    onSelect: (n: number) => void;
    bookTitle?: string;        // 全书模式:目录栏顶部书名
    synopsis?: string | null;  // 目录栏书名下的简介(无则不显示)
  };
  restoreScroll?: number | null;              // 全书模式:首次打开要恢复的滚动位置
  onScrollPos?: (chapterNum: number, scroll: number) => void; // 滚动位置上报(父级防抖持久化)
  polishCtx?: PolishCtx;       // 传入即开启「点选段落润色」
}

export default function Reader({
  loading, chapter, title, hasPrev, hasNext, onPrev, onNext, onClose, toc,
  restoreScroll, onScrollPos, polishCtx,
}: Props) {
  const {
    tab, setTab, prefs, setPrefs, showSettings, setShowSettings, tocOpen, setTocOpen,
    settingsRef, contentRef, discussLogRef,
    selPara, setSelPara, polishOpen, setPolishOpen, editOpen, setEditOpen,
    editText, setEditText, proseActions, direction, setDirection,
    polishing, polished, setPolished, applying, polishErr, setPolishErr,
    pendingSyncNum, setPendingSyncNum, syncNum, syncStage,
    discussOpen, setDiscussOpen, discussMsgs, discussInput, setDiscussInput,
    discussing, discussErr, suggestion, setSuggestion,
    closePolish, closeDiscuss, handleContentScroll,
    curText, selText, polishEnabled,
    doPolish, applyReplacement, applyPolish, startReaderSync, sendDiscuss, adoptSuggestion,
  } = useReaderState({ chapter, restoreScroll, onScrollPos, onClose, polishCtx });

  return (
    // 点遮罩与 Esc 同一分层顺序:先收内层弹层(对话/润色/编辑/段选),都收完才关阅读器——
    // 防止误点遮罩直接把未决状态连同阅读器一起关掉
    <div className="reader-overlay" onClick={() => {
      if (discussOpen) { if (!discussing) closeDiscuss(); return; }
      if (polishOpen) { closePolish(); return; }
      if (editOpen) { setEditOpen(false); return; }
      if (selPara != null) { setSelPara(null); return; }
      onClose();
    }}>
      <div
        className={"reader" + (toc ? " reader-book" : "")}
        data-theme={prefs.theme}
        data-font={prefs.font}
        data-size={prefs.size}
        onClick={(e) => e.stopPropagation()}
      >
        {chapter ? (
          <>
            <div className="reader-head">
              {toc && (
                <button className="btn-sm reader-toc-btn" onClick={() => setTocOpen((v) => !v)}>
                  目录
                </button>
              )}
              <h2 className="reader-title">
                <span className="reader-title-text">第{chapter.chapter_number}章 {title ?? ""}</span>
                <span className="reader-meta">
                  <span className={"badge " + (chapter.is_stale ? "err" : STATUS_BADGE[chapter.status] ?? "")}>
                    {chapter.is_stale ? "大纲已变" : STATUS_CN[chapter.status] ?? chapter.status}
                  </span>
                  <span className="muted"> {chapter.word_count}字</span>
                </span>
              </h2>
              {chapter.draft_content && chapter.draft_content !== chapter.final_content && (
                <div className="reader-tabs">
                  <button
                    type="button"
                    className={"reader-tab" + (tab === "final" ? " on" : "")}
                    onClick={() => { setTab("final"); setSelPara(null); closePolish(); }}
                  >定稿</button>
                  <button
                    type="button"
                    className={"reader-tab" + (tab === "draft" ? " on" : "")}
                    onClick={() => { setTab("draft"); setSelPara(null); closePolish(); }}
                  >草稿</button>
                </div>
              )}
              <div className="reader-settings" ref={settingsRef}>
                <button className="btn-sm" onClick={() => setShowSettings((v) => !v)}>
                  设置
                </button>
                {showSettings && (
                  <div className="reader-settings-pop">
                    <div className="rs-group">
                      <div className="fl">背景</div>
                      <div className="chips">
                        {THEME_OPTIONS.map((o) => (
                          <button
                            key={o.v}
                            type="button"
                            className={"chip" + (prefs.theme === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, theme: o.v }))}
                          >{o.label}</button>
                        ))}
                      </div>
                    </div>
                    <div className="rs-group">
                      <div className="fl">字体</div>
                      <div className="chips">
                        {FONT_OPTIONS.map((o) => (
                          <button
                            key={o.v}
                            type="button"
                            className={"chip " + o.cls + (prefs.font === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, font: o.v }))}
                          >{o.label}</button>
                        ))}
                      </div>
                    </div>
                    <div className="rs-group">
                      <div className="fl">字号</div>
                      <div className="chips">
                        {SIZE_OPTIONS.map((o) => (
                          <button
                            key={o.v}
                            type="button"
                            className={"chip" + (prefs.size === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, size: o.v }))}
                          >{o.label}</button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <button className="reader-close" onClick={onClose}>关闭</button>
              {/* 窄屏强制换行点:仅 ≤640px 显示,把头部切成两行(见 styles.css) */}
              <div className="reader-head-br" />
            </div>
            {(pendingSyncNum !== null || syncNum !== null) && (
              <div className="reader-sync-bar">
                {pendingSyncNum !== null && (
                  <div className="sync-ask">
                    <span className="sync-ask-text">
                      第 {pendingSyncNum} 章已保存。改动了情节吗?要同步一致性引擎吗?
                      <b className="hint">同步会更新人物状态、伏笔与后续章节的前情摘要;只改了文字/措辞可以跳过。</b>
                    </span>
                    <span className="sync-ask-actions">
                      <button className="primary btn-sm" disabled={syncNum !== null}
                        onClick={() => startReaderSync(pendingSyncNum)}>同步</button>
                      <button className="btn-sm" onClick={() => setPendingSyncNum(null)}>跳过</button>
                    </span>
                  </div>
                )}
                {syncNum !== null && (
                  <div className="sync-badge">
                    <span className="spin spin-sm" />
                    <span>第 {syncNum} 章同步一致性引擎中({syncStage})· 不影响继续阅读</span>
                  </div>
                )}
              </div>
            )}
            <div className="reader-body">
              {toc && (
                <div className={"reader-toc" + (tocOpen ? " open" : "")}>
                  {toc.bookTitle && (
                    <div className="reader-toc-book">
                      <div className="reader-toc-book-title">{toc.bookTitle}</div>
                      {toc.synopsis && (
                        <div className="reader-toc-book-syn" title={toc.synopsis}>
                          {toc.synopsis}
                        </div>
                      )}
                    </div>
                  )}
                  {toc.items.map((it) => (
                    <button
                      key={it.num}
                      type="button"
                      className={"reader-toc-item"
                        + (it.num === toc.current ? " on" : "")
                        + (it.disabled ? " off" : "")}
                      onClick={() => {
                        if (it.disabled) return;
                        toc.onSelect(it.num);
                        setTocOpen(false);
                      }}
                    >
                      <b>第{it.num}章</b> {it.label}
                    </button>
                  ))}
                </div>
              )}
              <div
                className="reader-content"
                ref={contentRef}
                onScroll={handleContentScroll}
                onClick={(e) => {
                  // 点正文空白处取消段落选择(点段落本身已 stopPropagation)
                  if (e.target === e.currentTarget) setSelPara(null);
                }}
              >
                <Paragraphs
                  text={curText}
                  selectedIdx={polishEnabled ? selPara : null}
                  onSelect={polishEnabled ? (i) => setSelPara(i) : undefined}
                />
              </div>
              {polishEnabled && selPara != null && !polishOpen && !editOpen && !discussOpen && (
                <div className="para-tools">
                  <button className="btn-sm primary" onClick={() => setDiscussOpen(true)}>
                    💬 问 AI
                  </button>
                  <button className="btn-sm" onClick={() => setPolishOpen(true)}>
                    ✨ 润色此段
                  </button>
                  <button className="btn-sm" onClick={() => { setEditText(selText ?? ""); setEditOpen(true); }}>
                    ✍️ 手动改
                  </button>
                  <button className="btn-sm" onClick={() => setSelPara(null)}>取消选择</button>
                </div>
              )}
            </div>
            <div className="reader-nav">
              <button disabled={!hasPrev || loading} onClick={onPrev}>
                ← 上一章
              </button>
              <button disabled={!hasNext || loading} onClick={onNext}>
                下一章 →
              </button>
            </div>
            {polishOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!polishing && !applying) closePolish(); }}>
                <div className="reader-polish-panel" onClick={(e) => e.stopPropagation()}>
                  {polished == null ? (
                    <>
                      <div className="rp-label">选中段落</div>
                      <div className="rp-orig">{selText}</div>
                      <div className="rp-label">润色方向(只改文笔,不动情节)</div>
                      <input
                        type="text"
                        value={direction}
                        placeholder="如:更紧张一些 / 去掉 AI 腔"
                        onChange={(e) => setDirection(e.target.value)}
                      />
                      <div className="chips rp-chips">
                        {(proseActions.length
                          ? proseActions.map((a) => ({ label: a.label, value: a.directive }))
                          : DIRECTION_CHIPS.map((c) => ({ label: c, value: c }))
                        ).map((c) => (
                          <button
                            key={c.label}
                            type="button"
                            className={"chip" + (direction === c.value ? " on" : "")}
                            onClick={() => setDirection(c.value)}
                          >{c.label}</button>
                        ))}
                      </div>
                      <div className="rp-actions">
                        <button className="primary" disabled={polishing} onClick={doPolish}>
                          {polishing && <span className="spin" />}开始润色
                        </button>
                        <button disabled={polishing} onClick={closePolish}>取消</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="rp-compare">
                        <div className="rp-col">
                          <div className="rp-label">原文</div>
                          <div className="rp-text">{selText}</div>
                        </div>
                        <div className="rp-col">
                          <div className="rp-label">润色后</div>
                          <div className="rp-text rp-new">{polished}</div>
                        </div>
                      </div>
                      <div className="rp-actions">
                        <button className="primary" disabled={applying} onClick={applyPolish}>
                          {applying && <span className="spin" />}
                          {applying ? "替换中…" : "替换原文"}
                        </button>
                        <button
                          disabled={applying}
                          onClick={() => { setPolished(null); setPolishErr(""); }}
                        >重新润色</button>
                        <button disabled={applying} onClick={closePolish}>取消</button>
                      </div>
                    </>
                  )}
                  {polishErr && <div className="msg-err rp-err">{polishErr}</div>}
                </div>
              </div>
            )}
            {editOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!applying) setEditOpen(false); }}>
                <div className="reader-polish-panel" onClick={(e) => e.stopPropagation()}>
                  <div className="rp-label">手动修改此段(只动这一段;保存后可选同步一致性引擎)</div>
                  <textarea
                    rows={Math.min(12, Math.max(4, Math.ceil(editText.length / 40)))}
                    value={editText}
                    autoFocus
                    onChange={(e) => setEditText(e.target.value)}
                  />
                  <div className="rp-actions">
                    <button className="primary"
                      disabled={applying || !editText.trim() || editText === selText}
                      onClick={() => applyReplacement(editText.trim(), true)}>
                      {applying && <span className="spin" />}
                      {applying ? "保存中…" : "保存修改"}
                    </button>
                    <button disabled={applying} onClick={() => setEditOpen(false)}>取消</button>
                  </div>
                  {polishErr && <div className="msg-err rp-err">{polishErr}</div>}
                </div>
              </div>
            )}
            {discussOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!discussing && !applying) closeDiscuss(); }}>
                <div className="reader-polish-panel reader-discuss-panel" onClick={(e) => e.stopPropagation()}>
                  <div className="rp-label">与 AI 聊这一段(可以问它什么意思,也可以让它帮你改)</div>
                  <div className="rd-orig">{selText}</div>
                  <div className="rd-log" ref={discussLogRef}>
                    {discussMsgs.length === 0 && !discussing && (
                      <div className="muted rd-empty">
                        试试:「这段是什么意思?」「这里为什么这么写?」「帮我改得紧张一点」
                      </div>
                    )}
                    {discussMsgs.map((m, i) => (
                      <div key={i} className={"rd-msg rd-" + m.role}>
                        <div className="rd-bubble">{m.content}</div>
                      </div>
                    ))}
                    {discussing && (
                      <div className="rd-msg rd-assistant">
                        <div className="rd-bubble muted"><span className="spin spin-sm" />思考中…</div>
                      </div>
                    )}
                  </div>
                  {suggestion != null && (
                    <div className="rd-suggestion">
                      <div className="rp-label">AI 给出的改写(采用后替换这一段)</div>
                      <div className="rp-text rp-new">{suggestion}</div>
                      <div className="rp-actions">
                        <button className="primary" disabled={applying} onClick={adoptSuggestion}>
                          {applying && <span className="spin" />}
                          {applying ? "替换中…" : "采用此改写"}
                        </button>
                        <button disabled={applying} onClick={() => setSuggestion(null)}>不用,继续聊</button>
                      </div>
                    </div>
                  )}
                  <div className="rd-input">
                    <textarea
                      rows={2}
                      value={discussInput}
                      placeholder="问点什么,或说说想怎么改…(Enter 发送,Shift+Enter 换行)"
                      disabled={discussing}
                      onChange={(e) => setDiscussInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendDiscuss();
                        }
                      }}
                    />
                    <div className="rp-actions">
                      <button className="primary" disabled={discussing || !discussInput.trim()} onClick={sendDiscuss}>
                        {discussing && <span className="spin" />}发送
                      </button>
                      <button disabled={discussing} onClick={closeDiscuss}>关闭</button>
                    </div>
                  </div>
                  {discussErr && <div className="msg-err rp-err">{discussErr}</div>}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="reader-content muted"><span className="spin" />加载正文…</div>
        )}
      </div>
    </div>
  );
}
