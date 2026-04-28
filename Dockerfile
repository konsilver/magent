# Multi-stage build for Jingxin-Agent backend

# Stage 1: Builder
FROM python:3.11-slim AS builder
RUN sed -i 's/deb.debian.org/mirrors.huaweicloud.com/g' /etc/apt/sources.list.d/debian.sources
WORKDIR /build

ENV PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=8

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Production Runtime
FROM python:3.11-slim AS production
RUN sed -i 's/deb.debian.org/mirrors.huaweicloud.com/g' /etc/apt/sources.list.d/debian.sources
# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /app

# Install runtime dependencies (including font support and document parsing tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    tzdata \
    fonts-wqy-zenhei \
    fontconfig \
    pandoc \
    libreoffice-writer \
    libreoffice-impress \
    libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Install bundled Chinese fonts without copying backend source code.
COPY src/backend/resources/fonts /tmp/fonts
RUN mkdir -p /usr/share/fonts/truetype/fangzheng && \
    find /tmp/fonts -type f \( -name "*.ttf" -o -name "*.TTF" \) -exec cp {} /usr/share/fonts/truetype/fangzheng/ \; && \
    rm -rf /tmp/fonts && \
    fc-cache -f

# Prepare mount points and writable directories.
RUN mkdir -p /app/src/backend /app/storage /app/logs /app/storage/manual && \
    chown -R appuser:appuser /app/src/backend /app/storage /app/logs /app/storage/manual && \
    rm -rf /home/appuser/.cache/matplotlib

# Set PATH for user-installed packages
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONPATH=/app/src/backend
ENV TZ=Asia/Shanghai

# Set container timezone to Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ >/etc/timezone

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:3001/health || exit 1

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 3001

# Start command
CMD ["bash", "/app/src/backend/scripts/backend_entrypoint.sh"]

# Stage 3a: Pre-build minimax-docx .NET CLI (avoid needing SDK at runtime)
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS docx-builder
COPY src/backend/agent_skills/skills/minimax-docx/scripts/dotnet/ /src/
RUN dotnet publish /src/MiniMaxAIDocx.Cli \
    -c Release -o /out --nologo -v quiet \
    && rm -rf /root/.nuget /root/.dotnet

# Stage 3b: Script Runner Sidecar
FROM python:3.11-slim AS script-runner
RUN sed -i 's/deb.debian.org/mirrors.huaweicloud.com/g' /etc/apt/sources.list.d/debian.sources
RUN useradd -m -u 1001 -s /bin/bash runner

ENV PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/lib/node_modules

# System deps: fonts, curl, Node.js 20, .NET 8.0 runtime, doc tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei fontconfig curl gnupg ca-certificates \
    pandoc zip unzip libreoffice-writer \
    && fc-cache -f \
    # 1. Node.js 20 保持不变
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # 2. 手动配置微软源，不使用那个报错的 .deb 包
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft-prod.list \
    # 3. 更新并安装 .NET
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-runtime-8.0 \
    && rm -rf /var/lib/apt/lists/*

# Node.js global packages (pptx-generator + minimax-pdf)
# Install Chromium into a fixed image path so the read-only runtime container
# can launch it without trying to download browsers on demand.
RUN mkdir -p /ms-playwright \
    && npm install -g pptxgenjs playwright \
    && PLAYWRIGHT_BROWSERS_PATH=/ms-playwright npx playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

# Python deps
COPY requirements-script-runner.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-script-runner.txt \
    && rm /tmp/requirements-script-runner.txt

# Pre-built minimax-docx CLI (from docx-builder stage)
COPY --from=docx-builder /out/ /opt/minimax-docx/

COPY src/backend/script_runner_service/ /app/
WORKDIR /app

RUN mkdir -p /workspace /tmp/scripts && chown -R runner:runner /workspace /tmp/scripts

USER runner
EXPOSE 8900

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8900", "--no-access-log"]
