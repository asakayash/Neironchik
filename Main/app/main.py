from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from jose import jwt, JWTError

from database import get_db, init_db
import crud
from services.auth import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM
from services.ai_logic import get_prompt_from_llm, generate_image_in_forge

app = FastAPI(title="LLM + Forge API")


@app.get("/")
async def root():
    return {"message": "API is running. Open /docs"}


@app.on_event("startup")
async def on_startup():
    await init_db()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class UserCreate(BaseModel):
    username: str
    password: str


class TextRequest(BaseModel):
    text: str


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await crud.get_user_by_username(db, username=username)
    if user is None:
        raise HTTPException(status_code=401)
    return user


@app.post("/register")
async def register(user: UserCreate, db: AsyncSession = Depends(get_db)):
    db_user = await crud.get_user_by_username(db, user.username)
    if db_user:
        raise HTTPException(status_code=400, detail="User exists")

    hashed_pw = get_password_hash(user.password)
    return await crud.create_user(db, user.username, hashed_pw)


@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await crud.get_user_by_username(db, form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/generate")
async def generate(request: TextRequest, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    llm_result = await get_prompt_from_llm(request.text)
    if llm_result.get("error"):
        raise HTTPException(
            status_code=llm_result.get("status_code", 500),
            detail=llm_result["error"],
        )

    prompt = llm_result["prompt"]
    negative_prompt = llm_result["negative_prompt"]
    forge_settings = {
        "steps": llm_result["steps"],
        "cfg_scale": llm_result["cfg_scale"],
        "sampler_name": llm_result["sampler_name"],
    }

    image_path, forge_error = await generate_image_in_forge(prompt, negative_prompt, forge_settings)
    if not image_path:
        raise HTTPException(
            status_code=(forge_error or {}).get("status_code", 500),
            detail=(forge_error or {}).get("error", "Forge failed"),
        )

    await crud.create_history_record(db, current_user.id, request.text, prompt, image_path)

    return {
        "original": request.text,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "image_path": image_path,
    }


@app.get("/history")
async def get_history(current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    history = await crud.get_user_history(db, current_user.id)
    return history
