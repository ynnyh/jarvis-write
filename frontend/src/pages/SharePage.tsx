// 公开分享阅读页:免登录只读(路由 /share/:token,挂在 HashRouter 外层保护之外)。
// 牛皮纸底 + 衬线正文(阅读器同款排版),页脚一枚「来自 jarvis-write」的小徽章引流。
// 数据全在服务端接口里,本页零敏感信息;链接可被作者撤销。
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

interface PubChapter { number: number; title?: string; content: string }
interface PubShare {
  scope: string;
  book_title: string;
  genre: string;
  chapters: PubChapter[];
}

export default function SharePage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<PubShare | null>(null);
  const [err, setErr] = useState("");
  const [cur, setCur] = useState(0);

  useEffect(() => {
    let alive = true;
    fetch(`/api/public/shares/${token}`)
      .then(async (r) => {
        if (!r.ok) {
          const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
          throw new Error(j.detail ?? `HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [token]);

  if (err) {
    return (
      <div className="pub-wrap">
        <div className="pub-err">这杯茶凉了——{err}</div>
        <a className="pub-brand" href="https://ynnyh.github.io/jarvis-write/">了解 jarvis-write →</a>
      </div>
    );
  }
  if (!data) {
    return <div className="pub-wrap"><div className="muted pub-loading">加载中…</div></div>;
  }

  const ch = data.chapters[cur] ?? data.chapters[0];
  const paras = (ch?.content ?? "").split(/\n+/).filter((p) => p.trim());

  return (
    <div className="pub-wrap pub-paper">
      <header className="pub-head">
        <h1 className="pub-title">{data.book_title}</h1>
        {data.genre && <span className="pub-genre">{data.genre}</span>}
      </header>

      {data.chapters.length > 1 && (
        <nav className="pub-toc">
          {data.chapters.map((c) => (
            <button key={c.number} type="button"
              className={"pub-toc-item" + (c.number === ch?.number ? " on" : "")}
              onClick={() => setCur(data.chapters.findIndex((x) => x.number === c.number))}>
              第{c.number}章 {c.title ?? ""}
            </button>
          ))}
        </nav>
      )}

      <article className="pub-body">
        {paras.map((p, i) => <p key={i}>{p}</p>)}
      </article>

      <footer className="pub-foot">
        <span className="pub-foot-line">本章由作者在 <b>jarvis-write</b> 里写作 —— AI 参与创作,但秩序属于作者。</span>
        <a className="pub-brand" href="https://ynnyh.github.io/jarvis-write/" target="_blank" rel="noreferrer">
          我也想写一本 →
        </a>
      </footer>
    </div>
  );
}
