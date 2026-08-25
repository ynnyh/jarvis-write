# app/engines/birthday/common.py
# -*- coding: utf-8 -*-
"""生日祝福引擎公共件:基调/关系目录、序列化、字典版切段(时间轴口径同 media.subtitles)。"""
from __future__ import annotations

from app.db.models import BirthdayWish
from app.engines.media.directions import direction_label
from app.engines.media.segments import plan_chunks

# 基调目录:key 白名单 + 给提示词的「节奏契约」(这个基调怎么拍才戳)。
# directive 借鉴开源 hyperframes 工作流的「叙事模板+节奏契约」:每条自带三幕路径,
# 模型照着走——生日片是被拍烂最多的品类,不给路径只会拍成蛋糕流水账。
BIRTHDAY_TONES: list[dict] = [
    {"key": "prank", "label": "整蛊爆笑",
     "directive": "伪装正经→最后一格全员亮蛋糕反转:前面越一本正经越好笑,梗必须长在寿星本人身上"},
    {"key": "tearjerk", "label": "催泪走心",
     "directive": "回忆物件特写开场→回忆杀层层递进→收在迟到的那句话:眼泪要具体,物件与口头禅是引信"},
    {"key": "warm", "label": "温馨日常",
     "directive": "平凡一天的蒙太奇→原来一直有人记得:不煽情,暖在细节密度,收在寿星会心一笑"},
    {"key": "surprise", "label": "惊喜反转",
     "directive": "假装全世界都忘了→暗地策划的蛛丝马迹→引爆:前半段的冷要真冷,引爆才有落差"},
    {"key": "hype", "label": "燃向里程碑",
     "directive": "把年龄拍成勋章:成人礼/而立/大寿,前压后爆,收在寿星挺胸抬头那一帧"},
    {"key": "cute", "label": "宠溺可爱",
     "directive": "周岁/宠物/恋人向:萌即正义,低视角、软质感、小手小脚的特写,收在奶呼呼的祝福"},
]
_TONE_MAP = {t["key"]: t for t in BIRTHDAY_TONES}
VALID_TONES = tuple(_TONE_MAP)

# 风格包目录:key 白名单 + 给提示词的「世界包」directive。
# 儿童向角色世界包(佩奇式/奥特曼式…):directive 三合一——强画风锚(锁死画面质感)+
# 世界观场景词(AI 从里面取景)+ 主角植入(寿星以该世界角色形象贯穿每一格)。
# **版权口径与灵感工坊 ghibli 先例一致**:UI 标签可以提示「同款气质」,但 directive
# 只描述画风特征与世界观、不点名品牌/IP 名——公网部署无版权风险,模型也不会往
# 临摹原片跑。label 里的「同款气质」是给用户选包用的暗号,不进提示词正文。
BIRTHDAY_PACKS: list[dict] = [
    {"key": "peppy", "label": "简笔动画 · 佩奇同款气质",
     "directive": "扁平简笔学前动画世界:粗描边、明快纯色平涂、简单几何形体、粉暖色调;"
                  "标志性场景——绿色山坡上的简笔小屋、跳泥坑溅起水花、雨天穿雨靴踩水、"
                  "一家子倒地大笑。寿星以简笔动画形象当主角,与动物一家人同吃同玩同闹,"
                  "每一格都有TA;收在全家与寿星一起跳泥坑或吹蜡烛的定格"},
    {"key": "hero", "label": "特摄英雄 · 奥特曼同款气质",
     "directive": "特摄英雄剧世界:银红配色皮套巨人的英雄感、变身闪光、十字光线、"
                  "城市夜景里的烟雾与火花、怪兽的巨大轮廓;节奏热血、镜头仰拍显巨大感。"
                  "寿星变身小小英雄,与巨人英雄并肩对战捣蛋怪兽,每一格都有TA;"
                  "收在胜利姿势定格(V字手/双手交叉发射光线),怪兽变成烟花散场"},
    {"key": "rescue3d", "label": "3D 救援队 · 汪汪队同款气质",
     "directive": "学前3D动画世界:圆润3D角色、高饱和糖果色、云朵般柔软的光;"
                  "标志性场景——瞭望塔总部、集合滑杆、头盔背包工程车装备、一次小小的"
                  "救援任务(救小猫/找回气球/清理路障)。寿星当救援队长,指挥动物队员"
                  "分工完成任务,每一格都有TA;收在任务达成全员击掌欢呼"},
    {"key": "dino", "label": "恐龙世界大冒险",
     "directive": "卡通化恐龙世界:丛林蕨类、远山火山、巨大但温和的恐龙伙伴;"
                  "标志性场景——骑在温和长颈恐龙背上眺望、和三角龙宝宝赛跑、"
                  "山洞里发现发光的恐龙蛋。寿星当小小探险家,每一格都有TA;"
                  "收在恐龙们围着寿星唱生日歌、尾巴尖挂着蛋糕"},
    {"key": "fairytale", "label": "童话城堡公主/王子",
     "directive": "童话绘本世界:城堡尖顶、舞会烛光、南瓜车、星星与月亮的装饰纹样,"
                  "柔和的金粉光;标志性场景——城堡大厅的加冕礼、旋转舞步、"
                  "魔法棒挥出星光。寿星当故事的主角(公主或王子由称呼性别自定),"
                  "每一格都有TA;收在众人向寿星行礼、王冠戴上头的那一帧"},
    {"key": "space", "label": "小小宇航员登月",
     "directive": "太空科幻世界:星云、舷窗里的地球、火箭舱内仪表的暖光、"
                  "月球表面的低重力蹦跳与扬尘;标志性场景——穿上小小宇航服、"
                  "飞船倒计时发射、月球漫步、月面插旗(旗面留白不写字)。"
                  "寿星当小小宇航员,每一格都有TA;收在寿星在月面向镜头敬礼,"
                  "头盔面罩上映出地球与蛋糕"},
]
_PACK_MAP = {p["key"]: p for p in BIRTHDAY_PACKS}
VALID_PACKS = tuple(_PACK_MAP)

