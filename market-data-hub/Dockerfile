FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
ARG PYPI_MIRROR_URL=
RUN if [ -n "$PYPI_MIRROR_URL" ]; then \
        pip config set global.index-url "$PYPI_MIRROR_URL"; \
    fi \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
