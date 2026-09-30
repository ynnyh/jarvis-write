# backend/scripts/loadcheck.py
# -*- coding: utf-8 -*-
"""并发压测基线(docs/25 §6.1 第 7 项):多用户部署下的服务端地基体检。

**这个脚本为什么存在**
多用户部署(server 模式)下,异步端点里跑同步 SQLAlchemy、长任务跨 LLM 调用
持有写事务,会同时打出两种症状:① 全站卡顿(只读接口被写事务拖住,P99 陡增);
② `sqlite3.OperationalError: database is locked`。这两种症状靠肉眼和 pytest
都看不出来——它们只在"并发 + 真库"下才现形,而现在**没有任何一个可复现的数字**
钉住现状。于是 docs/25 §6 的每一项服务端改动都只能说"感觉变好了",回退与否
全凭主观。本脚本就是那把尺子:自己拉起后端、打固定形态的压、留下 JSON 基线,
让后续改动可以拿"改动前的数字"逐条比。

**怎么用**
    python backend/scripts/loadcheck.py --base http://127.0.0.1:8000 \
        --concurrency 200 --rounds 5
    # 落 JSON(默认就落,路径会打印):
    python backend/scripts/loadcheck.py --out backend/evals_out/loadcheck-20260928-120000.json

脚本**自动起停后端**:用 subprocess 拉 uvicorn(APP_MODE=server / APP_ENV=dev /
随机 JWT_SECRET / 关限流 / DATABASE_URL 指向 evals_out 下的临时库 / JARVIS_DATA_DIR
指向临时目录),等 `/openapi.json` 返回 200 才开压,结束时关掉子进程。
**绝不碰仓库里的 backend/jarvis_write.db**——库路径由本脚本显式指定。

**读哪几条验收线**(docs/25 §6.1,口径逐条对应脚本结尾的结论行)
    ① 只读接口 P99 不再随写事务抖动 → A 段(纯读) vs C 段(读+写)的只读子集 P99
       用 A2 段(写完之后、纯读)做对照,避开"数据集变大"这个混淆因素;
    ② database is locked 计数 = 0 → 同时统计响应体命中与服务端日志命中两处;
    ③ jobs 无孤儿 running → 结尾读 /api/jobs?all=true 与
       /api/projects/{id}/running-jobs;
    ④ 任一项无改善 → 回退改动 —— 这条由后续改动拿本基线对比后判定,本脚本只出基线。

**覆盖范围(诚实声明)**
本脚本只打**不依赖 LLM** 的接口,因此 docs/25 基线里的"20 并发 × 生成任务"
那一半**没有**被覆盖:生成链路需要真实 provider key,压测机不该花真钱也不该
依赖外网。要补这一档有两条路:① 本机起 `scripts/mock_llm.py`,往压测库里插一条
指向它的 provider 配置(UI 会被 SSRF 防线拒,只能插库),再把生成端点接进只读/写集;
② 真部署的低峰窗口手工补测。本脚本不在这里假装覆盖它。

**输出里还有两行不是 docs/25 原文、但必须一起看的**
    ⓪ 可用性:四段合计的失败数——P99 好看但 60% 请求超时的"通过"没有意义;
    补充证据(服务端日志):Traceback / QueuePool 打满 / connection timed out /
    OperationalError 的计数——未捕获异常在 uvicorn 里只回 500,响应体看不到原因,
    locked 与连接池打满都只能靠扫日志确认。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BACKEND_DIR / "evals_out"

# Windows 控制台默认 GBK,强制 UTF-8 避免中文表格乱码(同 app/main.py 的做法)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


# 只读候选路径的偏好序(靠前的更"读得动库",最能暴露写事务干扰)。
# 真实路径仍然以 /openapi.json 为准,这里只决定**探测顺序**,猜错的会被剔除。
#
# 为什么不把 /api/projects(书列表)排前面:它要全量序列化当前用户的每一本书,
# 耗时随项目数线性涨。B 段一直在建项目,只读集若含它,A/C 两段比的就是
# 「数据集有多大」而不是「写事务干扰了多少」——验收线①会被这个混淆因素吃掉。
# 要固定成本、可复现,只读集就得挑**成本与写入量无关**的读接口
# (dashboard 自身只取最近 12 本,jobs/usage/auth-me 是常数级)。
# 想把 /api/projects 也纳入,显式 --include-paths /api/projects。
READ_PREFERENCE = (
    "/api/projects/dashboard",   # 跨书聚合(最重的只读;内部只取最近 12 本)
    "/api/jobs",                 # 任务中心(内存 + llm_usage group by)
    "/api/usage",                # token 汇总聚合
    "/api/auth/me",              # 鉴权 + 用户读
    "/api/health",               # 公开、零库
    "/api/settings/providers",   # 配置读
    "/api/projects",             # 书列表:成本随项目数增长,放最后
)
# 名字里带这些词的 GET 端点多半会触发模型(贵且慢),直接不作为只读候选
_READ_BLOCK_WORDS = ("ping-llm", "preview", "suggest", "analyze", "analyse", "reanalyze")


# ---------------------------------------------------------------- 数据结构


@dataclass
class Sample:
    """一次请求的计时与结局。"""

    tag: str            # read / write
    method: str
    path: str
    ms: float
    status: int | None
    err: str | None     # 归类后的错误标签;成功为 None


@dataclass
class Stat:
    """一段压测的统计结果(延迟单位 ms,保留 1 位)。"""

    name: str = ""
    total: int = 0
    ok: int = 0
    failed: int = 0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    max_ms: float = 0.0
    elapsed_s: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)
    locked: int = 0     # 本段里命中 database is locked 的响应数


def _pct(sorted_vals: list[float], p: float) -> float:
    """分位数(取秩法,不插值):样本量小的时候比插值更保守。"""
    if not sorted_vals:
        return 0.0
    idx = max(0, math.ceil(p / 100.0 * len(sorted_vals)) - 1)
    return round(sorted_vals[idx], 1)


def _classify(status: int | None, body: str, exc: BaseException | None) -> str | None:
    """把一次失败归成一个标签;成功返回 None。

    直方图按「HTTP 状态码 + 关键词」两类归并:locked 类关键词单列,因为它是
    docs/25 §6.1 的验收线②,必须能一眼从直方图里挑出来。
    """
    if status is not None and 200 <= status < 400:
        return None
    low = (body or "").lower()
    if "database is locked" in low or "operationalerror" in low:
        label = "sqlite3.OperationalError: database is locked"
    elif exc is not None:
        name = type(exc).__name__
        label = f"客户端异常:{name}"
        if "timeout" in name.lower():
            label += "(超时)"
    elif status is not None:
        label = f"HTTP {status}"
        # 5xx 时把响应体里的关键词也带出来(后端有时把原因写进 detail)
        snippet = (body or "").strip().replace("\n", " ")[:60]
        if snippet:
            label += f" {snippet}"
    else:
        label = "未知错误"
    return label


def summarize(name: str, samples: list[Sample], elapsed_s: float) -> Stat:
    st = Stat(name=name, total=len(samples), elapsed_s=round(elapsed_s, 2))
    lat: list[float] = []
    errs: Counter[str] = Counter()
    for s in samples:
        lat.append(s.ms)
        if s.err is None:
            st.ok += 1
        else:
            st.failed += 1
            errs[s.err] += 1
            if "locked" in s.err:
                st.locked += 1
    lat.sort()
    st.p50 = _pct(lat, 50)
    st.p95 = _pct(lat, 95)
    st.p99 = _pct(lat, 99)
    st.max_ms = round(lat[-1], 1) if lat else 0.0
    st.errors = dict(errs.most_common())
    return st


# ---------------------------------------------------------------- 后端起停


class Backend:
    """自己拉起的后端进程:环境全部显式钉死,绝不复用仓库里的库。"""

    def __init__(self, host: str, port: int, ts: str, keep_db: bool,
                 extra_env: dict[str, str] | None = None) -> None:
        self.host = host
        self.port = port
        self.keep_db = keep_db
        # --env 透传给子进程的额外变量(对照实验用,例如把连接池调回修前容量)
        self.extra_env = extra_env or {}
        self.db_path = OUT_DIR / f"loadcheck-{ts}.db"
        self.data_dir = OUT_DIR / f"loadcheck-{ts}-data"
        self.log_path = OUT_DIR / f"loadcheck-{ts}-server.log"
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_fh = None

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        # 抹掉继承来的模型 key:压测机不该(也不需要)真调 LLM,更不该花钱
        for k in [k for k in env if k.endswith("_API_KEY")]:
            env.pop(k, None)
        env.update(
            {
                "APP_MODE": "server",          # 多用户 JWT 模式,和线上部署一致
                "APP_ENV": "dev",              # dev 才放行弱密钥(这里仍给随机强密钥)
                "JWT_SECRET": secrets.token_urlsafe(48),
                "RATE_LIMIT_ENABLED": "false",  # 压测要的是后端不是限流器
                "INVITE_CODE": "loadcheck",     # 注册接口需要(否则注册 403)
                "DATABASE_URL": f"sqlite:///{self.db_path.as_posix()}",
                "JARVIS_DATA_DIR": str(self.data_dir),
                "PYTHONUNBUFFERED": "1",        # 日志实时落盘,收尾时能统计关键词
            }
        )
        env.update(self.extra_env)   # --env 最后覆盖,优先级最高
        return env

    def start(self, startup_timeout: float) -> float:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self._free_port_check()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--app-dir", str(BACKEND_DIR),
            "--host", self.host, "--port", str(self.port),
            "--log-level", "warning",
        ]
        # cwd 指到临时数据目录:万一有代码按相对路径写文件,也不会落进仓库
        self._log_fh = self.log_path.open("wb")
        self.proc = subprocess.Popen(  # noqa: S603 — 命令是本文件自己拼的常量
            cmd, cwd=str(self.data_dir), env=self.env(),
            stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        return self._wait_ready(startup_timeout)

    def _wait_ready(self, timeout: float) -> float:
        """轮询 /openapi.json:它 200 才代表 lifespan(建表+迁移)全跑完。"""
        url = f"http://{self.host}:{self.port}/openapi.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise SystemExit(
                    f"后端进程已退出(rc={self.proc.returncode}),日志尾部:\n{self.tail_log()}"
                )
            try:
                r = httpx.get(url, timeout=3.0)
                if r.status_code == 200:
                    return round(time.time() - (deadline - timeout), 2)
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise SystemExit(f"后端 {timeout:.0f}s 内没就绪,日志尾部:\n{self.tail_log()}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def _free_port_check(self) -> None:
        """端口被占就直接报错退出:压测地址必须可复现,不能偷偷换端口。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((self.host, self.port))
            except OSError as exc:
                raise SystemExit(
                    f"端口 {self.host}:{self.port} 被占用({exc});换一个 --base 再跑。"
                )

    def tail_log(self, lines: int = 30) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(无日志)"
        return "\n".join(text.splitlines()[-lines:])

    def log_locked_hits(self) -> dict[str, int]:
        """服务端日志的关键词计数——压测事故的权威来源。

        未捕获异常在 uvicorn 里只回 500 Internal Server Error,响应体里既看不到
        "database is locked" 也看不到连接池超时(FastAPI 不回传异常细节),
        所以必须扫日志。四个关键词对应四种不同的病:
          - database is locked / OperationalError : SQLite 写锁冲突(验收线②)
          - QueuePool / connection timed out      : 连接池被打满(async 端点里
            同步 SQL 阻塞事件循环,拿不到连接的请求只能干等 30 s)
          - Traceback                              : 崩了几个请求(上面两条的载体)
        """
        counts = {
            "database is locked": 0,
            "OperationalError": 0,
            "QueuePool": 0,
            "connection timed out": 0,
            "Traceback": 0,
        }
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return counts
        for key in counts:
            counts[key] = text.count(key)
        return counts

    def cleanup_db(self) -> None:
        """--keep-db 缺省保留:压测库的体积不大,留着才好复查 SQL 层的现场。"""
        if self.keep_db:
            print(f"[库] 保留(便于排查):{self.db_path}")
        else:
            for p in (self.db_path, Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm")):
                p.unlink(missing_ok=True)
            print(f"[库] 已删除:{self.db_path}")


# ---------------------------------------------------------------- 路由发现


async def discover_reads(
    client: httpx.AsyncClient, token: str | None, limit: int, extra: tuple[str, ...]
) -> list[dict[str, Any]]:
    """从 /openapi.json 里挑出真实的只读路由,并**实测**是否需要鉴权。

    两步都不可省:
    - 路径与参数取自 openapi(不凭空猜路由);
    - 「是否需要鉴权 / 是否真能跑」用一次真实请求判定(不带 token 打一发看是不是
      401,带 token 再打一发看是不是 2xx)——openapi 里没有声明依赖,猜不得。
    """
    spec = (await client.get("/openapi.json")).json()
    wanted: list[str] = []
    for path, item in spec.get("paths", {}).items():
        get = item.get("get")
        if not get or not path.startswith("/api/"):
            continue
        if "{" in path:          # 带路径参数的要在 openapi 里展开,这里不展开
            continue
        required_q = [
            p for p in (get.get("parameters") or [])
            if p.get("in") == "query" and p.get("required")
        ]
        if required_q:
            continue
        low = path.lower()
        if any(w in low for w in _READ_BLOCK_WORDS):
            continue
        wanted.append(path)
    # 偏好序在前,其余按字典序兜底
    pref = [p for p in READ_PREFERENCE if p in wanted]
    rest = sorted(p for p in wanted if p not in pref)
    # --include-paths 指定的排在偏好序之后,给"想看某个已知读接口"留个口子
    ordered = pref + rest + [p for p in extra if p in wanted]

    picked: list[dict[str, Any]] = []
    for path in ordered:
        if len(picked) >= limit:
            break
        auth_hdr = {"Authorization": f"Bearer {token}"} if token else {}
        t0 = time.perf_counter()
        try:
            anon = await client.get(path, timeout=10.0)
        except httpx.HTTPError:
            continue
        if anon.status_code == 401:
            auth_required = True
        elif anon.status_code < 400:
            auth_required = False  # 公开接口
        else:
            continue              # 不带 token 都报错,不是可用的只读探针
        try:
            ok = await client.get(path, headers=auth_hdr, timeout=10.0)
        except httpx.HTTPError:
            continue
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if ok.status_code >= 400:
            continue
        if ms > 2000:
            # 慢到不像只读(多半在等模型):压测里留着会把 P99 变成网络抖动而非库抖动
            print(f"  [发现] 剔除 {path}({ms} ms,疑似触模型/重活)")
            continue
        picked.append({"path": path, "auth_required": auth_required, "probe_ms": ms})
        print(f"  [发现] {path}  鉴权={'是' if auth_required else '否'}  探测 {ms} ms")
    return picked


async def get_token(client: httpx.AsyncClient, ts: str, invite: str) -> str:
    """拿 token:先注册(server 模式要鉴权),失败再回落 admin 登录。"""
    username = f"loadcheck_{ts}"
    password = "loadcheck123"
    r = await client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": invite},
        timeout=30.0,
    )
    if r.status_code == 200:
        print(f"[鉴权] 注册 {username} 成功")
        return r.json()["token"]
    print(f"[鉴权] 注册失败({r.status_code} {r.text[:80]}),回落 admin 登录")
    r = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin12345"},
        timeout=30.0,
    )
    if r.status_code != 200:
        raise SystemExit(f"取 token 失败:{r.status_code} {r.text[:200]}")
    print("[鉴权] admin 登录成功")
    return r.json()["token"]


