// write/useChapterEditor.ts —— 整章 CM6 编辑器的共享内核(2026-09 双轨档从 FreeWriteEditor 抽出)。
// 自由改稿(FreeWriteEditor)与同文双轨(DualTrackEditor)共用同一套编辑器配置:
//   换行/撤销历史/搜索替换/Ctrl+S 键位注册交给调用方/生成任务锁(Compartment 原地切只读)。
// 生命周期约定:父级按 chapter_number 加 key,切章即重建组件 → 编辑器只在本 hook 挂载时建一次。
// 为什么选 CM6 而不是 ProseMirror:正文是纯文本段落,没有富文本结构要建模;
// CM6 在中文 IME、大文档性能与无头定制上更稳,搜索/撤销历史开箱即用。
import { useEffect, useRef, useState, type RefObject } from "react";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";

export interface ChapterEditorOpts {
  /** 初始文档;同时是「脏判定」的已保存基准 */
  initialDoc: string;
  /** 任务锁:生成/重写进行中 → 编辑器只读(Compartment 原地切换,不重建) */
  genBlocked: boolean;
  /** 脏标记上报:父级切章前据此弹「丢弃修改」确认 */
  onDirtyChange?: (dirty: boolean) => void;
  /** 文档变化回调(每次击键):双轨的实时差异统计用 */
  onDocChanged?: (text: string) => void;
}

export function useChapterEditor(hostRef: RefObject<HTMLDivElement | null>, opts: ChapterEditorOpts) {
  const viewRef = useRef<EditorView | null>(null);
  const editableConf = useRef(new Compartment());
  // 已保存基准:dirty = 当前文档 !== 基准
  const baseRef = useRef(opts.initialDoc);
  const [dirty, setDirty] = useState(false);
  const [chars, setChars] = useState(0);
  // 回调走 ref:编辑器只建一次,闭包不过期(经 effect 同步,不碰渲染期写 ref 的红线)
  const dirtyCbRef = useRef(opts.onDirtyChange);
  const docCbRef = useRef(opts.onDocChanged);
  useEffect(() => {
    dirtyCbRef.current = opts.onDirtyChange;
    docCbRef.current = opts.onDocChanged;
  });

  function getDoc(): string {
    return viewRef.current?.state.doc.toString() ?? baseRef.current;
  }

  /** 整体替换文档并重置基准(双轨「载入当前定稿」用) */
  function setDoc(text: string): void {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    baseRef.current = text;
    setDirty(false);
    dirtyCbRef.current?.(false);
    view.focus();
  }

  /** 保存成功后调用:推进基准、清脏标记(内容由调用方自己知道) */
  function markSaved(content: string): void {
    baseRef.current = content;
    setDirty(false);
    dirtyCbRef.current?.(false);
  }

  // Ctrl+S → 调用方的 save:编辑器只建一次,经 ref 每次渲染后指向最新闭包
  const saveCbRef = useRef<() => void>(() => {});

  // 建编辑器(仅挂载时;组件按 chapter_number 加 key,切章即重建)
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: opts.initialDoc,
        extensions: [
          history(),
          EditorView.lineWrapping,
          highlightSelectionMatches(),
          search({ top: true }),
          keymap.of([
            { key: "Mod-s", preventDefault: true, run: () => { saveCbRef.current(); return true; } },
            ...defaultKeymap, ...historyKeymap, ...searchKeymap,
          ]),
          editableConf.current.of(EditorView.editable.of(!opts.genBlocked)),
          EditorView.updateListener.of((u) => {
            if (!u.docChanged) return;
            const text = u.state.doc.toString();
            setChars(text.replace(/\s/g, "").length);
            const isDirty = text !== baseRef.current;
            setDirty(isDirty);
            dirtyCbRef.current?.(isDirty);
            docCbRef.current?.(text);
          }),
          // 段落约定与全站一致:空行分段;编辑器不给自动缩进/Tab 制表(小说用不上)
        ],
      }),
    });
    view.focus();
    viewRef.current = view;
    setChars(opts.initialDoc.replace(/\s/g, "").length);
    return () => { view.destroy(); viewRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 生成/重写任务跑起来时切只读;结束后恢复可写(不重建,历史与光标保留)
  useEffect(() => {
    viewRef.current?.dispatch({
      effects: editableConf.current.reconfigure(EditorView.editable.of(!opts.genBlocked)),
    });
  }, [opts.genBlocked]);

  return { viewRef, dirty, chars, getDoc, setDoc, markSaved, setSaveHandler: (fn: () => void) => { saveCbRef.current = fn; } };
}
