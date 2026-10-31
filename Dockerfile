FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOME=/tmp

WORKDIR /app

RUN groupadd --system --gid 10001 pdf2md \
    && useradd --system --uid 10001 --gid pdf2md --home-dir /nonexistent \
       --no-create-home --shell /usr/sbin/nologin pdf2md \
    && mkdir -p /data \
    && chown 10001:10001 /data

COPY requirements-server.txt requirements.txt ./
COPY skills/convert-pdf-to-markdown/requirements.txt skills/convert-pdf-to-markdown/requirements.txt
RUN pip install --no-cache-dir -r requirements-server.txt

COPY --chown=10001:10001 pdf2md_core.py pdf2md_core.py
COPY --chown=10001:10001 pdf2md_server pdf2md_server
COPY --chown=10001:10001 skills/convert-pdf-to-markdown/scripts skills/convert-pdf-to-markdown/scripts

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "pdf2md_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
