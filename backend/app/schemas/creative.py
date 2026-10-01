"""参考资料与生效创作目标：材料是证据，绝不直接成为故事事实。"""
from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal["structure", "engine", "pacing", "voice", "experience", "detail"]


class Reference(BaseModel):
    name: str = Field(default="", max_length=150)
    description: str = Field(default="", max_length=2000)
    excerpt: str = Field(default="", max_length=12000)
    locator: str = Field(default="", max_length=300)
    # URL 仅是来源标记；未读取就不能伪称已读原作。
    url: str = Field(default="", max_length=1000)
    read_scope: str = Field(default="", max_length=300)


class Observation(BaseModel):
    dimension: Dimension
    instruction: str = Field(min_length=1, max_length=500)
    source_index: int = Field(ge=0, le=4)
    evidence: str = Field(default="", max_length=500)
    basis: Literal["excerpt", "description", "inferred"] = "inferred"


class GoalInput(BaseModel):
    intent: str = Field(default="", max_length=2000)
    form: Literal["serial", "short", "continuous", "sketch", "anthology"] = "serial"
    references: list[Reference] = Field(default_factory=list, max_length=5)
    selected: list[Dimension] = Field(default_factory=lambda: ["structure", "engine", "pacing", "voice", "experience", "detail"], max_length=6)
    observations: list[Observation] = Field(default_factory=list, max_length=30)
    unknowns: list[str] = Field(default_factory=list, max_length=15)
    must: str = Field(default="", max_length=1000)
    avoid: str = Field(default="", max_length=1000)
    enabled: bool = True
    expected_version: int = Field(default=0, ge=0)
    fetch_links: bool = False
