FROM python:3.11-slim

WORKDIR /app

# 安装依赖（单独一层，代码变更时不重新安装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/

# 不复制 .env / chroma_db / *.db
# API Key 通过火山引擎容器环境变量注入

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
