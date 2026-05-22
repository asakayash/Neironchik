# tests/test_crud.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from crud import create_history_record, get_user_history
from models import ChatHistory


class TestHistoryCRUD:
    """Тесты для операций с историей"""

    @pytest.mark.asyncio
    async def test_create_history_record(self):
        """Тест создания записи в истории"""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await create_history_record(
            mock_db,
            user_id=1,
            original="test original",
            prompt="test prompt",
            image="test.png",
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        assert isinstance(result, ChatHistory)

    @pytest.mark.asyncio
    async def test_get_user_history(self):
        """Тест получения истории пользователя"""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            ChatHistory(id=1, user_id=1, original_request="test1"),
            ChatHistory(id=2, user_id=1, original_request="test2"),
        ]

        mock_db.execute = AsyncMock(return_value=mock_result)

        history = await get_user_history(mock_db, user_id=1)

        assert len(history) == 2
        assert all(h.user_id == 1 for h in history)
