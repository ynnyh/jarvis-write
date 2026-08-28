# app/engines/render/__init__.py
"""出片引擎(轻量档):把「文+图 → 视频」外包给 autodl.art 托管的 ComfyUI 工作流。

分工:client 只会跟平台说话(提交/轮询/下载),service 编排一次出片的全过程
(构造参数→提交→轮询→落盘→回写指针);各线的提交参数由线内构造器给
(漫剧 engines/drama/video.py 的 api_render_payload / 情绪 engines/clips/render_input.py),
render 这里不做任何一条线的业务判断——符合「线间禁互引,共用件下沉 media」的约定。
"""
from .client import RenderError, fetch_bytes, poll, submit
from .service import apply_pointer, start_render

__all__ = ["RenderError", "apply_pointer", "fetch_bytes", "poll", "submit", "start_render"]
