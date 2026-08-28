# app/engines/media/__init__.py
# -*- coding: utf-8 -*-
"""三条出片线(漫剧 / 宣传片 / 情绪短片)共用的确定性件。

放这里的东西必须满足两条:①不含任何一条线的业务口径,②纯确定性(不调 LLM)。
现有成员:`segments` 切段与时间码,`anchors` 画风锚/负面词兜底,
`audio` 音频分轨口径(视频模型出环境音,人声与 BGM 后期整片铺),
`subtitles` SRT 时间码与累计时间轴,`directions` 画风方向目录,
`text` LLM 脏值收敛与带 BOM 的 CSV,`video` 视频生成共用件
(运镜词表/视频负面词/时长与分辨率口径,漫剧与情绪短片出片共用)。

依赖方向是单向的:三条线都可以往这里看,这里**不许**反过来 import 任何一条线
(`tests/test_engine_conventions.py` 会挡住;宣传片/短片从漫剧转引也一样挡)。
"""
