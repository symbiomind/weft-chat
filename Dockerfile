# weft-chat — the service and the client in one image.
#
# Everything the service needs to run is baked in, so the published image stands alone:
# docker-compose.example.yml mounts only config.yml, data/ and logs/. The source build
# (docker-compose.example-build.yml) also bind-mounts web/, tests/ and the tooling, so
# client and test edits are live without a rebuild.

FROM python:3.12-slim

WORKDIR /app

# Dependencies first so the layer caches across app-code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and migrations.
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Operator tooling, run with docker exec (e.g. python3 -m scripts.add_user).
COPY scripts/ scripts/

# The static web app. Also bind-mounted in dev (so edits are live), but copied in so
# the image stands alone without the mount.
COPY web/ web/

EXPOSE 5010

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.server"]
