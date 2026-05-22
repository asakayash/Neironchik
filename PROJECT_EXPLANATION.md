# Пояснение проекта по этапам

Этот файл объясняет проект простыми словами: что где лежит, зачем нужно и как части связаны между собой.

Проект делает API-сервис на Python. Пользователь регистрируется, получает токен, отправляет текстовую идею, сервис просит LLM превратить эту идею в prompt для Stable Diffusion, потом отправляет prompt в Forge API, получает картинку и сохраняет историю в базу данных.

## 1. Общая структура проекта

Главные места, которые нужно знать:

```text
Neironchik/
├── Main/
│   └── app/
│       ├── main.py
│       ├── database.py
│       ├── models.py
│       ├── crud.py
│       └── services/
│           ├── auth.py
│           └── ai_logic.py
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 1c8689bcc63c_initial_migration.py
├── alembic.ini
├── requirements.txt
├── README.md
├── database.db
└── images/
```

Что это значит простыми словами:

- `Main/app/main.py` - главный файл приложения. Тут описаны API-адреса: регистрация, логин, генерация картинки, история.
- `Main/app/database.py` - подключение к базе данных.
- `Main/app/models.py` - описание таблиц базы данных.
- `Main/app/crud.py` - функции для работы с базой: создать, найти, изменить, удалить.
- `Main/app/services/auth.py` - пароли и JWT-токены.
- `Main/app/services/ai_logic.py` - запросы к LLM и Forge API.
- `alembic/` - папка миграций базы данных.
- `requirements.txt` - список Python-библиотек.
- `database.db` - файл SQLite-базы.
- `images/` - папка, куда сохраняются сгенерированные картинки.

## 2. Создание базы данных

База данных нужна, чтобы приложение помнило пользователей и историю генераций.

В этом проекте используется SQLite. Это простая база данных в одном файле:

```text
database.db
```

### Где настраивается база

Файл:

```text
Main/app/database.py
```

Главная строка:

```python
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./database.db")
```

Что она делает:

- сначала пытается взять адрес базы из переменной окружения `DATABASE_URL`;
- если переменной нет, использует файл `database.db`;
- `sqlite+aiosqlite` означает: используется SQLite в асинхронном режиме.

### Что такое engine

В файле `Main/app/database.py` есть:

```python
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
```

`engine` - это объект подключения к базе данных.

Очень просто:

- база данных - это склад;
- `engine` - это дверь на склад;
- через эту дверь приложение читает и записывает данные.

### Что такое сессия

В файле `Main/app/database.py` есть:

```python
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
```

Сессия - это один разговор приложения с базой данных.

Например:

1. открыть сессию;
2. найти пользователя;
3. записать историю;
4. сохранить изменения;
5. закрыть сессию.

### Где FastAPI получает сессию базы

В файле `Main/app/database.py`:

```python
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

Эта функция выдает сессию базы данных для API-методов.

В `Main/app/main.py` она используется так:

```python
db: AsyncSession = Depends(get_db)
```

То есть FastAPI сам подставляет подключение к базе в нужную функцию.

### Где описаны таблицы

Файл:

```text
Main/app/models.py
```

Тут есть две таблицы:

```python
class User(Base):
    __tablename__ = "users"
```

и:

```python
class ChatHistory(Base):
    __tablename__ = "chat_history"
```

## 3. Таблица пользователей

Файл:

```text
Main/app/models.py
```

Модель:

```python
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
```

Это означает, что в базе есть таблица `users`.

В ней есть поля:

- `id` - номер пользователя. База сама выдает каждому пользователю свой номер.
- `username` - имя пользователя. Оно должно быть уникальным.
- `hashed_password` - пароль, но не обычный текст, а зашифрованный хэш.

Почему пароль хранится как хэш:

- нельзя хранить пароль как `123456`;
- если базу украдут, обычные пароли сразу увидят;
- хэш сложнее использовать как настоящий пароль.

## 4. Таблица истории генераций

Файл:

```text
Main/app/models.py
```

Модель:

```python
class ChatHistory(Base):
    __tablename__ = "chat_history"
```

Поля:

- `id` - номер записи истории.
- `user_id` - какому пользователю принадлежит запись.
- `original_request` - что пользователь изначально написал.
- `llm_prompt` - какой prompt вернула LLM.
- `image_path` - путь к сохраненной картинке.
- `created_at` - когда запись создана.

Пример:

```text
Пользователь написал:
"нарисуй красивый лес ночью"

