FROM python:3.11-slim

RUN mkdir /app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y gcc libpq-dev graphviz libpq-dev && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

RUN pip install --upgrade setuptools wheel
RUN pip install tox

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install celery[redis]

COPY . /app/

# Expose port
EXPOSE 8001

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8001"]
