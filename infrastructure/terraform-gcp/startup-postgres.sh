#!/bin/bash
set -e
DB_PASSWORD="${db_password}"
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release software-properties-common
# Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
# Persistent data volume
mkdir -p /data/postgres
# Run pgvector with Docker
docker run -d \
  --name pgvector \
  --restart unless-stopped \
  -e POSTGRES_USER=kb_admin \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_DB=knowledge_base \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v /data/postgres:/var/lib/postgresql/data \
  -p 5432:5432 \
  pgvector/pgvector:pg16