LLM сделала prompt:
"night forest, moonlight, high quality, detailed..."

Forge сгенерировал картинку:
"images/forge_a2c5536f.png"

Все это записалось в chat_history.
```

### Связь пользователя и истории

В `User` есть:

```python
history = relationship(
    "ChatHistory",
    back_populates="owner",
    cascade="all, delete-orphan",
    passive_deletes=True,
)
```

В `ChatHistory` есть:

```python
owner = relationship("User", back_populates="history")
```

Это связь:

```text
один пользователь -> много записей истории
```

То есть один пользователь может сгенерировать много картинок.

## 5. Настройка Alembic для миграций

Alembic нужен, чтобы менять структуру базы данных нормальным способом.

Простой пример:

- сегодня в таблице `users` есть `username` и `password`;
- завтра ты хочешь добавить `email`;
- руками ломать базу неудобно;
- Alembic создает миграцию, которая аккуратно добавляет поле.

### Главные файлы Alembic

```text
alembic.ini
alembic/env.py
alembic/versions/1c8689bcc63c_initial_migration.py
```

### alembic.ini

Файл:

```text
alembic.ini
```

Тут есть строка:

```ini
script_location = alembic
```

Она говорит Alembic:

```text
миграции лежат в папке alembic
```

Также есть:

```ini
sqlalchemy.url = sqlite+aiosqlite:///./database.db
```

Это адрес базы данных.

Но в проекте этот адрес дополнительно переопределяется через `Main/app/database.py`.

### alembic/env.py

Файл:

```text
alembic/env.py
```

Важная строка:

```python
from Main.app.models import Base
```

Она говорит Alembic:

```text
посмотри модели таблиц в Main/app/models.py
```

Еще важная строка:

```python
target_metadata = Base.metadata
```

`Base.metadata` - это описание всех таблиц, которые объявлены через SQLAlchemy.

Простыми словами:

- `models.py` говорит, какие таблицы должны быть;
- Alembic смотрит на `Base.metadata`;
- потом Alembic понимает, какую миграцию надо сделать.

Еще есть:

```python
config.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
```

Это значит:

```text
Alembic берет адрес базы из database.py, чтобы не писать разные адреса в разных местах.
```

### Файл миграции

Файл:

```text
alembic/versions/1c8689bcc63c_initial_migration.py
```

В нем есть:

```python
def upgrade() -> None:
```

`upgrade` - что сделать при применении миграции.

Внутри создаются таблицы:

```python
op.create_table('users', ...)
op.create_table('chat_history', ...)
```

Еще есть:

```python
def downgrade() -> None:
```

`downgrade` - как откатить миграцию назад.

Там таблицы удаляются:

```python
op.drop_table('chat_history')
op.drop_table('users')
```

### Как обычно работают с Alembic

Команды обычно такие:

```bash
alembic revision --autogenerate -m "some message"
```

Создает новую миграцию.

```bash
alembic upgrade head
```

Применяет миграции к базе.

```bash
alembic downgrade -1
```

Откатывает одну миграцию назад.

## 6. Создание базовых CRUD

CRUD - это четыре базовых действия с данными:

```text
C - Create - создать
R - Read   - прочитать
U - Update - обновить
D - Delete - удалить
```

В проекте CRUD лежит здесь:

```text
Main/app/crud.py
```

Этот файл не отвечает за HTTP-запросы.

Он отвечает только за работу с базой данных.

## 7. CRUD для пользователей

Файл:

```text
Main/app/crud.py
```

### Найти пользователя по id

```python
async def get_user_by_id(db: AsyncSession, user_id: int):
    return await db.get(User, user_id)
```

Что делает:

- получает сессию базы `db`;
- получает `user_id`;
- ищет пользователя с таким `id`;
- возвращает пользователя или `None`.

### Найти пользователя по username

```python
async def get_user_by_username(db: AsyncSession, username: str):
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalars().first()
```

Что делает:

- ищет пользователя по имени;
- используется при регистрации и логине.

### Получить всех пользователей

```python
async def get_users(db: AsyncSession):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()
```

Что делает:

- получает список всех пользователей;
- сортирует по `id`.

### Создать пользователя

```python
async def create_user(db: AsyncSession, username: str, hashed_password: str):
    db_user = User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user
