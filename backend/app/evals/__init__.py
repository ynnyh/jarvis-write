# app/evals/__init__.py
# -*- coding: utf-8 -*-
"""生成质量评测底座:把「prompt 改完感觉好了」变成「同一本黄金样本书上,数字这样变了」。

四个部件,各自独立可用:
- prompt_registry:给全部 prompt 模板算内容指纹,评测结果自动记录「这轮跑的是哪版 prompt」;
- metrics:零 LLM 的确定性指标(AI 味指数、节奏统计、重复、字数偏差、对白占比);
- fixtures:黄金样本书夹具(架构 + 逐章蓝图),能灌进任意库,也能从已有书导出;
- runner / report:真跑 generate_chapter 全管线逐章生成,收集主审四维、门禁、抽取、
  用量等数字,落成 JSON;两次运行可出对比表。

命令行入口见 `python -m app.evals --help`(在 backend/ 下执行)。
"""
