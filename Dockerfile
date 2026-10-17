FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY requirements-server.txt requirements.txt ./
COPY skills/convert-pdf-to-markdown/requirements.txt skills/convert-pdf-to-markdown/requirements.txt
RUN pip install --no-cache-dir -r requirements-server.txt

COPY pdf2md_core.py pdf2md_core.py
COPY pdf2md_server pdf2md_server
COPY skills/convert-pdf-to-markdown/scripts skills/convert-pdf-to-markdown/scripts

EXPOSE 8000
CMD ["uvicorn", "pdf2md_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