```

Пошагово:

1. Создается Python-объект `User`.
2. `db.add(db_user)` кладет его в очередь на запись.
3. `db.commit()` сохраняет в базу.
4. `db.refresh(db_user)` обновляет объект данными из базы, например новым `id`.
5. Функция возвращает созданного пользователя.

### Обновить пользователя

```python
async def update_user(...)
```

Что делает:

- находит пользователя;
- если пользователя нет, возвращает `None`;
- если передан новый `username`, меняет его;
- если передан новый `hashed_password`, меняет пароль;
- сохраняет изменения.

### Удалить пользователя

```python
async def delete_user(db: AsyncSession, user_id: int):
```

Что делает:

- находит пользователя;
- если пользователь есть, удаляет его;
- сохраняет изменения.

## 8. CRUD для истории генераций

Файл:

```text
Main/app/crud.py
```

### Найти запись истории по id

```python
async def get_history_by_id(db: AsyncSession, history_id: int):
    return await db.get(ChatHistory, history_id)
```

Ищет одну запись истории.

### Получить всю историю

```python
async def get_all_history(db: AsyncSession):
    result = await db.execute(select(ChatHistory).order_by(ChatHistory.created_at.desc()))
    return result.scalars().all()
```

Возвращает все записи истории, сначала самые новые.

### Получить историю конкретного пользователя

```python
async def get_user_history(db: AsyncSession, user_id: int):
```

Используется в API-методе:

```text
GET /history
```

То есть пользователь видит свою историю генераций.

### Создать запись истории

```python
async def create_history_record(db: AsyncSession, user_id: int, original: str, prompt: str, image: str):
```

Что записывает:

- id пользователя;
- исходный текст;
- prompt от LLM;
- путь к картинке.

Эта функция вызывается после успешной генерации картинки.

## 9. Написание основного сервиса на Python

Основной сервис лежит здесь:

```text
Main/app/main.py
```

Это FastAPI-приложение.

Главная строка:

```python
app = FastAPI(title="LLM + Forge API")
```

Она создает приложение.

Когда запускается сервер, FastAPI смотрит на `app` и понимает, какие API-адреса существуют.

## 10. Запуск при старте приложения

Файл:

```text
Main/app/main.py
```

Код:

```python
@app.on_event("startup")
async def on_startup():
    await init_db()
```

Что это значит:

- когда приложение запускается;
- вызывается функция `init_db`;
- она создает таблицы, если их еще нет.

Функция `init_db` находится в:

```text
Main/app/database.py
```

```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

Простыми словами:

```text
если таблиц нет, создай их
```

Важная мысль:

- для учебного проекта это удобно;
- в нормальном большом проекте чаще используют Alembic, а не автоматическое `create_all`.

## 11. Регистрация пользователя

Файл:

```text
Main/app/main.py
```

API:

```python
@app.post("/register")
async def register(user: UserCreate, db: AsyncSession = Depends(get_db)):
```

Пользователь отправляет:

```json
{
  "username": "test",
  "password": "12345"
}
```

Пошагово:

1. API получает username и password.
2. Проверяет, существует ли пользователь:

```python
db_user = await crud.get_user_by_username(db, user.username)
```

3. Если пользователь уже есть, возвращает ошибку:

```python
raise HTTPException(status_code=400, detail="User exists")
```

4. Если пользователя нет, пароль хэшируется:

```python
hashed_pw = get_password_hash(user.password)
```

5. Пользователь сохраняется в базу:

```python
return await crud.create_user(db, user.username, hashed_pw)
```

## 12. Авторизация и токен

Файл:

```text
Main/app/main.py
```

API:

```python
@app.post("/token")
async def login(...)
```

Пользователь отправляет логин и пароль.

Сервис:

1. ищет пользователя по имени;
2. проверяет пароль;
3. если все правильно, создает JWT-токен;
4. возвращает токен пользователю.

Код:

```python
access_token = create_access_token(data={"sub": user.username})
```

Функция `create_access_token` находится здесь:

```text
Main/app/services/auth.py
```

## 13. Сервис авторизации

Файл:

```text
Main/app/services/auth.py
```

Тут три основные задачи:

1. хэшировать пароль;
2. проверять пароль;
3. создавать JWT-токен.

### Хэширование пароля

```python
def get_password_hash(password):
    return pwd_context.hash(password)
```

Пример:

```text
было: 12345
стало: pbkdf2_sha256$...
```

