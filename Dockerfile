# 生产镜像
#
# 分层策略：先装依赖再拷代码。依赖文件不变时这一层可复用缓存，
# 镜像重建从「重新装一遍全部依赖」降到秒级 —— 这在频繁提交的流水线里差别很大。

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CRM_DATABASE_URL=sqlite:////data/crm.db \
    CRM_UPLOAD_DIR=/data/uploads

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 以非 root 运行。容器逃逸的代价差别很大，这一步成本极低。
RUN mkdir -p /data/uploads \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /srv/app /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# 先初始化数据库（建表 + 可选种子），再启动服务
CMD ["sh", "-c", "python -m app.bootstrap && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
