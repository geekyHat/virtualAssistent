# Immagine API NewRay (A-07, NewRay.md §21.1).
#
# Python 3.12 (compatibile con uv.lock: requires-python >=3.12,<3.13) e uv
# bloccato alla versione di sviluppo. L'ambiente si crea con
# `uv sync --frozen --no-dev`: solo dipendenze runtime, versioni esatte
# come in backend/uv.lock. Nessuna password o segreto in questa immagine.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/newray/backend/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.12.15

WORKDIR /opt/newray
COPY backend/ /opt/newray/backend/
RUN cd /opt/newray/backend && uv sync --frozen --no-dev

COPY deploy/entrypoint.sh /opt/newray/entrypoint.sh
RUN chmod +x /opt/newray/entrypoint.sh

ENTRYPOINT ["/opt/newray/entrypoint.sh"]