### Проверка пароля

```python
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)
```

Она проверяет:

```text
обычный пароль пользователя совпадает с хэшем из базы или нет
```

### Создание токена

```python
def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=30)):
```

Токен нужен, чтобы пользователь не отправлял пароль каждый раз.

Обычная схема:

1. пользователь логинится;
2. получает токен;
3. потом отправляет токен в защищенные API;
4. сервер понимает, кто это.

## 14. Проверка текущего пользователя

Файл:

```text
Main/app/main.py
```

Функция:

```python
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
```

Что делает:

1. берет токен из запроса;
2. расшифровывает его;
3. достает username;
4. ищет пользователя в базе;
5. если все нормально, возвращает пользователя.

Эта функция используется тут:

```python
current_user=Depends(get_current_user)
```

Например в:

```python
@app.post("/generate")
```

Это значит:

```text
генерировать картинки может только авторизованный пользователь
```

## 15. Создание сервиса для запросов по API LLM

Файл:

```text
Main/app/services/ai_logic.py
```

Тут две главные функции:

```python
async def get_prompt_from_llm(user_text: str) -> dict:
```

и:

```python
async def generate_image_in_forge(...)
```

Первая общается с LLM.

Вторая общается с Forge API.

## 16. Настройки LLM и Forge API

Файл:

```text
Main/app/services/ai_logic.py
```

Код:

```python
LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:11434/api/generate")
FORGE_API_URL = os.getenv("FORGE_API_URL", "http://127.0.0.1:7860/sdapi/v1/txt2img")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:1.5b")
IMAGES_DIR = os.getenv("IMAGES_DIR", "images")
```

Что это значит:

- `LLM_API_URL` - куда отправлять запрос к LLM, сейчас это Ollama.
- `FORGE_API_URL` - куда отправлять запрос для генерации картинки.
- `LLM_MODEL` - какую LLM-модель использовать.
- `IMAGES_DIR` - куда сохранять картинки.

Значения можно поменять через переменные окружения.

Они также описаны в:

```text
README.md
```

## 17. Как работает запрос к LLM

Файл:

```text
Main/app/services/ai_logic.py
```

Функция:

```python
async def get_prompt_from_llm(user_text: str) -> dict:
```

Пользователь пишет простую идею:

```text
красивая девушка в киберпанк городе
```

Сервис просит LLM вернуть JSON:

```json
{
  "prompt": "cyberpunk city, neon lights, high quality...",
  "negative_prompt": "low quality, blurry...",
  "steps": 25,
  "cfg_scale": 7,
  "sampler": "Euler a"
}
```

Для этого создается `payload`:

```python
payload = {
    "model": LLM_MODEL,
    "prompt": f"{system_instruction}. User idea: {user_text}",
    "stream": False,
    "format": "json",
    "keep_alive": 0,
}
```

Что внутри:

- `model` - модель Ollama;
- `prompt` - инструкция плюс текст пользователя;
- `stream: False` - ответ нужен целиком, не по кускам;
- `format: "json"` - просим ответ в JSON;
- `keep_alive: 0` - не держать модель загруженной после ответа.

Запрос отправляется через `httpx`:

```python
resp = await client.post(LLM_API_URL, json=payload)
```

Потом ответ разбирается:

```python
raw_response = resp.json().get("response", "").strip()
parsed = json.loads(raw_response)
```

И функция возвращает удобный словарь:

```python
return {
    "prompt": parsed.get("prompt", raw_response).strip(),
    "negative_prompt": parsed.get(...).strip(),
    "steps": parsed.get("steps", 20),
    "cfg_scale": parsed.get("cfg_scale", 7),
    "sampler_name": parsed.get("sampler", "Euler a"),
}
```

## 18. Как работает запрос к Forge API

Файл:

```text
Main/app/services/ai_logic.py
```

Функция:

```python
async def generate_image_in_forge(...)
```

Она получает:

- `prompt`;
- `negative_prompt`;
- настройки генерации.

Создается словарь:

```python
default_settings = {
    "prompt": prompt,
    "negative_prompt": negative_prompt,
    "steps": 20,
    "cfg_scale": 7,
    "width": 1024,
    "height": 768,
    "sampler_name": "Euler a",
    "scheduler": "Karras",
}
```

Это настройки картинки.

Потом они отправляются в Forge:

```python
resp = await client.post(FORGE_API_URL, json=default_settings)
```

