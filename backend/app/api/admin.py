# app/api/admin.py
# -*- coding: utf-8 -*-
"""后台管理接口(阶段 9):用户列表 / 重置密码 / 禁用启用 / 删用户 / 邀请码。

GET    /api/admin/users                       全部用户(含项目数与用量汇总)
POST   /api/admin/users/{id}/reset-password   重置某用户密码
PATCH  /api/admin/users/{id}                  禁用 / 启用(不能禁用自己)
DELETE /api/admin/users/{id}                  删用户及其全部项目数据(不能删自己)
GET    /api/admin/invite-codes                邀请码列表(附旧单码回落状态)
POST   /api/admin/invite-codes                新建邀请码(可备注 / 限次)
PATCH  /api/admin/invite-codes/{id}           停用 / 启用某个邀请码
DELETE /api/admin/invite-codes/{id}           删除邀请码
GET    /api/admin/quality-overview            生成质量聚合(截断率/回炉画像/问题分布/体量)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import delete_project_cascade
from app.auth import get_current_user, hash_password
from app.config import get_settings
from app.db.models import (
    AppSetting,
    Chapter,
    ChapterFeedback,
    ChapterIssue,
    FeatureUsage,
    InviteCode,
    LlmUsage,
    Project,
    ProviderConfig,
    ProviderSetting,
    User,
)
from app.db.session import get_db
from app.engines.polish.ai_flavor import (
    rule_config,
    set_gate_override,
    set_weight_overrides,
    weight_overrides,
)

logger = logging.getLogger("jarvis-write.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

_INVITE_CODE_KEY = "invite_code"


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """FastAPI 依赖:在校验登录的基础上要求管理员。"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def get_effective_invite_code(db: Session) -> tuple[str, str]:
    """当前生效的邀请码及其来源:DB 有记录(含空串)以 DB 为准,否则回落 .env。"""
    row = db.get(AppSetting, _INVITE_CODE_KEY)
    if row is not None:
        return row.value, "db"
    return get_settings().invite_code, "env"


# ---------- 用户管理 ----------


class AdminUserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: str
    project_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_calls: int


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """全部用户 + 项目数 + LLM 用量汇总(llm_usage 按 user_id 记账)。"""
    users = list(db.query(User).order_by(User.id))
    project_counts = dict(
        db.query(Project.user_id, func.count(Project.id))
        .group_by(Project.user_id)
        .all()
    )
    usage_rows = (
        db.query(
            LlmUsage.user_id,
            func.count(LlmUsage.id),
            func.sum(LlmUsage.prompt_tokens),
            func.sum(LlmUsage.completion_tokens),
        )
        .group_by(LlmUsage.user_id)
        .all()
    )
    usage = {
        uid: (int(calls or 0), int(prompt or 0), int(completion or 0))
        for uid, calls, prompt, completion in usage_rows
    }
    return [
        AdminUserOut(
            id=u.id,
            username=u.username,
            is_admin=u.is_admin,
            is_active=u.is_active,
            created_at=u.created_at.isoformat() if u.created_at else "",
            project_count=project_counts.get(u.id, 0),
            total_calls=usage.get(u.id, (0, 0, 0))[0],
            total_prompt_tokens=usage.get(u.id, (0, 0, 0))[1],
            total_completion_tokens=usage.get(u.id, (0, 0, 0))[2],
        )
        for u in users
    ]


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    req: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """管理员重置某用户密码(校验规则与注册一致)。"""
    user = _get_user_or_404(db, user_id)
    # bcrypt 只取密码前 72 字节,超长会直接抛 ValueError;提前拦截给明确提示
    if len(req.password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=400,
            detail="密码过长:按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)",
        )
    user.password_hash = hash_password(req.password)
    db.commit()
    logger.info("管理员重置了用户 %s 的密码", user.username)
    return {"ok": True}


