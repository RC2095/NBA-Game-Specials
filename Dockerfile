FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py database.py foul_pricing.py player_markets.py team_markets.py security.py .

RUN mkdir -p /app/data

ENV NBA_FOUL_DB_PATH=/app/data/nba_foul_api.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Shell form (not exec/JSON form) so ${PORT} actually expands. Most
# hosting platforms (Render, Railway, etc.) inject their own PORT env
# var and expect the app to bind to it; hardcoding 8000 would fail
# their health checks. Falls back to 8000 for plain `docker run` with
# no PORT set.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
