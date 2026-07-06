FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY agents ./agents
COPY broker ./broker
COPY common ./common
COPY config ./config
COPY data_sources ./data_sources
COPY orchestrator ./orchestrator
COPY storage ./storage

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["python", "-m", "orchestrator.main"]
CMD ["--once", "--mode", "paper"]
