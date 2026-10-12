# Tapsyrma

Платформа для ретейков формативных и суммативных работ. Сейчас здесь рабочая основа: вход, административный интерфейс и проверка доступности приложения и базы. Предметная логика экзаменов будет добавляться по [архитектуре](ARCHITECTURE.md).

## Локальный запуск

Нужен Docker с работающим Linux Engine. Локальный `.env` уже создан; если его нет, скопируйте `.env.example` в `.env` и замените `DJANGO_SECRET_KEY` и `POSTGRES_PASSWORD` длинными случайными значениями.

```sh
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Откройте `http://localhost/`, войдите созданным пользователем. `http://localhost/healthz/` проверяет также соединение с PostgreSQL.

## Перед запуском на VPS

В `.env` установите `DJANGO_DEBUG=0`, `SITE_ADDRESS=ваш-домен`, `DJANGO_ALLOWED_HOSTS=ваш-домен` и `DJANGO_CSRF_TRUSTED_ORIGINS=https://ваш-домен`. Направьте DNS на VPS и откройте порты 80/443; Caddy получит HTTPS-сертификат. Не публикуйте порт PostgreSQL. Настройте внешние резервные копии базы до работы с реальными результатами.

`.env` и приватные ключи игнорируются Git и Docker. В репозиторий отправляйте только `.env.example` с фиктивными значениями. Перед `git push` проверяйте `git diff --cached --name-only` и `git ls-files`.