Forge возвращает картинку в формате base64.

Base64 - это когда бинарная картинка превращена в длинную текстовую строку.

Дальше код делает обратное:

```python
f.write(base64.b64decode(images[0]))
```

И сохраняет файл:

```python
filename = os.path.join(IMAGES_DIR, f"forge_{uuid.uuid4().hex[:8]}.png")
```

Пример результата:

```text
images/forge_a2c5536f.png
```

## 19. Полный путь запроса генерации

Файл:

```text
Main/app/main.py
```

API:

```python
@app.post("/generate")
async def generate(...)
```

Пользователь отправляет:

```json
{
  "text": "нарисуй замок на горе ночью"
}
```

Дальше происходит цепочка:

```text
1. FastAPI получает текст пользователя.
2. get_current_user проверяет токен.
3. get_prompt_from_llm отправляет текст в LLM.
4. LLM возвращает prompt, negative_prompt и настройки.
5. generate_image_in_forge отправляет prompt в Forge API.
6. Forge возвращает картинку.
7. Картинка сохраняется в папку images.
8. create_history_record сохраняет историю в базу.
9. API возвращает пользователю результат.
```

Ключевой код:

```python
llm_result = await get_prompt_from_llm(request.text)
```

Потом:

```python
image_path, forge_error = await generate_image_in_forge(prompt, negative_prompt, forge_settings)
```

Потом:

```python
await crud.create_history_record(db, current_user.id, request.text, prompt, image_path)
```

## 20. Получение истории

Файл:

```text
Main/app/main.py
```

API:

```python
@app.get("/history")
async def get_history(...)
```

Что делает:

- проверяет токен пользователя;
- берет `current_user.id`;
- ищет историю только этого пользователя;
- возвращает список записей.

Код:

```python
history = await crud.get_user_history(db, current_user.id)
return history
```

CRUD-функция находится в:

```text
Main/app/crud.py
```

## 21. Создание окружения

Окружение нужно, чтобы зависимости проекта не смешивались с другими Python-проектами на компьютере.

В проекте уже есть папка:

```text
venv/
```

Это виртуальное окружение.

### Список библиотек

Файл:

```text
requirements.txt
```

Там указаны зависимости:

```text
fastapi
uvicorn
sqlalchemy
asyncpg
alembic
passlib
python-jose
python-multipart
httpx
pytest
pytest-asyncio
black
ruff
pre-commit
```

Что за что отвечает:

- `fastapi` - создание API.
- `uvicorn` - сервер, который запускает FastAPI.
- `sqlalchemy` - работа с базой данных через Python-объекты.
- `alembic` - миграции базы данных.
- `passlib` - хэширование паролей.
- `python-jose` - JWT-токены.
- `python-multipart` - нужно для формы логина OAuth2.
- `httpx` - HTTP-запросы к LLM и Forge API.
- `pytest` и `pytest-asyncio` - тесты.
- `black` - автоформатирование кода.
- `ruff` - проверка стиля и ошибок.
- `pre-commit` - проверки перед коммитом.

### Как создать окружение с нуля

Если окружения нет:

```bash
python -m venv venv
```

Активировать на Windows PowerShell:

```powershell
venv\Scripts\Activate.ps1
```

Установить зависимости:

```bash
pip install -r requirements.txt
```

## 22. Как запустить приложение

Обычно запуск такой:

```bash
uvicorn Main.app.main:app --reload
```

Но в этом проекте импорты написаны так:

```python
from database import get_db, init_db
from models import Base
```

Из-за этого запуск может требовать, чтобы рабочая папка была `Main/app`, либо чтобы Python видел эту папку в `PYTHONPATH`.

Практичный вариант запуска из папки `Main/app`:

```powershell
cd Main\app
uvicorn main:app --reload
```

После запуска документация API будет тут:

```text
http://127.0.0.1:8000/docs
```

## 23. Как API выглядит для пользователя

### Проверка, что API живой

```text
GET /
```

Ответ:

```json
{
  "message": "API is running. Open /docs"
}
```

### Регистрация

```text
POST /register
```

Тело:

```json
{
  "username": "test",
  "password": "12345"
}
```

### Логин

```text
POST /token
```

Возвращает:

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

### Генерация

```text
POST /generate
```

Нужен токен.

Тело:

```json
{
  "text": "нарисуй красивый ночной лес"
}
```

