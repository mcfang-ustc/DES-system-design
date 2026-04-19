# Backend Dockerfile for DES Formulation System
# Python 3.13 without Java (no OWL reasoning required)

FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    g++ \
    build-essential \
    gfortran \
    pkg-config \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements files
COPY src/tools/corerag/requirements.txt ./requirements/corerag_requirements.txt
COPY src/tools/largerag/requirements.txt ./requirements/largerag_requirements.txt
COPY src/web_backend/requirements.txt ./requirements/backend_requirements.txt

# Install Python dependencies
RUN pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple && pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    -r requirements/corerag_requirements.txt \
    -r requirements/largerag_requirements.txt \
    -r requirements/backend_requirements.txt

# Copy project source code
COPY src/ ./src/
COPY __init__.py ./

# Create necessary directories
RUN mkdir -p /app/data /app/logs \
    /app/src/tools/largerag/data/chroma_db_prod \
    /app/src/tools/largerag/data/prod_cache

# Set environment variables
# Include /app/src so that `import agent` etc. works without extra PYTHONPATH tweaks
ENV PYTHONPATH=/app/src:/app
ENV PROJECT_ROOT=/app/
# Set container timezone to Beijing (Asia/Shanghai) so logs use local time
ENV TZ=Asia/Shanghai

# Expose API port
EXPOSE 8000

# Working directory for backend
WORKDIR /app/src/web_backend

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start backend server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
