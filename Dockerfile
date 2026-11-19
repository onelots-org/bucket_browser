FROM python:3.12.6-slim-bookworm

ARG POETRY_VERSION=1.8.3
ENV POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1

WORKDIR /usr/src/app

COPY poetry.lock pyproject.toml /usr/src/app/

RUN pip install --no-cache-dir poetry==$POETRY_VERSION \
    && poetry install --only main --no-interaction \
    && rm -rf /root/.cache

COPY . /usr/src/app/

RUN usermod --home /tmp nobody && mkdir -p data && chown nobody data
USER nobody

ENV PYTHONPATH=/usr/src/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/static/style.css', timeout=4)"]

CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "--threads", "8", "run:app"]
