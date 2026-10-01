"""小说/动画共用参考入口，分析与生效分开，变更保留旧正文并标记失效。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, get_current_user
from app.db.models import AnimeEpisode, AnimeSeries, Project
from app.db.session import get_db
from app.engines.creative import GOAL_KEY, analyze_references, project_goal, validated_observations
from app.jobs import list_running, spawn_job
from app.schemas.creative import GoalInput

router = APIRouter(prefix="/api/creative", tags=["creative"], dependencies=[Depends(get_current_user)])


def _owner(db: Session, scope: str, target_id: int):
    model = {"project": Project, "anime": AnimeSeries, "original": AnimeSeries}.get(scope)
    if model is None:
        raise HTTPException(404, "创作入口不存在")
    row = db.get(model, target_id)
    if row is None:
        raise HTTPException(404, "作品不存在")
    assert_project_owner(row)
    if scope in ("anime", "original") and row.workspace != scope:
        raise HTTPException(404, "作品不属于当前工作区")
    return row


def _goal(row, scope):
    return project_goal(row) if scope == "project" else row.creative_goal or {}


def _validate_form(row, scope, body):
    if scope in ("anime", "original") and body.form not in ("sketch", "anthology"):
        raise HTTPException(400, "动画系列当前按独立单集创作，请选情景短剧或多段子合集")
    if scope == "project":
        expected = {"serial": "serial", "short": "short", "drama": "continuous"}.get(row.mode, "serial")
        if body.form != expected:
            raise HTTPException(400, "参考方向的形式需要与当前开书模式一致，请先切换开书模式")


@router.get("/{scope}/{target_id}")
def get_goal(scope: str, target_id: int, db: Session = Depends(get_db)):
    return {"goal": _goal(_owner(db, scope, target_id), scope)}


@router.post("/{scope}/{target_id}/analyze")
async def analyze(scope: str, target_id: int, body: GoalInput, db: Session = Depends(get_db)):
    row = _owner(db, scope, target_id)
    _validate_form(row, scope, body)
    if not body.intent.strip() and not body.references:
        raise HTTPException(400, "写一句想要的体验，或添加参考材料")
    snapshot = body.model_copy(update={"expected_version": _goal(row, scope).get("version", 0)})
    kind = f"creative-{scope}-{target_id}"
    for jid, job in list_running(kind):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        progress("正在理解参考与创作要求…")
        # 纯分析，不跨网络持有数据库事务，也不会修改作品。
        return {"goal": await analyze_references(snapshot)}

    return {"job_id": spawn_job(kind, work)}


@router.put("/{scope}/{target_id}")
def save_goal(scope: str, target_id: int, body: GoalInput, db: Session = Depends(get_db)):
    row = _owner(db, scope, target_id)
    _validate_form(row, scope, body)
    old = _goal(row, scope)
    if body.expected_version != old.get("version", 0):
        raise HTTPException(409, "创作方向已更新，请刷新后再保存")
    # 在作品生成中改方向会把新旧目标混写在同一集/章里，等待本轮完成再切换。
    if scope in ("anime", "original"):
        eids = [e.id for e in db.query(AnimeEpisode).filter_by(series_id=target_id)]
        kinds = {f"anime-cast-{target_id}"} | {f"anime-{k}-{eid}" for eid in eids for k in ("takes", "script", "shots", "fp")}
    else:
        kinds = {f"architecture-{target_id}", f"blueprint-{target_id}", f"generate-{target_id}", f"queue-{target_id}"}
    if any(j["kind"] in kinds or (scope == "project" and j["kind"].startswith(f"chapter-{target_id}-")) for _, j in list_running("")):
        raise HTTPException(409, "作品正在生成，完成后再改方向")
    goal = {**body.model_dump(exclude={"expected_version", "fetch_links"}), "observations": validated_observations(body), "version": old.get("version", 0) + 1}
    goal["history"] = [*(old.get("history") or []), {k: v for k, v in old.items() if k != "history"}][-8:] if old else []
    if scope == "project":
        row.global_tendency = {**(row.global_tendency or {}), GOAL_KEY: goal}
        row.book_plans = None
        row.brief_confirmed = False
        row.concept_confirmed = False
        row.outline_stale = bool(row.architecture)
        if row.architecture:
            row.architecture.concept_stale = True
    else:
        row.creative_goal = goal
        for ep in db.query(AnimeEpisode).filter_by(series_id=target_id):
            # 保留旧成果供查看，禁止把旧成果直接当新方向产物继续加工。
            ep.creative_stale = True
            ep.takes = [{**t, "_stale": True} for t in (ep.takes or [])]
    db.commit()
    return {"goal": goal}
