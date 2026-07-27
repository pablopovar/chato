FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY nerdo_api ./nerdo_api
COPY nerdo_mail ./nerdo_mail

RUN python -m compileall -q app nerdo_api nerdo_mail \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/users \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 3400 3401

CMD ["uvicorn", "nerdo_api.main:app", "--host", "0.0.0.0", "--port", "3400"]
