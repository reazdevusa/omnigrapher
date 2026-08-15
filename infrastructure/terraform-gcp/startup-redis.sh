#!/bin/bash
set -e
apt-get update -y
apt-get install -y ca-certificates curl
# Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
# Persistent data volume
mkdir -p /data/redis
# Run Redis
docker run -d \
  --name redis \
  --restart unless-stopped \
  -v /data/redis:/data \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --appendonly yes --dir /data
