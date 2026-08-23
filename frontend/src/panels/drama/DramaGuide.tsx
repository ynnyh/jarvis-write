// 漫剧工坊引导:进来先看这张卡就知道该点哪儿。
// 两块分开收起(各自的 localStorage 键):
// - DramaGuide:这是什么 + 五步怎么走 + 按钮点不动怎么办(顶部);
// - DramaProductionGuide:拿到手册之后在本站之外怎么出片(底部,最容易迷路的一段)。
// 样式沿用 ProjectPage 引导条那套(notice/step-guide/guide-body/guide-mini),不另起风格。
import { useState } from "react";

/** 可收起引导卡的公共外壳:收起后只留一枚小按钮,不占版面。 */
function Collapsible({ storageKey, mini, children }: {
  storageKey: string;
  mini: string;
  children: React.ReactNode;
}) {
  const [hidden, setHidden] = useState(() => localStorage.getItem(storageKey) === "1");
  if (hidden) {
    return (
      <button type="button" className="guide-mini muted"
        onClick={() => { localStorage.removeItem(storageKey); setHidden(false); }}>
        ⓘ {mini}
      </button>
    );
  }
  return (
    <div className="notice notice-info step-guide drama-guide">
      <div className="guide-body">{children}</div>
      <div className="guide-side">
        <button className="btn-sm"
          onClick={() => { localStorage.setItem(storageKey, "1"); setHidden(true); }}>
          收起
        </button>
      </div>
    </div>
  );
}

export function DramaGuide() {
  return (
    <Collapsible storageKey="drama-guide-hidden" mini="漫剧工坊怎么用">
      <div className="mb-2">
        <b>这是什么:</b>把已定稿的小说改成一份<b>拍摄手册</b>——分镜表 + 每格的绘图提示词 +
        配音稿 + 剪辑清单 + SRT 字幕。本站<b>不出图、不出视频、不合成语音</b>:那几步在你顺手的
        工具里做(见下方「出片指引」),这里负责把「该拍什么、怎么拍、提示词怎么写」全给你。
      </div>
      <div className="mb-2"><b>照着 ①→⑤ 走</b>(每步都能单独重跑,改一步不用推倒重来):</div>
      <ol className="drama-guide-steps">
        <li>
          <b>风格卡</b>:先拍板画风方向(拿不定就点「AI 荐方向」),AI 写一段「画风锁定段」。
          这段会逐字塞进之后<b>每一格</b>提示词——上百格画风不漂移靠它。
        </li>
        <li>
          <b>角色/场景卡</b>:从故事圣经批量出「锁定外貌段」,同一个人不换脸靠它。
          再点「出定妆照」——每个角色一条参考图提示词,<b>先出一张正面半身定妆照上传回来</b>,
          之后每格出图都把它当参考图传给生图站,人物才真锁得住脸(光靠文字描述压不到零)。
          手改过的卡点「🔒 锁定」,以后重跑不会覆盖你的改动。
        </li>
        <li>
          <b>切集</b>:选已定稿的章节范围,AI 按短剧节奏切成一集集(默认 90 秒一集),
          每集自带开场钩子与结尾卡点。一章可拆多集,过渡章也可以数章并一集。
        </li>
        <li>
          <b>单集流水线</b>:在集列表里<b>点任意一集</b>展开,然后按顺序
          ④-1 写剧本 → ④-2 拆分镜 → ④-3 出提示词 → ④-4 出成片包,最后「导出手册」。
          一格提示词不满意,用那格的「重出这格」补要求重生成,不必整集重跑。
        </li>
        <li><b>预告片</b>(可选):从各集的钩子/卡点混剪一条 30-60 秒宣传片,发布引流用。</li>
      </ol>
      <div><b>按钮点不动 / 报错了:</b></div>
      <ul className="drama-guide-steps">
        <li>「拆分镜」是灰的 → 这一集还没剧本,先点 ④-1 写剧本。</li>
        <li>「出提示词」是灰的 → 还没分镜,先点 ④-2 拆分镜。</li>
        <li>提示「先生成美术风格卡」→ 回 ① 定画风,提示词的画风锚从那里来。</li>
        <li>「出定妆照」提示「还没有美术风格卡」→ 同上,定妆照要带画风锚才跟正片是一套。</li>
        <li>
          点「出定妆照」提示「都已经有了」→ 只补缺不覆盖,想重写某个角色用那张卡上的
          「重出提示词」。上传定妆照被拒 → 只收真的 PNG/JPG/WebP(改扩展名没用),
          单张 4MB、一个角色最多 3 张。
        </li>
        <li>提示「没有正文」→ 这一集的源章还没定稿,回写作区把那几章定稿。</li>
        <li>整个工坊进不去 → 一章都没定稿。漫剧改编的是小说<b>成稿</b>,先去写作区定稿几章。</li>
      </ul>
    </Collapsible>
  );
}

export function DramaProductionGuide() {
  return (
    <Collapsible storageKey="drama-outguide-hidden" mini="出片指引(手册拿到之后怎么做)">
      <div className="mb-2">
        <b>出片指引:</b>下面这几步在<b>本站之外</b>做。导出的手册里每一格该干什么都写好了,
        照着顺序走一集大约十几分钟。
      </div>
      <ol className="drama-guide-steps">
        <li>
          <b>先出定妆照</b>:回 ② 角色卡,每个角色的「一键粘贴」整段粘进生图站出一张正面半身像,
          满意了下载,再传回角色卡存好。这一步花几分钟,省掉后面上百格换脸的返工。
        </li>
        <li>
          <b>出图</b>:每格提示词那里有「一键粘贴 · 你用的生图站」下拉——
          <b>只有一个描述框</b>的站(GPT-image / DALL·E / 豆包 / 通义)选第一项,负面词已经改写成
          「不要出现」并进正文,<b>整段粘一次就行</b>;<b>有负面词框</b>的站(即梦/可灵/SD)选第二项,
          正文与负面词分开粘;Midjourney 选第三项(英文 + <code>--ar</code>/<code>--no</code> 已带好)。
          出图前先在站点点「上传参考图」把该角色的定妆照传上去。同一集一次做完,别换平台换模型,
          换了画风就漂。
        </li>
        <li>
          <b>让它动起来</b>:静帧丢进图生视频(即梦/可灵/Runway),按分镜表的「运镜」字段
          给推/拉/摇/跟随,时长照「秒」那一列,一格 2-8 秒。
        </li>
        <li>
          <b>配音</b>:按成片包的配音稿逐条合成——剪映「文本朗读」最省事,火山引擎/MiniMax
          音色更多;每个角色挑哪种音色,看角色卡的「TTS 选型建议」与「朗读指示」。
        </li>
        <li>
          <b>拼装</b>:剪映里按剪辑清单的顺序摆片段 → 导入 SRT 压字幕 → 按 <code>bgm_tag</code>
          分段铺音乐 → 转场照清单给(冲突用快切,情绪落点用叠化)。
        </li>
        <li>
          <b>成本参考</b>:同类开源项目(Toonflow)自报一集约 ¥130——视频生成 ¥120 是大头,
          文本 ¥10,出图 &lt;¥1。按你选的平台、时长、重试次数浮动,<b>以平台实际计费为准</b>。
          想省钱就先只做静帧 + 轻微缩放平移,别每格都跑图生视频。
        </li>
      </ol>
      <div className="hint drama-compliance">
        合规提醒:配音请用平台<b>正版音色库</b>,不要克隆真人声音;商用发布前确认所选音色与
        出图模型的<b>商用授权</b>。
      </div>
    </Collapsible>
  );
}