# 关系目录:key 白名单 + label(决定提示词里的视角与口吻)
RELATIONSHIPS: list[dict] = [
    {"key": "mom", "label": "妈妈"},
    {"key": "dad", "label": "爸爸"},
    {"key": "partner", "label": "伴侣"},
    {"key": "bff", "label": "闺蜜/兄弟"},
    {"key": "child", "label": "孩子"},
    {"key": "friend", "label": "同事/朋友"},
    {"key": "self", "label": "自己(送自己)"},
    {"key": "idol", "label": "偶像(粉丝向)"},
]
_REL_MAP = {r["key"]: r for r in RELATIONSHIPS}
VALID_RELATIONSHIPS = tuple(_REL_MAP)

VALID_DURATIONS = (30, 60)
# 回忆点条数上下限(建单校验用;UI 文案建议 2-5 条,后端放宽到至少 1 条)
MAX_MEMORIES = 5
MEMORY_MAX_CHARS = 120


def tone_label(wish: BirthdayWish) -> str:
    """基调进提示词的写法:目录 key → 标签+节奏契约;自定义 → 原文 + 兜底契约
    (自定义基调没有目录 directive 可靠,不补一句「按三幕走」就会拍成蛋糕流水账)。"""
    if wish.tone in _TONE_MAP:
        t = _TONE_MAP[wish.tone]
        return f"{t['label']}({t['directive']})"
    custom = wish.custom_tone.strip()
    if not custom:
        return "(未定)"
    return (
        f"{custom}(自定义基调:仍按三幕节奏契约走——开场点名抛悬念、"
        "中段回忆具体到物、高潮收在动作帧,不许拍成蛋糕流水账)"
    )


def tone_display(wish: BirthdayWish) -> str:
    """列表用短标签。"""
    if wish.tone in _TONE_MAP:
        return _TONE_MAP[wish.tone]["label"]
    return wish.custom_tone.strip() or "自定义"


def relationship_label(key: str) -> str:
    return _REL_MAP.get(key, {}).get("label", "") or "(未填)"


def pack_label(key: str) -> str:
    """风格包短标签;空 key(不用包)返回空串,展示层按空处理。"""
    return _PACK_MAP.get(key, {}).get("label", "")


def pack_directive(key: str) -> str:
    """风格包的世界包 directive(强画风锚+场景词+主角植入);空 key 返回空串。"""
    return _PACK_MAP.get(key, {}).get("directive", "")


def shot_hint(duration_s: int) -> str:
    """时长 → 建议镜头数(提示词用)。30s 与情绪短片 30s 同档,60s 放宽到 12 格。"""
    return "5-7 格,每格 2-6 秒" if duration_s <= 30 else "9-12 格,每格 2-6 秒"


# =============== 切段(复用 media.segments 单点;时间轴与 SRT 同口径) ===============

def group_chunks(shots: list[dict], chunk_s: int) -> list[dict]:
    """把分镜 dict 列表按镜头边界贪心聚段,返回带起止时间码的段列表。"""
    return plan_chunks(shots, chunk_s)


# =============== 序列化 ===============

STATUS_CN = {
    "draft": "待生成",
    "generated": "候选已出",
    "picked": "已选定",
}


def wish_dict(row: BirthdayWish, with_candidates: bool = True) -> dict:
    d = {
        "id": row.id,
        "occasion": row.occasion,
        "tone": row.tone,
        "custom_tone": row.custom_tone,
        "tone_display": tone_display(row),
        "honoree_name": row.honoree_name,
        "relationship": row.relationship,
        "relationship_label": relationship_label(row.relationship),
        "milestone": row.milestone,
        "memories": row.memories or [],
        "sender_desc": row.sender_desc,
        "duration_s": row.duration_s,
        "pack": row.pack or "",
        "pack_label": pack_label(row.pack or ""),
        "direction": row.direction or "live",
        "direction_label": direction_label(row.direction or "live"),
        "style_hints": row.style_hints or "",
        "style_name": row.style_name,
        "style_cn": row.style_cn,
        "style_en": row.style_en,
        "negative": row.negative,
        "chosen": row.chosen,
        "clip": row.clip or {},
        "status": row.status,
        "status_cn": STATUS_CN.get(row.status, row.status),
    }
    if with_candidates:
        d["candidates"] = row.candidates or []
    return d