Ответ:

```json
{
  "original": "нарисуй красивый ночной лес",
  "prompt": "...",
  "negative_prompt": "...",
  "image_path": "images/forge_xxxxxxxx.png"
}
```

### История

```text
GET /history
```

Нужен токен.

Возвращает историю генераций текущего пользователя.

## 24. Как все связано между собой

Самая простая схема:

```text
main.py
  |
  | принимает HTTP-запросы
  v
crud.py
  |
  | читает и пишет данные
  v
database.py + models.py
  |
  | описывают и подключают базу
  v
database.db
```

Для генерации:

```text
main.py
  |
  | вызывает
  v
services/ai_logic.py
  |
  | запрос к LLM
  v
Ollama API
  |
  | prompt
  v
Forge API
  |
  | картинка
  v
images/
```

Для авторизации:

```text
main.py
  |
  | вызывает
  v
services/auth.py
  |
  | хэш пароля и JWT
  v
users в базе данных
```

## 25. Коротко: кто за что отвечает

```text
Main/app/main.py
```

Главный вход в приложение. Тут API-роуты.

```text
Main/app/database.py
```

Подключение к базе и создание сессий.

```text
Main/app/models.py
```

Описание таблиц `users` и `chat_history`.

```text
Main/app/crud.py
```

Функции для работы с базой.

```text
Main/app/services/auth.py
```

Пароли, хэши, JWT-токены.

```text
Main/app/services/ai_logic.py
```

Запросы к LLM и Forge API, сохранение картинки.

```text
alembic/env.py
```

Настройка Alembic, чтобы он видел модели проекта.

```text
alembic/versions/*.py
```

Файлы миграций. Они создают или меняют таблицы.

```text
requirements.txt
```

Список библиотек проекта.

## 26. Очень короткое объяснение всей идеи

Если совсем просто:

```text
Пользователь пишет идею.
Сервис проверяет, что пользователь вошел.
LLM превращает идею в красивый prompt.
Forge превращает prompt в картинку.
Сервис сохраняет картинку и историю.
Пользователь получает путь к картинке.
```

## 27. Актуально про ai_logic простыми словами

Ниже именно текущее, практическое объяснение того, как сейчас работает файл `Main/app/services/ai_logic.py`.

### Что вообще делает `ai_logic.py`

Этот файл отвечает за две вещи:

1. поговорить с Ollama и получить prompt;
2. поговорить с Forge и получить картинку.

То есть это "мост" между текстом пользователя и готовым изображением.

### Какие в нем две главные функции

В файле есть две основные функции:

```python
async def get_prompt_from_llm(user_text: str) -> dict:
```

и

```python
async def generate_image_in_forge(...)
```

Первая делает prompt.

Вторая делает картинку.

### Как работает `get_prompt_from_llm`

Когда пользователь отправляет текст, например:

```text
view of high-rise buildings in a cyberpunk city in the rain with neon lights in realism
```

функция `get_prompt_from_llm` делает вот что:

1. берет настройки из переменных окружения;
2. решает, в какой endpoint идти у Ollama;
3. собирает текст запроса к модели;
4. отправляет запрос;
5. пытается вытащить из ответа нормальный prompt;
6. если ответ плохой, делает повторную попытку;
7. если ответ все равно плохой, использует запасной путь.

### Что значит "собирает текст запроса"

Функция не отправляет в Ollama просто голый текст пользователя.

Она добавляет префикс-инструкцию:

```text
Convert this description into Illustrious SDXL tags and add negative prompt
```

И только потом подставляет сам текст пользователя.

Идея тут простая:

- модель должна понять, что от нее ждут не рассказ;
- а список тегов для генерации.

### Почему там есть `attempt 1/2`, `attempt 2/2`

Потому что LLM иногда отвечает плохо.

Например:

- пишет слишком много мусора;
- начинает повторять `artstation`;
- добавляет художников, которых никто не просил;
- возвращает не теги, а странный текст.

Поэтому код делает так:

1. сначала отправляет обычный запрос;
2. если ответ плохой, отправляет второй, уже более жесткий.

Это не "память диалога", а просто две попытки получить нормальный результат.

### Как код понимает, что ответ плохой

Он проверяет ответ несколькими простыми способами:

- похож ли текст на список тегов;
- не выглядит ли он как проза;
- не состоит ли он в основном из повторяющегося стайл-шума;
- можно ли вообще разобрать ответ как полезный prompt.

