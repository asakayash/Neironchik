import ast
import asyncio
import base64
import binascii
import json
import logging
import os
import uuid

import httpx

LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:11434/api/chat")
FORGE_API_URL = os.getenv("FORGE_API_URL", "http://127.0.0.1:7860/sdapi/v1/txt2img")
LLM_MODEL = os.getenv("LLM_MODEL", "prompter:latest")
IMAGES_DIR = os.getenv("IMAGES_DIR", "images")
LLM_CONNECT_TIMEOUT = float(os.getenv("LLM_CONNECT_TIMEOUT", "10"))
LLM_WRITE_TIMEOUT = float(os.getenv("LLM_WRITE_TIMEOUT", "30"))
LLM_POOL_TIMEOUT = float(os.getenv("LLM_POOL_TIMEOUT", "30"))
LLM_READ_TIMEOUT_RAW = os.getenv("LLM_READ_TIMEOUT", "180")
FORGE_CONNECT_TIMEOUT = float(os.getenv("FORGE_CONNECT_TIMEOUT", "10"))
FORGE_WRITE_TIMEOUT = float(os.getenv("FORGE_WRITE_TIMEOUT", "30"))
FORGE_POOL_TIMEOUT = float(os.getenv("FORGE_POOL_TIMEOUT", "30"))
FORGE_READ_TIMEOUT_RAW = os.getenv("FORGE_READ_TIMEOUT", "900")

LLM_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "steps": {"type": "integer"},
        "cfg_scale": {"type": "number"},
        "sampler_name": {"type": "string"},
    },
    "required": ["prompt", "negative_prompt", "steps", "cfg_scale", "sampler_name"],
    "additionalProperties": True,
}

logger = logging.getLogger(__name__)


def _preview_text(value, limit: int = 300) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = repr(value)

    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _build_error(stage: str, message: str, *, status_code: int = 500, **extra) -> dict:
    error = {
        "stage": stage,
        "message": message,
    }
    error.update(
        {key: value for key, value in extra.items() if value not in (None, "", [], {})}
    )
    return {"error": error, "status_code": status_code}


def _stringify_prompt_value(value) -> str:
    if value is None:
        return ""

    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float)):
        return str(value).strip()

    return ""


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _extract_prompt_fragments(field_name: str, value) -> list[str]:
    field_label = field_name.replace("_", " ").strip()
    fragments = []

    if value is None:
        return fragments

    if isinstance(value, bool):
        if value:
            fragments.append(field_label)
        return fragments

    if isinstance(value, list):
        for item in value:
            fragments.extend(_extract_prompt_fragments(field_name, item))
        return fragments

    if isinstance(value, (int, float)):
        value = str(value)

    if not isinstance(value, str):
        return fragments

    clean_value = value.strip().replace("_", " ")
    if not clean_value:
        return fragments

    if field_label in {"subject", "character", "scene", "setting", "prompt"}:
        fragments.append(clean_value)
        return fragments

    if field_label in {"style", "medium", "artist", "quality", "mood", "lighting"}:
        fragments.append(clean_value)
        return fragments

    if clean_value.casefold() in {"true", "yes"}:
        fragments.append(field_label)
        return fragments

    if clean_value.casefold() in {"false", "no"}:
        return fragments

    fragments.append(f"{clean_value} {field_label}".strip())
    return fragments


def _build_prompt_fallback(parsed: dict, user_text: str) -> str:
    excluded_keys = {
        "prompt",
        "tags",
        "prompt_tags",
        "tag_list",
        "negative_prompt",
        "negative",
        "steps",
        "cfg_scale",
        "sampler_name",
        "sampler",
    }
    fragments = []

    for key, value in parsed.items():
        if key in excluded_keys:
            continue
        fragments.extend(_extract_prompt_fragments(key, value))

    fragments = _dedupe_keep_order([fragment for fragment in fragments if fragment])
    original_text = user_text.strip()

    if original_text and fragments:
        return ", ".join([original_text, *fragments])
    if fragments:
        return ", ".join(fragments)
    return original_text


def _get_forge_timeout() -> httpx.Timeout:
    read_timeout_raw = FORGE_READ_TIMEOUT_RAW.strip().lower()
    read_timeout = (
        None
        if read_timeout_raw in {"none", "0", "false", "off"}
        else float(FORGE_READ_TIMEOUT_RAW)
    )

    return httpx.Timeout(
        connect=FORGE_CONNECT_TIMEOUT,
        write=FORGE_WRITE_TIMEOUT,
        pool=FORGE_POOL_TIMEOUT,
        read=read_timeout,
    )


def _get_llm_timeout() -> httpx.Timeout:
    read_timeout_raw = LLM_READ_TIMEOUT_RAW.strip().lower()
    read_timeout = (
        None
        if read_timeout_raw in {"none", "0", "false", "off"}
        else float(LLM_READ_TIMEOUT_RAW)
    )

    return httpx.Timeout(
        connect=LLM_CONNECT_TIMEOUT,
        write=LLM_WRITE_TIMEOUT,
        pool=LLM_POOL_TIMEOUT,
        read=read_timeout,
    )


