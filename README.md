# Neironchik

HTTP API для генерации изображений через Stable Diffusion Forge. Сервис принимает текст пользователя, просит локальную LLM собрать Illustrious/SDXL prompt, отправляет его в Forge и сохраняет историю генераций в SQLite.

## Что умеет

- регистрация и логин через JWT
- генерация `prompt` и `negative_prompt` через Ollama
- генерация изображения через Forge
- сохранение истории запросов в SQLite
- просмотр истории пользователя

## Стек

- `FastAPI`
- `SQLAlchemy` + `aiosqlite`
- `Alembic`
- `httpx`
- `Ollama`
- `Stable Diffusion WebUI Forge`
- `pytest` + `pytest-asyncio`

## Локальный запуск

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn main:app --app-dir Main/app --reload
```

После запуска:

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Docker

Базовый запуск:

```powershell
docker compose up --build -d
docker compose logs -f app
```

Что важно помнить:

- `docker-compose.yml` поднимает инфраструктурную заготовку: FastAPI, Ollama и Forge.
- В контейнере Ollama нет вашей кастомной модели по умолчанию, поэтому после старта её нужно подтянуть отдельно, например `docker compose exec llm ollama pull prompter`.
- В Forge-контейнере тоже нет вашего личного checkpoint из коробки. Его нужно будет добавить позже в `forge-workspace` или заменить образ/маунты под свою сборку.
- Для приложения уже проброшены сервисные URL `llm:11434` и `forge:7860`, так что вручную их внутри контейнеров настраивать не нужно.

## Переменные окружения

Основные:

- `DATABASE_URL` — по умолчанию `sqlite+aiosqlite:///./database.db`
- `LLM_API_URL` — по умолчанию `http://127.0.0.1:11434/api/chat`
- `LLM_GENERATE_URL` — опционально; если не задан, для `raw_generate` выводится из `LLM_API_URL`
- `FORGE_API_URL` — по умолчанию `http://127.0.0.1:7860/sdapi/v1/txt2img`
- `LLM_MODEL` — по умолчанию `prompter`
- `IMAGES_DIR` — по умолчанию `images`

Параметры LLM:

- `LLM_REQUEST_MODE` — по умолчанию `raw_generate`; альтернативно `chat_json`
- `LLM_PROMPT_PREFIX` — по умолчанию `Convert this description into Illustrious SDXL tags and add negative prompt`
- `LLM_KEEP_ALIVE` — по умолчанию `0`
- `LLM_TEMPERATURE` — по умолчанию `0`
- `LLM_NUM_PREDICT` — по умолчанию `256`
- `LLM_MAX_RETRIES` — по умолчанию `2`
- `LLM_DEFAULT_STEPS` — по умолчанию `28`
- `LLM_DEFAULT_CFG_SCALE` — по умолчанию `7.0`
- `LLM_DEFAULT_SAMPLER` — по умолчанию `DPM++ 2M`

Таймауты:

- `LLM_CONNECT_TIMEOUT`
- `LLM_WRITE_TIMEOUT`
- `LLM_POOL_TIMEOUT`
- `LLM_READ_TIMEOUT`
- `FORGE_CONNECT_TIMEOUT`
- `FORGE_WRITE_TIMEOUT`
- `FORGE_POOL_TIMEOUT`
- `FORGE_READ_TIMEOUT`

Пример `.env`:

```env
DATABASE_URL=sqlite+aiosqlite:///./database.db
LLM_API_URL=http://127.0.0.1:11434/api/chat
FORGE_API_URL=http://127.0.0.1:7860/sdapi/v1/txt2img
LLM_MODEL=prompter
LLM_REQUEST_MODE=raw_generate
LLM_PROMPT_PREFIX=Convert this description into Illustrious SDXL tags and add negative prompt
LLM_KEEP_ALIVE=0
LLM_TEMPERATURE=0
LLM_NUM_PREDICT=256
LLM_MAX_RETRIES=2
LLM_READ_TIMEOUT=180
FORGE_READ_TIMEOUT=900
IMAGES_DIR=images
```

## Как работает генерация

1. `POST /generate` принимает пользовательский текст.
2. `Main/app/services/ai_logic.py` отправляет текст в Ollama.
3. По умолчанию используется `raw /api/generate`, потому что кастомный `prompter` у вас стабильнее работает в completion-режиме, чем через chat-template.
4. Если модель вернула внятный tag-list, backend дополняет его локальными дефолтами для Forge.
5. Если ответ превратился в прозу или в style spam, backend делает повторную попытку с более жёсткой инструкцией.
6. Если и это не помогло, используется нейтральный fallback из текста пользователя, чтобы не слать мусор в Forge.
7. Forge генерирует PNG, а результат и исходный текст сохраняются в БД.

## Почему `prompter` иногда "уезжает"

Проблема обычно не в памяти диалога, а в том, что модель вместо списка тегов продолжает ассоциации из обучения. Сейчас это сдерживается так:

- каждый запрос отправляется отдельно
- `keep_alive=0` выгружает модель после ответа
- по умолчанию используется `raw_generate`, а не chat-template
- backend не требует от модели каждый раз порождать большой JSON
- при плохом ответе делается повторная попытка
- при совсем неудачном ответе используется fallback вместо случайного `artstation`-шума

Если вашей модели лучше на прогретом состоянии, можно вручную поднять `LLM_KEEP_ALIVE` до `1m` или `5m`. Если она, наоборот, нормально переваривает structured outputs, можно переключиться на `LLM_REQUEST_MODE=chat_json`.

`Modelfile` для текущей схемы не обязателен. Критичнее, чтобы backend отправлял правильную триггер-фразу через `LLM_PROMPT_PREFIX`.

## API

- `GET /` — healthcheck
- `POST /register` — регистрация
- `POST /token` — получение bearer token
- `POST /generate` — генерация изображения
- `GET /history` — история генераций текущего пользователя

## Тесты

```powershell
venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Сейчас покрыты:

- `services/ai_logic.py`
- `crud.py`
- API-роут `/generate`

## Файлы проекта

- `README.md` — быстрый старт и окружение
- `PROJECT_EXPLANATION.md` — подробное объяснение проекта и логики `ai_logic.py`
- `docker-compose.yml` — локальная Docker-заготовка для app + Ollama + Forge

## Git Ignore

В `.gitignore` уже исключены локальные артефакты разработки и генерации, в том числе:

- `images/`
- `data/`
- `forge-workspace/`
- `.env`
- `venv/`

## Контакты

- GitHub repository: `https://github.com/asakayash/Neironchik`
- Issues: `https://github.com/asakayash/Neironchik/issues`
