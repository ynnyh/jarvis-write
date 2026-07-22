// 历史版本对比:旧版 vs 当前版左右分栏,支持回退
import {
  ChapterDetail, ChapterVersionBrief, ChapterVersionDetail, VERSION_SOURCE_CN,
} from "../../api";
import { Paragraphs } from "../../components/Reader";

interface Props {
  chapterNumber: number;
  versions: ChapterVersionBrief[];
  compareVer: ChapterVersionDetail | null;
  current: ChapterDetail | null;
  busy: boolean;
  onClose: () => void;
  onSelectVersion: (v: ChapterVersionBrief) => void;
  onRestore: (versionId: number) => void;
}

export default function VersionCompare({
  chapterNumber, versions, compareVer, current, busy,
  onClose, onSelectVersion, onRestore,
}: Props) {
  return (
    <div className="card">
      <div className="card-head mb-2">
        <h3 className="grow">第{chapterNumber}章 · 历史版本对比</h3>
        <button className="btn-sm" onClick={onClose}>关闭</button>
      </div>
      {versions.length === 0 ? (
        <div className="muted">
          暂无历史版本。以后重写 / 润色 / 手改正文时,被覆盖的旧版会自动存到这里,可随时对比回退。
        </div>
      ) : (
        <>
          <div className="muted mb-2">
            选一个旧版和「当前版」左右对照。满意当前版就关掉;想要旧版点「回退」。
          </div>
          <div className="chips mb-2">
            {versions.map((v) => (
              <span key={v.id}
                className={"chip" + (compareVer?.id === v.id ? " on" : "")}
                onClick={() => onSelectVersion(v)}>
                v{v.version} · {VERSION_SOURCE_CN[v.source] ?? v.source} · {v.word_count}字
              </span>
            ))}
          </div>
          {compareVer && current && (
            <>
              <div className="split mt-2">
                <div>
                  <div className="pane-title">
                    旧版 v{compareVer.version}
                    ({VERSION_SOURCE_CN[compareVer.source] ?? compareVer.source} · {compareVer.word_count}字)
                  </div>
                  <div className="pane pane-prose prose">
                    <Paragraphs text={compareVer.final_content} />
                  </div>
                </div>
                <div>
                  <div className="pane-title">当前版({current.word_count}字)</div>
                  <div className="pane pane-prose prose">
                    <Paragraphs text={current.final_content || current.draft_content} />
                  </div>
                </div>
              </div>
              <div className="actions mt-3">
                <button className="primary" disabled={busy}
                  title={busy ? "有任务进行中,完成后可回退" : undefined}
                  onClick={() => onRestore(compareVer.id)}>
                  回退到旧版 v{compareVer.version}(覆盖当前版并同步一致性引擎)
                </button>
                <button onClick={onClose}>保留当前版</button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