async def get_prompt_from_llm(user_text: str) -> dict:
    schema_hint = json.dumps(LLM_RESPONSE_SCHEMA, ensure_ascii=False)
    system_instruction = (
        "You are an expert prompt engineer for the Illustrious SDXL anime model. "
        "Your task is to translate user descriptions into a rich, comma-separated list of Danbooru-style tags. "
        "RULES: "
        "1. NEVER use full sentences. Break everything down into single words or short phrases. "
        "2. Always start the prompt with quality tags: 'masterpiece, best quality, ultra-detailed, highres'. "
        "3. Add lighting, environment, and camera angle tags to make the prompt voluminous. "
        "4. Output ONLY a valid JSON object with double quotes. "
        "EXAMPLE OUTPUT:\n"
        "{\n"
        '  "prompt": "masterpiece, best quality, 1girl, solo, cyberpunk city, high-rise buildings, neon lights, raining, wet streets, night, outdoors, glowing, cinematic lighting, realism, highly detailed",\n'
        '  "negative_prompt": "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, watermark, username, blurry",\n'
        '  "steps": 28,\n'
        '  "cfg_scale": 7.0,\n'
        '  "sampler_name": "Euler a"\n'
        "}\n"
        f"Follow this JSON schema exactly: {schema_hint}"
    )

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Convert to tags: {user_text}"},  # Триггер!
        ],
        "stream": False,
        "format": LLM_RESPONSE_SCHEMA,  # Если используешь structured outputs в свежей Ollama
        "keep_alive": "5m",
        "options": {
            "temperature": 0.8,  # Делает выбор тегов богаче
            "top_p": 0.9,
            "num_ctx": 4096,  # Даем больше контекста для объемных ответов
        },
    }

    async with httpx.AsyncClient(timeout=_get_llm_timeout()) as client:
        try:
            resp = await client.post(LLM_API_URL, json=payload)
            resp.raise_for_status()

            response_payload = resp.json()
            raw_response = (
                response_payload.get("message", {}).get("content")
                or response_payload.get("response", "")
            ).strip()

            if not raw_response:
                logger.error(
                    "LLM returned empty content: %s", _preview_text(response_payload)
                )
                return _build_error(
                    "llm",
                    "LLM returned an empty response body.",
                    status_code=502,
                    endpoint=LLM_API_URL,
                    model=LLM_MODEL,
                    response_preview=_preview_text(response_payload),
                )

            try:
                parsed = json.loads(raw_response)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw_response)
                except (SyntaxError, ValueError) as eval_err:
                    logger.error("Failed to parse LLM response: %s", raw_response)
                    return _build_error(
                        "llm_parse",
                        "Failed to parse LLM response as JSON.",
                        status_code=502,
                        model=LLM_MODEL,
                        endpoint=LLM_API_URL,
                        raw_response=_preview_text(raw_response),
                        parse_error=repr(eval_err),
                    )

            if not isinstance(parsed, dict):
                return _build_error(
                    "llm_parse",
                    "LLM response must be a JSON object.",
                    status_code=502,
                    model=LLM_MODEL,
                    endpoint=LLM_API_URL,
                    raw_response=_preview_text(raw_response),
                    parsed_type=type(parsed).__name__,
                )

            prompt = _stringify_prompt_value(parsed.get("prompt"))
            if not prompt:
                prompt = _stringify_prompt_value(parsed.get("tags"))
            if not prompt:
                prompt = _stringify_prompt_value(parsed.get("prompt_tags"))
            if not prompt:
                prompt = _stringify_prompt_value(parsed.get("tag_list"))
            if not prompt:
                prompt = _build_prompt_fallback(parsed, user_text)

            if not prompt:
                logger.error(
                    "LLM response is missing a usable prompt: %s", raw_response
                )
                return _build_error(
                    "llm_validation",
                    "LLM response is missing a non-empty prompt.",
                    status_code=502,
                    model=LLM_MODEL,
                    endpoint=LLM_API_URL,
                    raw_response=_preview_text(raw_response),
                    parsed_keys=sorted(parsed.keys()),
                )
            elif prompt == user_text.strip():
                logger.warning(
                    "LLM prompt fallback used original user text: %s", raw_response
                )
            elif prompt.startswith(f"{user_text.strip()},"):
                logger.warning(
                    "LLM prompt fallback combined original text with partial structured fields: %s",
                    raw_response,
                )

            negative_prompt = _stringify_prompt_value(parsed.get("negative_prompt"))
            if not negative_prompt:
                negative_prompt = _stringify_prompt_value(parsed.get("negative"))
            if not negative_prompt:
                negative_prompt = (
                    "blurry, low quality, distorted, deformed, bad anatomy"
                )

            return {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "steps": int(parsed.get("steps", 28)),
                "cfg_scale": float(parsed.get("cfg_scale", 7.0)),
                "sampler_name": str(
                    parsed.get("sampler_name") or parsed.get("sampler") or "Euler a"
                ),
            }
        except httpx.TimeoutException as exc:
            logger.exception("LLM request timed out")
            return _build_error(
                "llm",
                "LLM request timed out.",
                status_code=504,
                endpoint=LLM_API_URL,
                model=LLM_MODEL,
                timeout_config={
                    "connect": LLM_CONNECT_TIMEOUT,
                    "write": LLM_WRITE_TIMEOUT,
                    "pool": LLM_POOL_TIMEOUT,
                    "read": LLM_READ_TIMEOUT_RAW,
                },
                exception=repr(exc),
            )
        except httpx.HTTPStatusError as exc:
            response_text = exc.response.text.strip()
            logger.exception("LLM returned HTTP %s", exc.response.status_code)
            return _build_error(
                "llm",
                "LLM service returned a non-success status.",
                status_code=502,
                endpoint=LLM_API_URL,
                model=LLM_MODEL,
                upstream_status=exc.response.status_code,
                response_preview=_preview_text(response_text),
            )
        except httpx.RequestError as exc:
            logger.exception("Failed to reach LLM service")
            return _build_error(
                "llm",
                "Failed to reach LLM service.",
                status_code=502,
                endpoint=LLM_API_URL,
                model=LLM_MODEL,
                exception=repr(exc),
            )
        except Exception as exc:
            logger.exception("Unexpected LLM error")
            return _build_error(
                "llm",
                "Unexpected LLM error.",
                status_code=500,
                endpoint=LLM_API_URL,
                model=LLM_MODEL,
                exception=repr(exc),
            )


