#!/bin/bash
# Запускать на VPS от root (или через sudo): bash setup_vps.sh
set -e

echo "=== Установка Docker и Docker Compose plugin ==="
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
apt-get update -qq
apt-get install -y -qq docker-compose-plugin

echo "=== Настройка файрвола (ufw) ==="
if command -v ufw &> /dev/null; then
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
fi

echo "=== Каталог проекта ==="
mkdir -p /opt/paytracker
echo "Каталог /opt/paytracker создан. Теперь скопируй туда проект (см. deploy_instructions.md) и запусти:"
echo "  cd /opt/paytracker && docker compose up -d --build"
