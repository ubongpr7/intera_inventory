FROM python:3.13
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN mkdir /app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y gcc libpq-dev graphviz libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY uv.lock /app/
COPY .python-version /app/

RUN uv sync --locked
ENV PATH="/app/.venv/bin:$PATH"

COPY . /app/

# Expose port
EXPOSE 7002


CMD ["sh", "-c", "uv run manage.py migrate && uv run manage.py runserver 0.0.0.0:7002"]