async def generate_image_in_forge(
    prompt: str,
    negative_prompt: str = "blurry, low quality, distorted, deformed, bad anatomy",
    settings: dict = None,
) -> tuple[str, dict | None]:
    """
    Generate an image in Forge with optional settings overrides.
    settings can contain: steps, cfg_scale, width, height, sampler_name.
    """
    prompt = prompt.strip()
    if not prompt:
        return "", _build_error(
            "forge_input",
            "Prompt is empty before sending request to Forge.",
            status_code=400,
            endpoint=FORGE_API_URL,
        )

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

    if settings:
        default_settings.update(settings)

    await asyncio.sleep(1)

    async with httpx.AsyncClient(timeout=_get_forge_timeout()) as client:
        try:
            resp = await client.post(FORGE_API_URL, json=default_settings)
            resp.raise_for_status()
            data = resp.json()

            images = data.get("images", [])
            if not images:
                return "", _build_error(
                    "forge",
                    "Forge returned no images.",
                    status_code=502,
                    endpoint=FORGE_API_URL,
                    response_preview=_preview_text(data),
                )

            info_raw = data.get("info", "{}")
            if isinstance(info_raw, str):
                try:
                    info = json.loads(info_raw) if info_raw else {}
                except json.JSONDecodeError:
                    info = {"raw_info": _preview_text(info_raw)}
            elif isinstance(info_raw, dict):
                info = info_raw
            else:
                info = {"raw_info": repr(info_raw)}

            os.makedirs(IMAGES_DIR, exist_ok=True)
            filename = os.path.join(IMAGES_DIR, f"forge_{uuid.uuid4().hex[:8]}.png")
            with open(filename, "wb") as f:
                f.write(base64.b64decode(images[0]))

            return filename, info
        except httpx.TimeoutException as exc:
            logger.exception("Forge request timed out")
            return "", _build_error(
                "forge",
                "Forge request timed out.",
                status_code=504,
                endpoint=FORGE_API_URL,
                timeout_config={
                    "connect": FORGE_CONNECT_TIMEOUT,
                    "write": FORGE_WRITE_TIMEOUT,
                    "pool": FORGE_POOL_TIMEOUT,
                    "read": FORGE_READ_TIMEOUT_RAW,
                },
                exception=repr(exc),
            )
        except httpx.HTTPStatusError as exc:
            response_text = exc.response.text.strip()
            logger.exception("Forge returned HTTP %s", exc.response.status_code)
            return "", _build_error(
                "forge",
                "Forge service returned a non-success status.",
                status_code=502,
                endpoint=FORGE_API_URL,
                upstream_status=exc.response.status_code,
                response_preview=_preview_text(response_text),
            )
        except httpx.RequestError as exc:
            logger.exception("Failed to reach Forge service")
            return "", _build_error(
                "forge",
                "Failed to reach Forge service.",
                status_code=502,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
        except (ValueError, OSError, binascii.Error) as exc:
            logger.exception("Forge response processing failed")
            return "", _build_error(
                "forge",
                "Forge response could not be processed.",
                status_code=500,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
        except Exception as exc:
            logger.exception("Unexpected Forge error")
            return "", _build_error(
                "forge",
                "Unexpected Forge error.",
                status_code=500,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
