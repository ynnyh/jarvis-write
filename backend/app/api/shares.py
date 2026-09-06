# app/api/shares.py
# -*- coding: utf-8 -*-
"""公开分享链接:整本书/单章以只读链接分享给任何人(免登录阅读)。

两个面:
- 作者面(挂 projects 前缀,带鉴权):创建 / 列表 / 撤销;
- 公开面(/api/public/shares/{token},无鉴权):凭 token 取只读内容——
  只回书名与正文,不含设定/圣经/伏笔/大纲等任何其他数据,也不含用户信息。

安全要点:token 是唯一凭证(secrets.token_urlsafe,不可枚举);可随时撤销;
撤销/项目删除后立即 404;公开端点只读、无 AI、不泄露任何 key 或用户信息。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Chapter, Outline, Project, ShareLink, User
from app.db.session import get_db

# =============== 作者面:创建 / 列表 / 撤销 ===============

owner_router = APIRouter(prefix="/api/projects", tags=["shares"])


class ShareCreate(BaseModel):
    scope: str = Field(default="book", description="book=整本书 / chapter=单章")
    chapter_number: int | None = Field(default=None, ge=1, description="scope=chapter 时必填")


class ShareOut(BaseModel):
    id: int
    token: str
    scope: str
    chapter_number: int | None
    revoked: bool
    view_count: int


class PublicShareOut(BaseModel):
    scope: str
    book_title: str
    genre: str
    # book:全部有正文的章(按章号排序);chapter:单元素列表
    chapters: list[dict]


def _get_owned_project(db: Session, project_id: int, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user.id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _get_share_owned(db: Session, share_id: int, user: User) -> ShareLink:
    row = (
        db.query(ShareLink)
        .filter(ShareLink.id == share_id, ShareLink.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    return row


def _share_out(row: ShareLink) -> ShareOut:
    return ShareOut(
        id=row.id, token=row.token, scope=row.scope,
        chapter_number=row.chapter_number, revoked=row.revoked,
        view_count=row.view_count,
    )


@owner_router.post("/{project_id}/shares", response_model=ShareOut)
async def create_share(
    project_id: int,
    req: ShareCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """为整本书或单章创建公开只读链接(每次创建一个新 token)。"""
    if req.scope not in ("book", "chapter"):
        raise HTTPException(status_code=400, detail="scope 只能是 book 或 chapter")
    if req.scope == "chapter" and req.chapter_number is None:
        raise HTTPException(status_code=400, detail="单章分享必须指定 chapter_number")
    _get_owned_project(db, project_id, user)

    row = ShareLink(
        token=secrets.token_urlsafe(24),
        project_id=project_id, user_id=user.id,
        scope=req.scope, chapter_number=req.chapter_number if req.scope == "chapter" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _share_out(row)


@owner_router.get("/{project_id}/shares", response_model=list[ShareOut])
async def list_shares(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_owned_project(db, project_id, user)
    rows = (
        db.query(ShareLink)
        .filter(ShareLink.project_id == project_id, ShareLink.user_id == user.id)
        .order_by(ShareLink.id.desc())
        .all()
    )
    return [_share_out(r) for r in rows]


@owner_router.delete("/{project_id}/shares/{share_id}")
async def revoke_share(
    project_id: int,
    share_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_share_owned(db, share_id, user)
    if row.project_id != project_id:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    row.revoked = True
    db.commit()
    return {"revoked": True}


# =============== 公开面:免登录只读 ===============

public_router = APIRouter(prefix="/api/public/shares", tags=["shares-public"])


def _public_chapters(db: Session, project_id: int, only_chapter: int | None) -> list[dict]:
    """有正文的章(按章号排序),标题取大纲章名。单章分享只回该章。"""
    rows = (
        db.query(Chapter, Outline.title)
        .outerjoin(Outline, Chapter.outline_id == Outline.id)
        .filter(
            Chapter.project_id == project_id,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    out = []
    for ch, outline_title in rows:
        if only_chapter is not None and ch.chapter_number != only_chapter:
            continue
        out.append({
            "number": ch.chapter_number,
            "title": outline_title or f"第{ch.chapter_number}章",
            "content": ch.final_content or ch.draft_content or "",
        })
    return out


@public_router.get("/{token}", response_model=PublicShareOut)
async def get_public_share(token: str, db: Session = Depends(get_db)):
    """免登录取分享内容:只回书名与正文(只读),浏览计数 +1。"""
    row = (
        db.query(ShareLink)
        .filter(ShareLink.token == token, ShareLink.revoked.is_(False))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="分享不存在或已被撤销")
    project = db.get(Project, row.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="分享不存在或已被撤销")

    chapters = _public_chapters(db, row.project_id, row.chapter_number if row.scope == "chapter" else None)
    if not chapters:
        raise HTTPException(status_code=404, detail="分享内容为空,可能已被作者清理")

    row.view_count += 1
    db.commit()

    return PublicShareOut(
        scope=row.scope,
        book_title=project.title,
        genre=project.genre or "",
        chapters=chapters,
    )
