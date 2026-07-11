# Kenyan News API Server
# Lightweight: serves the FastAPI, reads SQLite from uploaded DB snapshots.
FROM python:3.13-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project
COPY pyproject.toml .
COPY kenyan_news/ kenyan_news/

# Install dependencies (no playwright — API doesn't crawl)
RUN uv sync --no-dev --group server --frozen

EXPOSE 8090

CMD ["uv", "run", "uvicorn", "kenyan_news.api:app", "--host", "0.0.0.0", "--port", "8090"]
