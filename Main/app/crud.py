from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from models import ChatHistory, User


async def get_user_by_id(db: AsyncSession, user_id: int):
    return await db.get(User, user_id)


async def get_user_by_username(db: AsyncSession, username: str):
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalars().first()


async def get_users(db: AsyncSession):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


async def create_user(db: AsyncSession, username: str, hashed_password: str):
    db_user = User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def update_user(
    db: AsyncSession,
    user_id: int,
    *,
    username: str | None = None,
    hashed_password: str | None = None,
):
    db_user = await db.get(User, user_id)
    if db_user is None:
        return None

    if username is not None:
        db_user.username = username
    if hashed_password is not None:
        db_user.hashed_password = hashed_password

    await db.commit()
    await db.refresh(db_user)
    return db_user


async def delete_user(db: AsyncSession, user_id: int):
    db_user = await db.get(User, user_id)
    if db_user is None:
        return None

    await db.delete(db_user)
    await db.commit()
    return db_user


async def get_history_by_id(db: AsyncSession, history_id: int):
    return await db.get(ChatHistory, history_id)


async def get_all_history(db: AsyncSession):
    result = await db.execute(select(ChatHistory).order_by(ChatHistory.created_at.desc()))
    return result.scalars().all()


async def get_user_history(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(ChatHistory).filter(ChatHistory.user_id == user_id).order_by(ChatHistory.created_at.desc())
    )
    return result.scalars().all()


async def create_history_record(db: AsyncSession, user_id: int, original: str, prompt: str, image: str):
    db_record = ChatHistory(user_id=user_id, original_request=original, llm_prompt=prompt, image_path=image)
    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)
    return db_record


async def update_history_record(
    db: AsyncSession,
    history_id: int,
    *,
    original_request: str | None = None,
    llm_prompt: str | None = None,
    image_path: str | None = None,
):
    db_record = await db.get(ChatHistory, history_id)
    if db_record is None:
        return None

    if original_request is not None:
        db_record.original_request = original_request
    if llm_prompt is not None:
        db_record.llm_prompt = llm_prompt
    if image_path is not None:
        db_record.image_path = image_path

    await db.commit()
    await db.refresh(db_record)
    return db_record


async def delete_history_record(db: AsyncSession, history_id: int):
    db_record = await db.get(ChatHistory, history_id)
    if db_record is None:
        return None

    await db.delete(db_record)
    await db.commit()
    return db_record
