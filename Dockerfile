# 多阶段构建:前端 build → 后端运行时(FastAPI 托管前端产物)
FROM node:22-slim AS frontend
# 部署时 --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) 烤入当前提交号,
# 前端据此与后端 /api/version 比对,发现旧缓存就提示刷新(更新提醒功能)。
ARG GIT_COMMIT=dev
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit --registry=https://registry.npmmirror.com
COPY frontend/ ./
ENV VITE_APP_COMMIT=${GIT_COMMIT}
RUN npm run build

FROM python:3.12-slim
ARG GIT_COMMIT=dev
WORKDIR /srv
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
COPY backend/ ./backend/
COPY --from=frontend /fe/dist ./frontend/dist
# 更新日志:/api/version 读取最新一条展示给用户(parents[3] 即 /srv)
COPY CHANGELOG.md ./
ENV APP_COMMIT=${GIT_COMMIT}
# 数据目录(SQLite/Chroma 落这里,compose 用 volume 持久化)
RUN mkdir -p /srv/data
WORKDIR /srv/backend
EXPOSE 8000
# 默认数据落在工作目录 ./(即 /srv/backend);compose 通过 DATABASE_URL/CHROMA_PERSIST_DIR
# 把数据指到 /srv/data,并用 named volume(jarvis_data)持久化
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
