import base64
import json
import os
from unittest.mock import AsyncMock, mock_open, patch

import pytest

from services.ai_logic import (
    _build_prompt_fallback,
    _extract_json_object,
    _extract_prompt_fragments,
    _looks_like_tag_prompt,
    _stringify_prompt_value,
    generate_image_in_forge,
    get_prompt_from_llm,
)


class TestLLMResponseParsing:
    @pytest.mark.asyncio
    async def test_successful_llm_response(self):
        mock_response = {
            "message": {
                "content": json.dumps(
                    {
                        "prompt": "1girl, beautiful, sunset, anime style",
                        "negative_prompt": "blurry, low quality",
                        "steps": 28,
                        "cfg_scale": 7.5,
                        "sampler_name": "DPM++ 2M Karras",
                    }
                )
            }
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            result = await get_prompt_from_llm("Create beautiful anime girl")

            assert result["prompt"] == "1girl, beautiful, sunset, anime style"
            assert result["negative_prompt"] == "blurry, low quality"
            assert result["steps"] == 28
            assert result["cfg_scale"] == 7.5
            assert "error" not in result

    @pytest.mark.asyncio
    async def test_llm_response_missing_prompt_field(self):
        mock_response = {
            "message": {
                "content": json.dumps(
                    {
                        "negative_prompt": "bad quality",
                        "steps": 20,
                        "cfg_scale": 7,
                        "sampler_name": "Euler",
                    }
                )
            }
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            result = await get_prompt_from_llm("Test prompt")

            assert "error" not in result
            assert result["prompt"] == "test prompt"
            assert result["negative_prompt"] == "bad quality"

    @pytest.mark.asyncio
    async def test_llm_response_invalid_json(self):
        mock_response = {"message": {"content": "This is not valid JSON"}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            result = await get_prompt_from_llm("Test")

            assert "error" not in result
            assert result["prompt"] == "test"

    @pytest.mark.asyncio
    async def test_llm_retries_after_invalid_json(self):
        invalid_response = AsyncMock(
            status_code=200,
            json=lambda: {"message": {"content": "This is not valid JSON"}},
            raise_for_status=lambda: None,
        )
        valid_response = AsyncMock(
            status_code=200,
            json=lambda: {
                "message": {
                    "content": json.dumps(
                        {
                            "prompt": "cyberpunk city, neon lights, rain, realism",
                            "negative_prompt": "blurry, low quality",
                            "steps": 28,
                            "cfg_scale": 7.0,
                            "sampler_name": "DPM++ 2M",
                        }
                    )
                }
            },
            raise_for_status=lambda: None,
        )

        with patch("httpx.AsyncClient") as mock_client:
            post_mock = AsyncMock(side_effect=[invalid_response, valid_response])
            mock_client.return_value.__aenter__.return_value.post = post_mock

            result = await get_prompt_from_llm("Cyberpunk city in rain")

            assert result["prompt"] == "cyberpunk city, neon lights, rain, realism"
            assert post_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_llm_style_spam_falls_back_to_user_text(self):
        mock_response = {
            "response": (
                ", artstation, concept art, illustration, digital painting, "
                "by greg rutkowski and alphonse mucha, 8 k resolution, "
                "trending on artstation, trending on artstation"
            )
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            result = await get_prompt_from_llm(
                "view of high-rise buildings in a cyberpunk city in the rain with neon lights in realism"
            )

            assert "error" not in result
            assert "artstation" not in result["prompt"]
            assert "cyberpunk city" in result["prompt"]
            assert "neon lights" in result["prompt"]

    @pytest.mark.asyncio
    async def test_llm_timeout(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=TimeoutError("Connection timeout")
            )

            result = await get_prompt_from_llm("Test")

            assert "error" in result
            assert result["error"]["stage"] == "llm"
            assert result["status_code"] == 504


class TestPromptFallback:
    def test_extract_fragments_from_structured_data(self):
        parsed = {
            "subject": "beautiful girl",
            "style": "anime",
            "quality": "high resolution",
            "mood": "happy",
        }

        fragments = []
        for key, value in parsed.items():
            fragments.extend(_extract_prompt_fragments(key, value))

        assert len(fragments) > 0
        assert any("girl" in fragment for fragment in fragments)

    def test_build_prompt_fallback(self):
        parsed = {
            "character": "sailor moon",
            "scene": "city at night",
            "lighting": "neon",
            "steps": 28,
        }

        result = _build_prompt_fallback(parsed, "original user text")

        assert "sailor moon" in result or "character" in result
        assert "steps" not in result

    def test_stringify_prompt_various_types(self):
        assert _stringify_prompt_value(["cat", "dog", "bird"]) == "cat, dog, bird"
        assert _stringify_prompt_value(42) == "42"
        assert _stringify_prompt_value(None) == ""
        assert _stringify_prompt_value("  hello world  ") == "hello world"

    def test_extract_json_object_from_code_block(self):
        raw = '```json\n{"prompt":"a","negative_prompt":"b","steps":28,"cfg_scale":7,"sampler_name":"Euler"}\n```'

        parsed = _extract_json_object(raw)

        assert parsed["prompt"] == "a"

    def test_detects_prose_instead_of_tag_prompt(self):
        assert _looks_like_tag_prompt("cyberpunk city, neon lights, rain, realism")
        assert not _looks_like_tag_prompt(
            "In a cyberpunk city, four skyscrapers stand in the rain while neon lights flicker."
        )


class TestImageGeneration:
    @pytest.mark.asyncio
    async def test_successful_image_generation(self):
        mock_image_data = base64.b64encode(b"fake_image_data").decode("utf-8")
        mock_response = {
            "images": [mock_image_data],
            "info": json.dumps({"seed": 12345, "parameters": {}}),
        }

        with patch("httpx.AsyncClient") as mock_client, patch(
            "os.makedirs"
        ) as mock_makedirs, patch("builtins.open", mock_open()) as mock_file:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            image_path, info = await generate_image_in_forge(
                "beautiful anime girl",
                "blurry, low quality",
                {"steps": 25, "cfg_scale": 7.5},
            )

            assert image_path.endswith(".png")
            assert image_path.startswith(f"images{os.sep}")
            assert info is not None
            mock_file.assert_called()
            mock_makedirs.assert_called()

    @pytest.mark.asyncio
    async def test_forge_empty_response(self):
        mock_response = {"images": [], "info": "{}"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: mock_response,
                    raise_for_status=lambda: None,
                )
            )

            image_path, error = await generate_image_in_forge("test")

            assert image_path == ""
            assert error is not None
            assert error["error"]["stage"] == "forge"

    @pytest.mark.asyncio
    async def test_forge_timeout(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=TimeoutError("Connection timeout")
            )

            image_path, error = await generate_image_in_forge("test")

            assert image_path == ""
            assert error["error"]["stage"] == "forge"
            assert error["status_code"] == 504

    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        image_path, error = await generate_image_in_forge("")

        assert image_path == ""
        assert error["error"]["stage"] == "forge_input"
        assert error["status_code"] == 400