Если ответ выглядит сомнительно, код не хочет слепо слать его в Forge.

### Что такое запасной путь

Если Ollama все равно отвечает плохо, код не обязан сразу падать.

Он может собрать простой fallback prompt из текста пользователя.

Простая идея такая:

- взять сам текст пользователя;
- почистить его;
- превратить в более-менее нормальный список частей;
- отдать это в Forge.

Это не магия и не идеальный prompt.

Но это лучше, чем отправить в Forge мусор из серии:

```text
trending on artstation, greg rutkowski, alphonse mucha, 8k, 8k, 8k...
```

### Что функция возвращает после Ollama

Она возвращает не просто одну строку, а словарь с готовыми настройками:

```python
{
    "prompt": "...",
    "negative_prompt": "...",
    "steps": 28,
    "cfg_scale": 7.0,
    "sampler_name": "DPM++ 2M",
    "scheduler": "Karras",
    "width": 1024,
    "height": 1024,
}
```

То есть после `get_prompt_from_llm` приложение уже знает:

- какой prompt использовать;
- какой negative prompt использовать;
- с какими параметрами идти в Forge;
- какой размер картинки выбрать.

### Откуда берется размер картинки

Внутри есть функция:

```python
determine_resolution(prompt_text, user_text)
```

Она смотрит на слова в запросе.

Если текст больше похож на:

- портрет;
- персонажа в полный рост;
- вертикальную сцену,

то выбирается вертикальное разрешение.

Если текст больше похож на:

- пейзаж;
- фон;
- город;
- wide shot,

то выбирается горизонтальное разрешение.

Если ничего не понятно, берется квадрат:

```text
1024x1024
```

### Как работает `generate_image_in_forge`

Когда prompt уже готов, вторая функция делает вот что:

1. проверяет, что prompt вообще не пустой;
2. собирает настройки для Forge;
3. отправляет HTTP-запрос в Forge API;
4. ждет base64-картинку;
5. декодирует ее;
6. сохраняет PNG на диск;
7. возвращает путь к файлу и доп. информацию.

### Что именно уходит в Forge

Forge получает словарь примерно такого вида:

```python
{
    "prompt": "...",
    "negative_prompt": "...",
    "steps": 28,
    "cfg_scale": 7.0,
    "width": 1024,
    "height": 1024,
    "sampler_name": "DPM++ 2M",
    "scheduler": "Karras",
}
```

То есть Forge уже не думает, как составить prompt.

Он получает готовую инструкцию на генерацию.

### Что происходит после ответа Forge

Forge возвращает картинку в base64.

Код делает обратное преобразование:

```python
base64 -> bytes -> png file
```

И сохраняет файл в папку `images`.

Имя файла генерируется автоматически, чтобы картинки не перетирали друг друга.

Пример:

```text
images/forge_ab12cd34.png
```

### Почему в `ai_logic.py` так много проверок ошибок

Потому что у нас здесь сразу два внешних сервиса:

- Ollama;
- Forge.

Оба могут:

- не ответить;
- ответить слишком долго;
- вернуть плохой HTTP-код;
- вернуть странный текст;
- вернуть пустую картинку.

Поэтому код старается не просто "упасть с traceback", а вернуть понятную структуру ошибки:

```python
{
    "error": {
        "stage": "...",
        "message": "..."
    },
    "status_code": ...
}
```

Это помогает `main.py` аккуратно превратить внутреннюю ошибку в нормальный HTTP-ответ.

### Очень коротко вся цепочка работы `ai_logic`

Если совсем просто:

```text
Пользователь пишет текст
-> ai_logic отправляет его в Ollama
-> Ollama пытается сделать prompt
-> ai_logic чистит и проверяет ответ
-> если ответ плохой, пробует еще раз или берет fallback
-> ai_logic отправляет готовый prompt в Forge
-> Forge возвращает картинку
-> ai_logic сохраняет PNG
-> main.py возвращает результат пользователю
```

### Зачем это все разделено в отдельный файл

Потому что `main.py` не должен знать все детали:

- как именно формируется prompt;
- как устроены retries;
- как выбрать размер;
- как разобрать ответ Ollama;
- как сохранить base64-картинку.

`main.py` просто говорит:

```python
сделай prompt
сделай картинку
```

А вся тяжелая логика живет в `ai_logic.py`.