# ---------------------------------------------------------------- 压测驱动


@dataclass
class Req:
    method: str
    path: str
    tag: str
    body: dict[str, Any] | None = None


async def _fire(
    client: httpx.AsyncClient, hdr: dict[str, str], req: Req, timeout: float
) -> Sample:
    t0 = time.perf_counter()
    try:
        r = await client.request(
            req.method, req.path, headers=hdr, json=req.body, timeout=timeout
        )
        ms = round((time.perf_counter() - t0) * 1000, 1)
        body = r.text[:2000]
        return Sample(req.tag, req.method, req.path, ms, r.status_code,
                      _classify(r.status_code, body, None))
    except httpx.HTTPError as exc:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return Sample(req.tag, req.method, req.path, ms, None,
                      _classify(None, "", exc))


async def run_segment(
    client: httpx.AsyncClient, hdr: dict[str, str],
    reqs: list[Req], concurrency: int, rounds: int, timeout: float,
) -> tuple[list[Sample], float]:
    """concurrency 个 worker,每个连打 rounds 发;worker 之间用轮转摊平端点。

    轮转而不是"每个 worker 死磕一个端点":后者会让某个端点的连接复用率异常,
    测出来的尾延迟混着连接池行为,不是服务端行为。
    """
    samples: list[Sample] = []
    lock = asyncio.Lock()

    async def worker(idx: int) -> None:
        mine: list[Sample] = []
        for i in range(rounds):
            mine.append(await _fire(client, hdr, reqs[(idx + i) % len(reqs)], timeout))
        async with lock:
            samples.extend(mine)

    t0 = time.perf_counter()
    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    return samples, round(time.perf_counter() - t0, 2)


