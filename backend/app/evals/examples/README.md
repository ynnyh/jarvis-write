# 评测报告范例

本目录存放评测链路的报告范例,供新贡献者了解输出格式,不作为回归基准。

| 文件 | 来历 | 用途 |
|---|---|---|
| `baseline-po_feng_ji.*` | `scripts/evals_fake_baseline.py` 生成,不耗 token | 验证 fixture→run→report 链路,格式示例 |
| `baseline-real-po_feng_ji.*` | 真模型(deepseek-v4-pro)全 10 章实跑,2026-09-04 | 真实质量参照:达标率 1.0、AI 味 4.1、门禁 0 blocker / 5 major / 34 minor |

注意:`baseline-real-*` 跑在夹具修正(commit `daa5e7a`)**之前**,
其中第 1/2 章时间线矛盾对应的那条 preflight 警告已在夹具修正后消失,
后续对比时这一项的差异属于预期,不算回归。

正式的改动对比请用 `python -m app.evals compare <旧 run.json> <新 run.json>`。
