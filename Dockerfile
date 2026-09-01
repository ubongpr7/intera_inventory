FROM python:3.13
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN mkdir /app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    graphviz \
    libpq-dev \
    librdkafka-dev \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libgobject-2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY uv.lock /app/
COPY .python-version /app/

RUN uv sync --locked
RUN uv run python -c "from weasyprint import HTML; HTML(string='<p>WeasyPrint smoke test</p>').write_pdf('/tmp/weasyprint-smoke.pdf')"
ENV PATH="/app/.venv/bin:$PATH"

COPY . /app/

# Expose port
EXPOSE 7002


CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:7002"]