class UserPatch(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: int,
    req: UserPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """禁用 / 启用账号。禁用后旧 token 立即失效(见 get_current_user)。"""
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id and not req.is_active:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    user.is_active = req.is_active
    db.commit()
    logger.info(
        "管理员%s了用户 %s", "启用" if req.is_active else "禁用", user.username
    )
    return {"ok": True, "is_active": req.is_active}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """删除用户:级联清掉其名下全部项目的关联数据,以及设置与用量记录。"""
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")

    deleted_projects = 0
    for project in db.query(Project).filter(Project.user_id == user.id).all():
        delete_project_cascade(db, project)
        deleted_projects += 1
    db.query(ProviderSetting).filter(
        ProviderSetting.user_id == user.id
    ).delete(synchronize_session=False)
    db.query(ProviderConfig).filter(
        ProviderConfig.user_id == user.id
    ).delete(synchronize_session=False)
    db.query(LlmUsage).filter(LlmUsage.user_id == user.id).delete(
        synchronize_session=False
    )
    db.delete(user)
    db.commit()
    logger.info(
        "管理员删除了用户 %s(含 %d 个项目)", user.username, deleted_projects
    )
    return {"ok": True, "deleted_projects": deleted_projects}


# ---------- 邀请码(多码体系) ----------


class InviteCodeItem(BaseModel):
    id: int
    code: str
    note: str | None
    max_uses: int | None
    used_count: int
    is_active: bool
    created_at: str


class LegacyFallback(BaseModel):
    """表为空时仍在生效的旧单码(app_settings / .env),前端用来提示过渡状态。"""

    code: str
    source: str  # db / env


class InviteCodeListOut(BaseModel):
    items: list[InviteCodeItem]
    legacy_fallback: LegacyFallback | None


class InviteCodeCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{4,64}$")
    note: str | None = Field(default=None, max_length=200)
    max_uses: int | None = Field(default=None, ge=1)


class InviteCodePatch(BaseModel):
    is_active: bool


def _to_item(row: InviteCode) -> InviteCodeItem:
    return InviteCodeItem(
        id=row.id,
        code=row.code,
        note=row.note,
        max_uses=row.max_uses,
        used_count=row.used_count,
        is_active=row.is_active,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/invite-codes", response_model=InviteCodeListOut)
async def list_invite_codes(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """邀请码列表;表为空时附当前生效的旧单码,便于前端提示过渡状态。"""
    rows = list(db.query(InviteCode).order_by(InviteCode.id))
    legacy = None
    if not rows:
        code, source = get_effective_invite_code(db)
        legacy = LegacyFallback(code=code, source=source)
    return InviteCodeListOut(items=[_to_item(r) for r in rows], legacy_fallback=legacy)


@router.post("/invite-codes", response_model=InviteCodeItem)
async def create_invite_code(
    req: InviteCodeCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    code = req.code.strip()
    if db.query(InviteCode).filter(InviteCode.code == code).first():
        raise HTTPException(status_code=400, detail="邀请码已存在")
    row = InviteCode(
        code=code,
        note=req.note.strip() if req.note else None,
        max_uses=req.max_uses,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("管理员创建了邀请码 %s(上限:%s)", row.code, row.max_uses or "不限")
    return _to_item(row)


def _get_invite_or_404(db: Session, invite_id: int) -> InviteCode:
    row = db.get(InviteCode, invite_id)
    if row is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    return row


@router.patch("/invite-codes/{invite_id}", response_model=InviteCodeItem)
async def patch_invite_code(
    invite_id: int,
    req: InviteCodePatch,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    row = _get_invite_or_404(db, invite_id)
    row.is_active = req.is_active
    db.commit()
    logger.info(
        "管理员%s了邀请码 %s", "启用" if req.is_active else "停用", row.code
    )
    return _to_item(row)


@router.delete("/invite-codes/{invite_id}")
async def delete_invite_code(
    invite_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    row = _get_invite_or_404(db, invite_id)
    db.delete(row)
    db.commit()
    logger.info("管理员删除了邀请码 %s", row.code)
    return {"ok": True}


# ---------- AI 味检测热更配置(类别权重 + 自愈门槛) ----------
# 检测规则/权重改代码要发版;线上某类误伤/漏杀时,这里在线调参立即生效,
# 配置本体存 AppSetting(key=ai_flavor_config),启动时 load 进内存。

_FLAVOR_CFG_KEY = "ai_flavor_config"
_FLAVOR_WEIGHT_RANGE = (0.0, 5.0)
_FLAVOR_GATE_RANGE = (0.0, 30.0)


class AiFlavorConfigOut(BaseModel):
    gate_score: float                    # 当前生效的自愈门槛(默认或覆盖)
    weights: dict[str, float]            # 当前生效的类别权重(默认+覆盖合并)
    categories: list[dict]               # 类别目录(类别名/出厂权重/当前权重/规则数)


class AiFlavorConfigIn(BaseModel):
    gate_score: float = Field(ge=_FLAVOR_GATE_RANGE[0], le=_FLAVOR_GATE_RANGE[1])
    # 只收覆盖项(与出厂相同的也允许,落库时一并存);类别名不存在的被忽略
    weights: dict[str, float]


def _save_flavor_config(db: Session, cfg: dict) -> None:
    row = db.get(AppSetting, _FLAVOR_CFG_KEY)
    if row is None:
        row = AppSetting(key=_FLAVOR_CFG_KEY)
        db.add(row)
    row.value = json.dumps(cfg, ensure_ascii=False)
    db.commit()


def load_ai_flavor_config() -> None:
    """启动时把 AppSetting 里的热更配置载进检测模块内存(找不到就维持出厂值)。

    供 main.lifespan 调用;DB 异常不阻断启动(检测回落到代码常量,行为同未配置)。
    """
    from app.db.session import SessionLocal

    try:
        session = SessionLocal()
        try:
            row = session.get(AppSetting, _FLAVOR_CFG_KEY)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 配置加载失败不拦启动
        logger.warning("读取 AI 味热更配置失败,维持出厂值", exc_info=True)
        return
    if not row or not row.value:
        return
    try:
        cfg = json.loads(row.value)
        set_weight_overrides(cfg.get("weights") or {})
        set_gate_override(cfg.get("gate_score"))
    except (ValueError, TypeError):
        logger.warning("AI 味热更配置损坏(%r),维持出厂值", row.value[:200])


def _apply_flavor_config(db: Session, req: AiFlavorConfigIn) -> AiFlavorConfigOut:
    """落库 + 更新内存 + 返回生效后的全量配置(GET/PUT 共用的单一出口)。"""
    valid = {c["category"] for c in rule_config()}
    unknown = [k for k in req.weights if k not in valid]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"未知规则类别:{'、'.join(unknown)}"
        )
    for k, v in req.weights.items():
        if not (_FLAVOR_WEIGHT_RANGE[0] <= v <= _FLAVOR_WEIGHT_RANGE[1]):
            raise HTTPException(
                status_code=400,
                detail=f"类别「{k}」权重 {v} 超出范围 {_FLAVOR_WEIGHT_RANGE}",
            )
    set_weight_overrides(req.weights)
    set_gate_override(req.gate_score)
    _save_flavor_config(
        db, {"gate_score": req.gate_score, "weights": req.weights}
    )
    return AiFlavorConfigOut(
        gate_score=req.gate_score,
        weights={c["category"]: c["weight"] for c in rule_config()},
        categories=rule_config(),
    )


@router.get("/ai-flavor-config", response_model=AiFlavorConfigOut)
async def get_ai_flavor_config(
    _admin: User = Depends(get_current_admin),
):
    """AI 味检测当前配置:生效门槛 + 各类别生效权重(含出厂值对照)。"""
    from app.engines.polish.polisher import get_deai_gate

    return AiFlavorConfigOut(
        gate_score=get_deai_gate(),
        weights={c["category"]: c["weight"] for c in rule_config()},
        categories=rule_config(),
    )


@router.put("/ai-flavor-config", response_model=AiFlavorConfigOut)
async def put_ai_flavor_config(
    req: AiFlavorConfigIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """在线调参:整组覆盖类别权重 + 自愈门槛,落库并立即生效(不重启)。"""
    out = _apply_flavor_config(db, req)
    logger.info(
        "管理员更新了 AI 味检测配置:门槛 %.1f,权重覆盖 %s",
        out.gate_score, weight_overrides() or "(无)",
    )
    return out


@router.get("/usage")
async def feature_usage_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """功能使用统计:各功能线的 使用人数 / 动作次数 / 最后使用时间。

    数据源是 app/usage.py 的动作级计数(非 GET 已鉴权请求,30s 批量落库),
    给「哪个工坊值得继续投入」提供最小依据。只读,不含任何用户内容。
    """
    rows = (
        db.query(
            FeatureUsage.feature,
            func.count(func.distinct(FeatureUsage.user_id)),
            func.sum(FeatureUsage.uses),
            func.max(FeatureUsage.last_used_at),
        )
        .group_by(FeatureUsage.feature)
        .order_by(func.sum(FeatureUsage.uses).desc())
        .all()
    )
    return {
        "usage": [
            {
                "feature": feature,
                "users": users,
                "uses": int(total or 0),
                "last_used_at": last,
            }
            for feature, users, total, last in rows
        ]
    }


# ---------- 生成质量观测(线上归因闭环的聚合半环) ----------


def _ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _iter_review_snapshots(db: Session, since):
    """逐章解析 review_snapshot;损坏/空快照跳过,不让一条脏数据拖垮整体。"""
    chapters = (
        db.query(Chapter)
        .filter(Chapter.review_snapshot.isnot(None), Chapter.review_snapshot != "")
        .filter(Chapter.updated_at >= since)
        .all()
    )
    for ch in chapters:
        try:
            snap = json.loads(ch.review_snapshot)
        except (ValueError, TypeError):
            continue
        if isinstance(snap, dict):
            yield ch, snap


def quality_overview(db: Session, days: int) -> dict:
    """生成质量聚合:只读既有落库信号,不做任何 LLM 调用。

    四路信号(口径见 docs/17):
    - llm:截断率与 finish_reason=length 占比 —— 「以为是模型能力,其实是截断」的判据
    - rework:review_snapshot.rework_log 的 trigger 分布(含 gate_degraded 静默降级隔离)
    - issues:chapter_issues 按 issue_type × severity,open 挂起数
    - volume:章节字数体量(供与差评交叉时对照)
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 1) LLM 调用:截断 / 输出预算用尽 / 按模型分布
    total_calls = (
        db.query(func.count(LlmUsage.id)).filter(LlmUsage.created_at >= since).scalar() or 0
    )
    truncated_calls = (
        db.query(func.count(LlmUsage.id))
        .filter(LlmUsage.created_at >= since, LlmUsage.truncated.is_(True))
        .scalar() or 0
    )
    finish_length = (
        db.query(func.count(LlmUsage.id))
        .filter(LlmUsage.created_at >= since, LlmUsage.finish_reason == "length")
        .scalar() or 0
    )
    prompt_tokens = (
        db.query(func.coalesce(func.sum(LlmUsage.prompt_tokens), 0))
        .filter(LlmUsage.created_at >= since)
        .scalar()
    )
    completion_tokens = (
        db.query(func.coalesce(func.sum(LlmUsage.completion_tokens), 0))
        .filter(LlmUsage.created_at >= since)
        .scalar()
    )
    by_model_rows = (
        db.query(
            LlmUsage.model,
            func.count(LlmUsage.id),
            func.sum(LlmUsage.truncated),
        )
        .filter(LlmUsage.created_at >= since)
        .group_by(LlmUsage.model)
        .order_by(func.count(LlmUsage.id).desc())
        .all()
    )

    # 2) 回炉画像:解析各章 review_snapshot
    reviewed = passed = gate_degraded = stalled_hint = 0
    revision_rounds_total = 0
    trigger_counts: dict[str, int] = {}
    for _ch, snap in _iter_review_snapshots(db, since):
        reviewed += 1
        if snap.get("passed"):
            passed += 1
        rounds = snap.get("revision_rounds") or 0
        revision_rounds_total += int(rounds if isinstance(rounds, (int, float)) else 0)
        if snap.get("hints"):
            stalled_hint += 1
        for entry in snap.get("rework_log") or []:
            if not isinstance(entry, dict):
                continue
            trigger = str(entry.get("trigger") or "unknown")
            trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1
            if trigger == "gate_degraded":
                gate_degraded += 1

    # 3) 质量问题:按类型 × 严重度,open 挂起数
    issue_rows = (
        db.query(ChapterIssue.issue_type, ChapterIssue.severity, func.count(ChapterIssue.id))
        .filter(ChapterIssue.created_at >= since)
        .group_by(ChapterIssue.issue_type, ChapterIssue.severity)
        .all()
    )
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for issue_type, severity, count in issue_rows:
        by_type[issue_type] = by_type.get(issue_type, 0) + int(count)
        by_severity[severity] = by_severity.get(severity, 0) + int(count)
    open_issues = (
        db.query(func.count(ChapterIssue.id))
        .filter(ChapterIssue.created_at >= since, ChapterIssue.status == "open")
        .scalar() or 0
    )

    # 4) 章节体量
    vol_rows = db.query(func.count(Chapter.id), func.coalesce(func.avg(Chapter.word_count), 0)).filter(
        Chapter.updated_at >= since
    ).one()

    # 5) 用户反馈 + 交叉归因:差评章的截断率/降级率 vs 全体均值。
    #    「没监控以为是模型能力问题,有监控发现是截断」——这一步就是归因。
    fb_rows = (
        db.query(ChapterFeedback.rating, ChapterFeedback.categories, ChapterFeedback.chapter_id)
        .filter(ChapterFeedback.created_at >= since)
        .all()
    )
    good = sum(1 for r, _c, _cid in fb_rows if r == "good")
    bad = len(fb_rows) - good
    cat_counts: dict[str, int] = {}
    bad_chapter_ids: set[int] = set()
    for rating, categories, chapter_id in fb_rows:
        if rating != "bad":
            continue
        bad_chapter_ids.add(chapter_id)
        for cat in categories or []:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    def _degraded_ratio(chapter_ids: set[int]) -> float:
        """一组章节里出现过门禁降级隔离的占比(llm_usage 无章节关联,
        截断率做不了按章交叉——那是按调用计的口径,别假装能算)。"""
        if not chapter_ids:
            return 0.0
        snaps = (
            db.query(Chapter.review_snapshot)
            .filter(Chapter.id.in_(chapter_ids))
            .all()
        )
        degraded = sum(1 for (snap,) in snaps if _snap_has_trigger(snap, "gate_degraded"))
        return _ratio(degraded, len(snaps))

    return {
        "days": days,
        "llm": {
            "total_calls": int(total_calls),
            "truncated_calls": int(truncated_calls),
            "truncated_ratio": _ratio(int(truncated_calls), int(total_calls)),
            "finish_length_calls": int(finish_length),
            "finish_length_ratio": _ratio(int(finish_length), int(total_calls)),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "by_model": [
                {
                    "model": model,
                    "calls": int(calls),
                    "truncated": int(truncated or 0),
                    "truncated_ratio": _ratio(int(truncated or 0), int(calls)),
                }
                for model, calls, truncated in by_model_rows
            ],
        },
        "rework": {
            "chapters_reviewed": reviewed,
            "passed": passed,
            "pass_ratio": _ratio(passed, reviewed),
            "avg_revision_rounds": (
                round(revision_rounds_total / reviewed, 2) if reviewed else 0.0
            ),
            "gate_degraded_count": gate_degraded,
            "stalled_hint_chapters": stalled_hint,
            "trigger_counts": trigger_counts,
        },
        "issues": {
            "open_count": int(open_issues),
            "by_type": by_type,
            "by_severity": by_severity,
        },
        "volume": {
            "chapters": int(vol_rows[0] or 0),
            "avg_word_count": round(float(vol_rows[1] or 0), 1),
        },
        "feedback": {
            "total": len(fb_rows),
            "good": good,
            "bad": bad,
            "bad_ratio": _ratio(bad, len(fb_rows)),
            "by_category": cat_counts,
            "cross": {
                "bad_chapters": {
                    "count": len(bad_chapter_ids),
                    "degraded_ratio": _degraded_ratio(bad_chapter_ids),
                },
                # 全体基线:差评章降级率显著高于它 → 差评主因在模型稳定性,
                # 接近它 → 差评主因在内容本身(文风/节奏),与降级无关
                "baseline": {
                    "degraded_ratio": _ratio(gate_degraded, reviewed),
                },
            },
        },
    }


def _snap_has_trigger(snapshot_raw: str | None, trigger: str) -> bool:
    """review_snapshot 里是否出现过指定 trigger(降级隔离等)。脏快照按 False。"""
    if not snapshot_raw:
        return False
    try:
        snap = json.loads(snapshot_raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(snap, dict):
        return False
    return any(
        isinstance(e, dict) and e.get("trigger") == trigger
        for e in (snap.get("rework_log") or [])
    )


class QualityOverviewOut(BaseModel):
    days: int
    llm: dict
    rework: dict
    issues: dict
    volume: dict
    feedback: dict


@router.get("/quality-overview", response_model=QualityOverviewOut)
async def get_quality_overview(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """生成质量聚合(只读):截断率 / 回炉画像 / 问题分布 / 章节体量。

    数据全部来自既有落库(llm_usage / review_snapshot / chapter_issues / chapters),
    零 LLM 成本。与 /usage(功能使用账)互补:那边回答「谁在用哪条线」,
    这里回答「生成质量哪里在出问题」。
    """
    return quality_overview(db, days)
