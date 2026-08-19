# backend/deai_smoketest.py
# -*- coding: utf-8 -*-
"""去 AI 味实测(真实 LLM):正向锚 voice 的 on/off 对照 + 自愈闭环演示。

不走 DB / 不碰加密,直接用**环境变量**里的 provider 配置构造 adapter,再 monkeypatch
到真实引擎路径上(continue_tail / polish_text / deai_self_heal),跑完打印每段文本的
ai_flavor 分数(越低越干净)+ 正文本身,让你量化 + 肉眼判断:
  ① 双向锚定  —— 同一续写/润色,喂了名家 voice 锚 vs 没喂,AI 味差多少
  ② 自愈闭环  —— 一段脏文本经 deai_self_heal 定向去味,分数掉多少、文本变干净没
  ④ 铺满入口  —— 续写(此前全裸)、整章润色 现在都吃 voice 锚

key 只经环境变量,绝不写进代码或仓库。用法:
    cd backend
    export JARVIS_TEST_API_KEY=sk-xxxx                       # 必填
    export JARVIS_TEST_BASE_URL=https://api.deepseek.com     # 你的官方/中转地址
    export JARVIS_TEST_MODEL=deepseek-chat                   # 模型名
    export JARVIS_TEST_FORMAT=openai-compatible              # 或 anthropic / gemini
    export JARVIS_TEST_VOICE=yuhua                           # 文风胶囊(可选,默认 yuhua)
    python deai_smoketest.py
跑完把整段输出贴回来,我据分数与文本判断去味到位没有、要不要再调门槛/锚点。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 控制台默认 GBK,会把中文/符号编崩;强制 UTF-8(建议再 > out.txt 重定向后回贴)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import app.engines.pipeline.continuation as cont
import app.engines.polish.polisher as pol
from app.engines.polish.ai_flavor import ai_flavor_report
from app.llm.anthropic import AnthropicAdapter
from app.llm.gemini import GeminiAdapter
from app.llm.openai_compatible import OpenAICompatibleAdapter
from app.prompts.style_capsules import get_capsule, render_voice_block

_CLS = {
    "openai-compatible": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GeminiAdapter,
}

# 一段典型 AI 腔正文(神态套话 + 升华点题 + 论文式罗列 + 比喻堆砌),用作润色/自愈输入
DIRTY = (
    "夜色如墨,仿佛给整座城市披上了一层神秘的面纱。他的眼中闪过一丝不易察觉的复杂神色,"
    "嘴角勾起一抹意味深长的弧度。空气仿佛在这一刻凝固了,时间仿佛静止了一般。"
    "他沉默片刻,缓缓开口,声音低沉而富有磁性:「有些事,终究是躲不过的。」"
    "他心中五味杂陈,一种难以言喻的情绪涌上心头。首先,他明白自己已别无选择;"
    "其次,他清楚前路必然充满荆棘;最后,他更加坚定了内心的信念。"
    "总而言之,这一夜注定不平凡,命运的齿轮,已然开始悄然转动。"
)

# 一段相对干净的正文结尾,用作续写起点:看模型往下续得干净(接得住)还是自己飘成 AI 腔
TAIL = (
    "他把车停在巷口,熄了火。雨还在下。"
    "对面那栋楼三层的灯亮着,是她的房间。"
    "他看了一眼手机,十一点四十。"
)


def _show(label: str, text: str) -> None:
    r = ai_flavor_report(text)
    top = "、".join(h.phrase for h in r.hits[:5]) or "(无明显命中)"
    print(f"\n--- {label} | AI味分={r.score:.1f}(越低越干净)命中{len(r.hits)}处:{top} ---")
    print(text.strip())


async def main() -> None:
    key = os.environ.get("JARVIS_TEST_API_KEY", "").strip()
    if not key:
        print("[X] 请先 export JARVIS_TEST_API_KEY=你的key(以及 BASE_URL / MODEL / FORMAT)")
        return
    fmt = os.environ.get("JARVIS_TEST_FORMAT", "openai-compatible").strip()
    cls = _CLS.get(fmt)
    if cls is None:
        print(f"[X] 未知 FORMAT:{fmt},可选 {list(_CLS)}")
        return
    base = os.environ.get("JARVIS_TEST_BASE_URL", "").strip()
    model = os.environ.get("JARVIS_TEST_MODEL", "").strip()
    if not model:
        print("[X] 请先 export JARVIS_TEST_MODEL=模型名(如 deepseek-chat / gpt-4o-mini)")
        return
    voice_key = os.environ.get("JARVIS_TEST_VOICE", "yuhua").strip()
    if get_capsule(voice_key) is None:
        print(f"[X] 未知文风胶囊:{voice_key}(试试 yuhua/luxun/wangzengqi/plain 等)")
        return

    adapter = cls(
        api_key=key,
        base_url=base or None,
        model_name=model,
        temperature=0.8,
        max_tokens=4096,
        timeout=180,
    )
    # 把真实引擎里"按任务从 DB 取模型"的 get_adapter_for 换成这一个 adapter(实测直连)
    cont.get_adapter_for = lambda *a, **k: adapter
    pol.get_adapter_for = lambda *a, **k: adapter

    voice = render_voice_block(voice_key=voice_key)
    cap = get_capsule(voice_key)
    print(f"实测配置:format={fmt} model={model or '(adapter默认)'} 文风锚={cap.name}")
    print("=" * 72)

    # ① + ④:续写对照 —— 续写此前完全裸奔,现在能吃 voice 锚
    print("\n【④ 续写对照】同一正文结尾,voice 锚 关 vs 开")
    off = await cont.continue_tail("本章:雨夜,他在她楼下犹豫要不要上去", "前情:两人冷战三天", TAIL, voice_block="")
    on = await cont.continue_tail("本章:雨夜,他在她楼下犹豫要不要上去", "前情:两人冷战三天", TAIL, voice_block=voice)
    _show("续写 · voice 关", off)
    _show(f"续写 · voice 开({cap.name})", on)

    # ②:自愈闭环 —— 脏文本定向去味,看分数掉多少
    print("\n" + "=" * 72)
    print("\n【② 自愈闭环】脏文本 → 定向去味重写(带安全阀,没改好就回退)")
    healed, before, after = await pol.deai_self_heal(DIRTY)
    print(f"自愈前 AI味分={before.score:.1f} → 自愈后={after.score:.1f}("
          f"{'已改善' if after.score < before.score else '未改善→已回退保原文'})")
    _show("原始脏文本", DIRTY)
    _show("自愈去味后", healed)

    # ④:整章润色 voice 对照 —— 润色也从档案自动吃 voice
    print("\n" + "=" * 72)
    print("\n【④ 润色 voice 对照】同一脏文本,润色时 voice 锚 关 vs 开")
    r_off = await pol.polish_text(DIRTY)
    r_on = await pol.polish_text(DIRTY, global_tendency={"_profile": {"voice_key": voice_key}})
    _show("润色 · voice 关", r_off["polished"])
    _show(f"润色 · voice 开({cap.name})", r_on["polished"])

    print("\n" + "=" * 72)
    print("看点:voice 开的续写/润色应更贴该名家语感、AI味分更低;自愈应把脏文本分数显著压下来。")


if __name__ == "__main__":
    asyncio.run(main())
