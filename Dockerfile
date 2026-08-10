FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Torch em versao CPU: a imagem cai de ~2.5GB para ~800MB.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential && apt-get autoremove -y

# poppler-utils: dependencia de runtime do pdf2image (fallback visual do
# importador, ve src/extracao.py). Nao pode ser purgado como o
# build-essential -- e usado em tempo de execucao, nao so no build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

COPY src/ ./src/
COPY data/ ./data/

# usuario nao-root
RUN useradd --create-home --shell /bin/bash nestbot \
 && mkdir -p /app/.cache/huggingface \
 && chown -R nestbot:nestbot /app
USER nestbot

CMD ["python", "-m", "src.bot"]
