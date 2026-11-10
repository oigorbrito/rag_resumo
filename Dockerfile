# Piloto: roda em qualquer host com Docker (servidor interno, Oracle, Google Cloud, etc.).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOST=0.0.0.0 PORT=8000 RESUMO_DADOS_DIR=/dados PYTHONUNBUFFERED=1
VOLUME ["/dados"]
EXPOSE 8000
CMD ["python", "scripts/app_web.py"]
