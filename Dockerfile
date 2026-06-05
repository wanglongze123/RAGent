FROM python:3.11-slim

WORKDIR /app

# 系统依赖（sentence-transformers 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# 依赖先装，利用 Docker 层缓存
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY server/ .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
