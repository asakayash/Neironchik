from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt

# Секретный ключ для подписи токенов
SECRET_KEY = "super_secret_key"
ALGORITHM = "HS256"

# Настройка алгоритма хэширования
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=30)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
