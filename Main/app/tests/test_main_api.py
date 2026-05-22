from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import main
from models import User


@pytest.fixture
def mock_user():
    return User(id=1, username="testuser", hashed_password="hashed")


@pytest.fixture
def client(mock_user):
    async def mock_get_current_user():
        return mock_user

    async def mock_get_db():
        yield AsyncMock(spec=AsyncSession)

    main.app.dependency_overrides[main.get_current_user] = mock_get_current_user
    main.app.dependency_overrides[main.get_db] = mock_get_db

    with patch("main.init_db", new=AsyncMock()):
        with TestClient(main.app) as test_client:
            yield test_client

    main.app.dependency_overrides.clear()


class TestGenerationEndpoint:
    def test_successful_generation(self, client):
        mock_llm_response = {
            "prompt": "beautiful anime girl, sunset",
            "negative_prompt": "blurry",
            "steps": 28,
            "cfg_scale": 7.5,
            "sampler_name": "Euler a",
            "width": 832,
            "height": 1216,
        }

        with patch(
            "main.get_prompt_from_llm", new_callable=AsyncMock
        ) as mock_llm, patch(
            "main.generate_image_in_forge", new_callable=AsyncMock
        ) as mock_forge, patch(
            "main.crud.create_history_record", new_callable=AsyncMock
        ) as mock_history:
            mock_llm.return_value = mock_llm_response
            mock_forge.return_value = ("images/test.png", None)

            response = client.post(
                "/generate",
                json={"text": "Create anime girl"},
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["prompt"] == "beautiful anime girl, sunset"
            assert data["image_path"] == "images/test.png"
            assert data["width"] == 832
            assert data["height"] == 1216
            mock_history.assert_called_once()

    def test_llm_error(self, client):
        with patch("main.get_prompt_from_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "error": {
                    "stage": "llm",
                    "message": "LLM service failed",
                },
                "status_code": 502,
            }

            response = client.post(
                "/generate",
                json={"text": "Test"},
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 502
            assert response.json()["detail"]["stage"] == "llm"
            assert response.json()["detail"]["message"] == "LLM service failed"

    def test_forge_error(self, client):
        mock_llm_response = {
            "prompt": "test prompt",
            "negative_prompt": "bad",
            "steps": 20,
            "cfg_scale": 7,
            "sampler_name": "Euler",
        }

        with patch(
            "main.get_prompt_from_llm", new_callable=AsyncMock
        ) as mock_llm, patch(
            "main.generate_image_in_forge", new_callable=AsyncMock
        ) as mock_forge:
            mock_llm.return_value = mock_llm_response
            mock_forge.return_value = (
                "",
                {
                    "error": {
                        "stage": "forge",
                        "message": "Forge failed",
                    },
                    "status_code": 503,
                },
            )

            response = client.post(
                "/generate",
                json={"text": "Test"},
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 503
            assert response.json()["detail"]["stage"] == "forge"
            assert response.json()["detail"]["message"] == "Forge failed"
