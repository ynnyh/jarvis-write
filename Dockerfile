# 多阶段构建:前端 build → 后端运行时(FastAPI 托管前端产物)
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /fe/dist ./frontend/dist
# 数据目录(SQLite/Chroma 落这里,compose 用 volume 持久化)
RUN mkdir -p /srv/data
WORKDIR /srv/backend
EXPOSE 8000
# 数据(SQLite/Chroma)落在 /srv/backend,用 volume 持久化
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
