// 出片引擎卡:autodl.art 的 ComfyUI 工作流账号配置(token 加密存库,回显打码)。
// 轻量档只需要两样:令牌 + 用哪两个工作流(有静帧走首尾帧,没静帧走文生视频)。
// workflow_id 默认值来自后端模型;用户自己换了工作流(平台页面右侧抽屉可查 ID)就这里改。
import { useEffect, useState } from "react";
import { RenderConfigOut, renderApi } from "../../renderApi";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

const DOC_URL = "https://autodl.art/docs/comfyui_api/";

export function RenderCard() {
  const [cfg, setCfg] = useState<RenderConfigOut | null>(null);
  const [token, setToken] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [resolution, setResolution] = useState("768p");
  const [wfI2v, setWfI2v] = useState("");
  const [wfT2v, setWfT2v] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const c = await renderApi.getConfig();
        setCfg(c);
        setBaseUrl(c.base_url);
        setResolution(c.resolution);
        setWfI2v(c.workflow_i2v);
        setWfT2v(c.workflow_t2v);
      } catch { /* 未登录/网络差的静默失败:表单留空即可 */ }
      finally { setLoaded(true); }
    })();
  }, []);

  async function save() {
    setSaving(true);
    try {
      const c = await renderApi.saveConfig({
        base_url: baseUrl,
        token: token || undefined, // 留空 = 不改动已存 token
        resolution,
        workflow_i2v: wfI2v,
        workflow_t2v: wfT2v,
      });
      setCfg(c);
      setToken("");
      toast.ok("出片引擎配置已保存",
        c.configured ? "漫剧/情绪短片的镜头卡片上可以点「出片」了" : "token 是空的,出片前还需要填写");
    } catch (e) { toast.err("出片配置保存失败", errMsg(e)); } finally { setSaving(false); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>出片引擎<span className="muted">文+图 → 视频(autodl.art ComfyUI 工作流)</span></h2>
        {cfg && <span className="badge">{cfg.configured ? "已配置" : "未配置"}</span>}
      </div>
      <p className="card-desc">
        在漫剧分镜格 / 情绪短片出片工作台上点「出片」,由 autodl.art 托管的 ComfyUI 工作流生成视频草片,
        出完自动下载回本站挂回该格。计费约 ¥0.02/秒(凌晨 ¥0.01),重 roll 一次几分钱。
        令牌在 autodl.art「令牌管理」创建(分组选 <b>ComfyUI</b>),<a href={DOC_URL} target="_blank" rel="noreferrer">API 文档 →</a>
      </p>
      {!loaded ? <p className="muted">读取配置…</p> : (
        <>
          <div className="form-grid">
            <label className="field">
              <span className="fl">令牌(token){cfg?.has_token && <span className="muted">(已存 {cfg.token_masked})</span>}</span>
              <input type="password" value={token} autoComplete="new-password"
                placeholder={cfg?.has_token ? "留空保持不变" : "粘贴 autodl.art 的 ComfyUI 令牌"}
                onChange={(e) => setToken(e.target.value)} />
            </label>
            <label className="field">
              <span className="fl">接口地址</span>
              <input value={baseUrl} placeholder="https://www.autodl.art"
                onChange={(e) => setBaseUrl(e.target.value)} />
            </label>
            <label className="field">
              <span className="fl">画质档(竖横按各片画幅自动折算)</span>
              <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
                <option value="768p">768p(推荐,与 480p 同价)</option>
                <option value="480p">480p(更省,草稿够用)</option>
              </select>
            </label>
          </div>
          <div className="form-grid mt-2">
            <label className="field">
              <span className="fl">首尾帧工作流 ID(有静帧的格走它)</span>
              <input value={wfI2v} placeholder="minimax_h3_lightx2v"
                onChange={(e) => setWfI2v(e.target.value)} />
            </label>
            <label className="field">
              <span className="fl">文生视频工作流 ID(没静帧的格走它)</span>
              <input value={wfT2v} placeholder="minimax_h3_lightx2v_no_pic"
                onChange={(e) => setWfT2v(e.target.value)} />
            </label>
          </div>
          <p className="hint">
            工作流 ID 在 autodl.art「ComfyUI 工作流」页每个卡片上能看到;留空则用默认的
            MiniMax H3 首尾帧 / 文生视频。两档之外的工作流(对口型/多图参考)后续版本接入。
          </p>
          <div className="form-actions mt-2">
            <button className="primary" disabled={saving} onClick={save}>
              {saving && <span className="spin spin-sm" />}保存出片配置
            </button>
          </div>
        </>
      )}
    </div>
  );
}
