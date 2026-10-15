FROM python:3.12-slim

WORKDIR /app

# Install curl
RUN apt-get update && apt-get install -y curl

# Install nodejs
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs

# Install uv
RUN pip install uv

# Configure uv to use Aliyun mirror
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Install dependencies
COPY backend/pyproject.toml backend/README.md ./
RUN uv sync --no-dev

# Copy project files
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/start_worker.sh ./start_worker.sh
COPY builtin_skills/s127-gml/SKILL.md ./builtin_skills/s127-gml/SKILL.md

# Set environment variables
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
