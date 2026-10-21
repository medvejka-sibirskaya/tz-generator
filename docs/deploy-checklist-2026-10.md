# Чек-деплоя TZ Generator (урок 20.10)

Деплой = Redeploy в Dokploy (+ push в GitHub по «заливай»). Урок 20.10:
рестарт контейнера во время живого платежа глотает уведомление ЮMoney
(его только 3 попытки: сразу, +10 мин, +1 час) — деплоить, когда платежей нет.

## До деплоя

- [ ] Проверить журнал на сервере: `/opt/tz-private/payments.log` — нет ли
      платежа прямо сейчас (`waiting-for-confirmation` за последние минуты).
- [ ] Env Dokploy актуален (YOOMONEY_*, ADMIN_TOKEN, LLM_*, TG_*, SECRET_KEY,
      APP_URL) — менять ДО Redeploy.
- [ ] Bind-mounts на месте (Dokploy): `/opt/tz-private/payments.py`,
      `notify.py`, `payments.log` — проверять ПОСЛЕ пересоздания контейнера.

## После деплоя (5 страниц за 2 минуты)

- [ ] `https://tz.medvejka.ru/` → 200
- [ ] `/form/light` → форма открывается
- [ ] `/gost/pay` → страница оплаты (не заглушка), кнопка видна
- [ ] `/gost/access/<любой токен из журнала>` → 302 в `/form/gost`
- [ ] Журнал пишется: новая строка появляется (нажать тест/сделать заход)

## Если что-то не так

- Шаблон/страница 500 → первым делом `md5sum payments.py` в контейнере vs
  локальный; `docker logs <контейнер>` — последняя ошибка в хвосте.
- Запросы «прыгают» — проверить `docker ps --filter status=running` (контейнеры
  должны быть 1, не 5) и `docker service inspect --format
  '{{.Spec.Mode.Replicated.Replicas}}' services-tzgenerator-ofhoco`.
- Транзитный docker cp (шаблон/модуль в живой контейнер) — только как временная
  мера; код обязан сразу же уйти в репозиторий, иначе Redeploy потеряет правку.