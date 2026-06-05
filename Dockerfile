FROM python:3.11-slim

WORKDIR /app

# 换国内 apt 源加速（Debian trixie 为 deb822 格式；非中国环境可删除本行）
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources

# 系统依赖（sentence-transformers 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# 换国内 PyPI 源加速（非中国环境可删除本行）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 依赖先装，利用 Docker 层缓存
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY server/ .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
