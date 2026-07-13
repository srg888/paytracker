# Деплой PayTracker на VPS

## 1. Скопировать проект на сервер

С локальной машины (там, где распакован `paytracker_mvp.zip`):

```bash
rsync -avz --exclude 'uploads' --exclude '__pycache__' \
    ./paytracker/ root@YOUR_VPS_IP:/opt/paytracker/
```

Или через `scp -r paytracker root@YOUR_VPS_IP:/opt/`, если rsync не установлен.

## 2. Настроить сервер (один раз)

Скопировать и запустить `setup_vps.sh` на сервере (ставит Docker + Docker Compose plugin,
открывает 22/80/443 в ufw):

```bash
scp setup_vps.sh root@YOUR_VPS_IP:/root/
ssh root@YOUR_VPS_IP "bash /root/setup_vps.sh"
```

## 3. Секреты

На сервере:

```bash
cd /opt/paytracker
cp .env.example .env
sed -i "s/замени-на-случайную-строку/$(openssl rand -hex 32)/" .env
sed -i "s/замени-на-другую-случайную-строку/$(openssl rand -hex 32)/" .env
cat .env   # проверить, что оба секрета реально заполнились разными значениями
```

## 4. Запуск

```bash
cd /opt/paytracker
docker compose up -d --build
docker compose logs -f web   # проверить, что миграции и сидинг прошли без ошибок
```

Порт 8000 в `docker-compose.yml` привязан к `127.0.0.1` — то есть снаружи сервера
без реверс-прокси приложение недоступно. Это осознанно: платёжные данные не должны
торчать в интернет без TLS.

## 5. nginx как реверс-прокси + HTTPS

Если nginx ещё не стоит:

```bash
apt-get install -y nginx certbot python3-certbot-nginx
```

Конфиг `/etc/nginx/sites-available/paytracker`:

```nginx
server {
    listen 80;
    server_name paytracker.your-domain.example;

    client_max_body_size 550M;  # лимит на все файлы заявки — 500 Мб + запас

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/paytracker /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d paytracker.your-domain.example
```

Certbot сам допишет `listen 443 ssl` и настроит редирект с 80 на 443.

## 6. Проверка

Открыть `https://paytracker.your-domain.example/login` — должна открыться страница
выбора демо-пользователя.

## 7. Дальнейшие обновления кода

```bash
# на локальной машине
rsync -avz --exclude 'uploads' --exclude '__pycache__' ./paytracker/ root@YOUR_VPS_IP:/opt/paytracker/
# на сервере
cd /opt/paytracker && docker compose up -d --build
```

`uploads_data` и `db_data` — именованные Docker-volumes, они не пересоздаются при
`up --build`, данные и файлы заявок сохраняются между обновлениями.

## Важно до реальной эксплуатации

- Замени демо-пользователей и упрощённый логин (без пароля) на настоящую
  аутентификацию — см. README.md, раздел "Что НЕ реализовано".
- Настрой резервное копирование `db_data` (например, `pg_dump` по cron в отдельное
  хранилище) — это финансовые данные казначейства.
- Ограничь доступ к серверу по IP/VPN, если это внутренний инструмент холдинга —
  не обязательно светить его наружу вообще, можно поднять только во внутренней сети.
