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

RUN usermod --home /tmp nobody
USER nobody

ENV PYTHONPATH=/usr/src/app
EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "run:app"]
