FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy DATA_DIR=/app/data PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home monitor && mkdir /app/data && chown monitor:monitor /app/data
COPY main.py ./
COPY src ./src
USER monitor
VOLUME ["/app/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "main.py", "health"]
CMD ["python", "main.py", "serve", "--host", "0.0.0.0"]