# ---------------------------------------------------------------- 打印


def print_stat(title: str, st: Stat, extra: str = "") -> None:
    print(f"\n=== {title}{(' ' + extra) if extra else ''} ===")
    print(f"  总请求 {st.total} · 成功 {st.ok} · 失败 {st.failed} · 耗时 {st.elapsed_s}s")
    print(f"  P50 {st.p50} ms · P95 {st.p95} ms · P99 {st.p99} ms · 最大 {st.max_ms} ms")
    if st.errors:
        print("  错误类型直方图:")
        for label, n in st.errors.items():
            print(f"    {n:>6}  {label}")
    else:
        print("  错误类型直方图:空(全成功)")


def _stat_dict(st: Stat) -> dict[str, Any]:
    return asdict(st)


def git_fingerprint() -> dict[str, Any]:
    """记下这份基线是在哪份代码上跑的。

    基线的价值全在"可复现":别人拿着数字回退/对比时,必须知道当时的工作树长什么样。
    取不到 git 不影响压测(纯脚本环境也能跑),返回空即可。
    """
    info: dict[str, Any] = {"head": None, "app_dirty_files": None}
    try:
        repo = BACKEND_DIR.parent
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        info["head"] = head.stdout.strip() or None
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--", "backend/app", "backend/alembic"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        lines = [ln for ln in dirty.stdout.splitlines() if ln.strip()]
        info["app_dirty_files"] = len(lines)
        info["app_dirty_sample"] = lines[:10]
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="并发压测基线(docs/25 §6.1)")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="压测地址(脚本自己在这里起后端)")
    ap.add_argument("--concurrency", type=int, default=200, help="并发 worker 数")
    ap.add_argument("--rounds", type=int, default=5, help="每个 worker 的请求数")
    ap.add_argument("--read-paths", type=int, default=6, help="只读段最多用几个端点")
    ap.add_argument("--include-paths", nargs="*", default=[],
                    help="额外纳入只读集的已知路径(排在自动发现之后),如 --include-paths /api/projects")
    ap.add_argument("--seed-projects", type=int, default=20, help="开压前预置的项目数(固定数据集)")
    ap.add_argument("--read-after-rounds", type=int, default=2,
                    help="A2 段(写后纯读对照)的每 worker 请求数;用来把"
                         "「数据集变大」与「写事务干扰」分开")
    ap.add_argument("--startup-timeout", type=float, default=180.0)
    ap.add_argument("--request-timeout", type=float, default=20.0,
                    help="单请求客户端超时(秒)。刻意小于 app/db/session.py 里 SQLite 的 "
                         "30 s busy_timeout 与连接池 30 s 等待:超时就算服务端卡住的账,"
                         "而不是客户端没耐心")
    ap.add_argument("--p99-ratio", type=float, default=3.0,
                    help="验收线①的阈值:C段只读 P99 / A2段只读 P99 超过此倍数即判不过"
                         "(docs/25 未给具体数字,这是本脚本的默认口径,可调)")
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径(默认 evals_out/loadcheck-<ts>.json)")
    ap.add_argument("--keep-db", action="store_true", default=True, help="保留压测库(默认行为)")
    ap.add_argument("--drop-db", action="store_true", help="跑完删掉压测库")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                    help="额外透传给后端子进程的环境变量(可重复)。做对照实验用,"
                         "如 --env DB_POOL_SIZE=5 --env DB_MAX_OVERFLOW=10")
    args = ap.parse_args()

    if args.concurrency < 1 or args.rounds < 1:
        raise SystemExit("--concurrency / --rounds 必须 >= 1")

    # --env 解析成 dict 后再传给后端;写错格式立刻报错,免得拼出一个
    # 静默不生效的环境变量(对照实验最怕这个:以为改了其实没改)。
    extra_env: dict[str, str] = {}
    for item in args.env:
        if "=" not in item:
            raise SystemExit(f"--env 要写成 KEY=VALUE,收到:{item!r}")
        k, v = item.split("=", 1)
        extra_env[k.strip()] = v.strip()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.out) if args.out else OUT_DIR / f"loadcheck-{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    url = urlparse(args.base)
    host = url.hostname or "127.0.0.1"
    port = url.port or 8000
    backend = Backend(host, port, ts, keep_db=not args.drop_db, extra_env=extra_env)

    print("=" * 72)
    print(f"jarvis-write 并发压测基线 · {ts}")
    print(f"机器 {platform.platform()} · CPU {os.cpu_count()} 核 · "
          f"Python {platform.python_version()} · SQLite {sqlite3.sqlite_version}")
    print(f"参数 并发={args.concurrency} 每 worker 轮数={args.rounds} 地址={args.base}")
    if extra_env:
        print(f"后端附加环境变量 {extra_env}  ← 对照实验条件")
    print("=" * 72)

    result: dict[str, Any] = {
        "ts": ts,
        "args": vars(args),
        "machine": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "git": git_fingerprint(),
    }

    # start() 失败(比如工作树被改坏、端口被抢)时也要把子进程收干净,
    # 否则压测脚本自己退出了,后端还占着端口在跑。
    try:
        boot_s = backend.start(args.startup_timeout)
    except BaseException:
        backend.stop()
        raise
    print(f"[后端] 已就绪(耗时 {boot_s}s)·库 {backend.db_path} ·日志 {backend.log_path}")

    try:
        result["server"] = {
            "base": args.base, "boot_seconds": boot_s,
            "db_path": str(backend.db_path), "log_path": str(backend.log_path),
        }
        async def run() -> dict[str, Any]:
            limits = httpx.Limits(
                max_connections=args.concurrency + 32,
                max_keepalive_connections=args.concurrency + 32,
            )
            async with httpx.AsyncClient(
                base_url=args.base, limits=limits,
                headers={"Accept": "application/json"},
            ) as client:
                hdr: dict[str, str] = {}
                token: str | None = None
                if (await client.get("/api/mode", timeout=10.0)).json().get("is_local"):
                    print("[鉴权] local 免鉴权模式:跳过注册,请求不带 token")
                else:
                    token = await get_token(client, ts, "loadcheck")
                    hdr["Authorization"] = f"Bearer {token}"

                print("[发现] 从 /openapi.json 挑只读路由(实测鉴权要求):")
                reads = await discover_reads(client, token, args.read_paths,
                                             tuple(args.include_paths))
                if not reads:
                    raise SystemExit("没挑出任何可用的只读端点,压测无法进行(见上面的 [发现] 输出)")
                result["read_endpoints"] = reads
                read_reqs = [Req("GET", r["path"], "read") for r in reads]

                # 固定数据集:预置一批项目,让只读接口一开始就有东西可读
                write_body = {
                    "title": f"压测·{ts}",
                    "topic": "压测用占位项目,不调模型",
                    "genre": "悬疑推理",
                    "target_chapters": 30,
                    "target_words_per_chapter": 2000,
                }
                for i in range(args.seed_projects):
                    await client.post("/api/projects", headers=hdr,
                                      json={**write_body, "title": f"压测种子·{i}"},
                                      timeout=args.request_timeout)
                print(f"[数据集] 预置 {args.seed_projects} 个项目")

                async def n_projects() -> int:
                    """当前库里的项目数,只作报告用。

                    读不出来(超时/报错)不能让整轮压测崩掉——它是仪表,不是被测对象。
                    """
                    try:
                        r = await client.get("/api/projects", headers=hdr,
                                             timeout=args.request_timeout)
                        return len(r.json()) if r.status_code == 200 else -1
                    except httpx.HTTPError:
                        return -1

                async def n_projects_id() -> int | None:
                    """随便取一个项目 id(③ 那个项目级接口要 path 参数)。"""
                    try:
                        r = await client.get("/api/projects", headers=hdr,
                                             timeout=args.request_timeout)
                        rows = r.json() if r.status_code == 200 else []
                        return rows[0]["id"] if rows else None
                    except (httpx.HTTPError, KeyError, IndexError, ValueError):
                        return None

                # 预热:每个端点先打一发,把首次调用的惰性 import 成本剔出统计
                await asyncio.gather(
                    *(_fire(client, hdr, r, args.request_timeout) for r in read_reqs)
                )
                await _fire(client, hdr,
                            Req("POST", "/api/projects", "write", write_body),
                            args.request_timeout)

                out: dict[str, Any] = {}

                print(f"\n[A 只读] 并发 {args.concurrency} × {args.rounds} 发"
                      f"(库内项目数 {await n_projects()})")
                s, el = await run_segment(client, hdr, read_reqs,
                                          args.concurrency, args.rounds,
                                          args.request_timeout)
                a = summarize("A 只读", s, el)
                print_stat("A · 只读", a, f"并发={args.concurrency}")
                out["A"] = {"stat": _stat_dict(a),
                            "read": _stat_dict(summarize("A 只读", [x for x in s if x.tag == "read"], el))}

                print(f"\n[B 并发建项目] 并发 {args.concurrency} × {args.rounds} 发"
                      f"(库内项目数 {await n_projects()})")
                s_b, el_b = await run_segment(client, hdr,
                                              [Req("POST", "/api/projects", "write", write_body)],
                                              args.concurrency, args.rounds,
                                              args.request_timeout)
                b = summarize("B 建项目", s_b, el_b)
                print_stat("B · 并发建项目", b, f"并发={args.concurrency}")
                out["B"] = {"stat": _stat_dict(b)}

                print(f"\n[A2 只读对照] 写完之后再打一轮纯读(数据集已变大)"
                      f"(库内项目数 {await n_projects()})")
                s_a2, el_a2 = await run_segment(client, hdr,
                                                read_reqs, args.concurrency,
                                                args.read_after_rounds,
                                                args.request_timeout)
                a2 = summarize("A2 只读", s_a2, el_a2)
                print_stat("A2 · 只读(写后对照)", a2, f"并发={args.concurrency} × {args.read_after_rounds} 发")
                out["A2"] = {"stat": _stat_dict(a2)}

                print(f"\n[C 混合] 只读 + 建项目同时跑(库内项目数 {await n_projects()})")
                mixed = read_reqs + [Req("POST", "/api/projects", "write", write_body)]
                s_c, el_c = await run_segment(client, hdr, mixed,
                                              args.concurrency, args.rounds,
                                              args.request_timeout)
                c = summarize("C 混合", s_c, el_c)
                c_read = summarize("C 混合·只读子集", [x for x in s_c if x.tag == "read"], el_c)
                c_write = summarize("C 混合·写子集", [x for x in s_c if x.tag == "write"], el_c)
                print_stat("C · 混合", c, f"并发={args.concurrency}")
                print_stat("C · 只读子集(与 A2 对比即验收线①)", c_read)
                print_stat("C · 写子集", c_write)
                out["C"] = {"stat": _stat_dict(c),
                            "read": _stat_dict(c_read), "write": _stat_dict(c_write)}

                # ③ 孤儿 running:任务中心 + 项目级两个接口都读,任一有即不过。
                # 读失败只影响 ③ 的可判定性,不能让整轮压测崩掉。
                orphan_running: int | None = None
                orphan_detail = ""
                try:
                    r = await client.get("/api/jobs", headers=hdr,
                                         params={"all": "true"},
                                         timeout=args.request_timeout)
                    if r.status_code == 200:
                        jobs = r.json().get("jobs", [])
                        running = [j for j in jobs if j.get("status") == "running"]
                        orphan_running = len(running)
                        orphan_detail = f"/api/jobs?all=true 共 {len(jobs)} 条,running {len(running)} 条"
                    else:
                        orphan_detail = f"/api/jobs 不可读({r.status_code}),③ 无法判定"
                except httpx.HTTPError as exc:
                    orphan_detail = f"/api/jobs 读取失败({type(exc).__name__}),③ 无法判定"
                pid = await n_projects_id()
                if pid is not None:
                    try:
                        r3 = await client.get(f"/api/projects/{pid}/running-jobs",
                                              headers=hdr,
                                              timeout=args.request_timeout)
                        if r3.status_code == 200:
                            n = len(r3.json().get("jobs", []))
                            orphan_detail += f";项目 {pid} running-jobs {n} 条"
                            orphan_running = n if orphan_running is None else max(orphan_running, n)
                    except httpx.HTTPError:
                        orphan_detail += f";项目 {pid} running-jobs 读取失败"
                print(f"\n[③ 孤儿任务] {orphan_detail}")
                out["orphan_running"] = {"count": orphan_running, "detail": orphan_detail}
                return out

        out = asyncio.run(run())
        result["segments"] = out
        result["log_hits"] = backend.log_locked_hits()
    finally:
        backend.stop()
        print(f"\n[后端] 已停止 · 日志 {backend.log_path}")
        backend.cleanup_db()

    # ---- 结论行(docs/25 §6.1 口径) ----
    a_read = result["segments"]["A"]["read"]
    a2_read = result["segments"]["A2"]["stat"]
    c_read = result["segments"]["C"]["read"]
    locked_resp = sum(
        result["segments"][k]["stat"]["locked"] for k in ("A", "B", "A2", "C")
    )
    locked_log = result["log_hits"]["database is locked"]
    orphan = result["segments"]["orphan_running"]["count"]

    ratio = (c_read["p99"] / a2_read["p99"]) if a2_read["p99"] else float("inf")
    total_all = sum(result["segments"][k]["stat"]["total"] for k in ("A", "B", "A2", "C"))
    failed_all = sum(result["segments"][k]["stat"]["failed"] for k in ("A", "B", "A2", "C"))

    # P99 被客户端超时截顶时,两段的 P99 都等于超时上限,倍数会算出一个漂亮的
    # 假"通过"(服务端早就不动了,比的是同一堵墙)。所以先判"是否被截顶":
    # 只有 P99 真顶到超时上限(≥90%)才作废倍数;顶到了就不通过,并说清原因。
    ceiling = args.request_timeout * 1000
    pinned = [n for n, s in (("A", a_read), ("A2", a2_read), ("C", c_read))
              if s["p99"] >= 0.9 * ceiling]
    v1 = not pinned and ratio <= args.p99_ratio
    why1 = (
        f"{'/'.join(pinned)} 段 P99 顶到 {args.request_timeout:.0f}s 客户端超时上限"
        f"(服务端已无响应),倍数 {ratio:.2f} 不可比"
        if pinned else f"倍数 {ratio:.2f}(阈值 {args.p99_ratio})"
    )

    print("\n" + "=" * 72)
    print("结论(docs/25 §6.1 验收线;本脚本只给基线,④ 由改动后重跑对比判定)")
    print("=" * 72)
    print(f"  机器 {platform.platform()} · CPU {os.cpu_count()} 核 · "
          f"并发 {args.concurrency} × {args.rounds} 发 · 库 {backend.db_path.name}")
    print(f"  ⓪ [本脚本附加·可用性] 四段合计 {total_all} 请求,失败 {failed_all} "
          f"({failed_all / total_all * 100 if total_all else 0:.0f}%)")
    print(f"  ① 只读 P99 稳定性:A(纯读) {a_read['p99']} ms → "
          f"A2(写后纯读对照) {a2_read['p99']} ms → C(混写时只读) {c_read['p99']} ms;"
          f"→ {'通过' if v1 else '不通过'}({why1})")
    print(f"  ② database is locked:响应命中 {locked_resp} 次 · "
          f"服务端日志命中 {locked_log} 次 → "
          f"{'通过' if locked_resp == 0 and locked_log == 0 else '不通过'}")
    print(f"  ③ 孤儿 running 任务:{orphan if orphan is not None else '无法判定'} 个 → "
          f"{'通过' if orphan == 0 else ('无法判定' if orphan is None else '不通过')}")
    print("  ④ 任一项无改善 → 回退改动:待后续改动后重跑本脚本与本基线逐项对比")
    hits = result["log_hits"]
    print(f"  [补充证据·服务端日志] Traceback {hits['Traceback']} · "
          f"QueuePool 打满 {hits['QueuePool']} · connection timed out "
          f"{hits['connection timed out']} · OperationalError {hits['OperationalError']}")
    print("=" * 72)

    result["verdict"] = {
        "0_failed_total": failed_all,
        "0_request_total": total_all,
        "1_read_p99_stable": v1,
        "1_ratio": round(ratio, 2),
        "1_pinned_at_ceiling": pinned,
        "2_locked_zero": locked_resp == 0 and locked_log == 0,
        "3_no_orphan_running": orphan == 0,
    }
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[结果] {out_path}")
    print("[提示] 生成任务那一档(需真 key)本脚本没覆盖:压测机不调 LLM。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
