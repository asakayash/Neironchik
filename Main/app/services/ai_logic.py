import base64
import binascii
import json
import logging
import os
import re
import uuid

import httpx

LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:11434/api/chat")
LLM_GENERATE_URL = os.getenv("LLM_GENERATE_URL", "")
FORGE_API_URL = os.getenv("FORGE_API_URL", "http://127.0.0.1:7860/sdapi/v1/txt2img")
LLM_MODEL = os.getenv("LLM_MODEL", "prompter")
LLM_REQUEST_MODE = os.getenv("LLM_REQUEST_MODE", "raw_generate").strip().lower()
LLM_PROMPT_PREFIX = os.getenv(
    "LLM_PROMPT_PREFIX",
    "Convert this description into Illustrious SDXL tags and add negative prompt",
)
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "0")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_NUM_PREDICT = int(os.getenv("LLM_NUM_PREDICT", "256"))
LLM_MAX_RETRIES = max(1, int(os.getenv("LLM_MAX_RETRIES", "2")))
LLM_DEFAULT_STEPS = int(os.getenv("LLM_DEFAULT_STEPS", "28"))
LLM_DEFAULT_CFG_SCALE = float(os.getenv("LLM_DEFAULT_CFG_SCALE", "7.0"))
LLM_DEFAULT_SAMPLER = os.getenv("LLM_DEFAULT_SAMPLER", "DPM++ 2M")
LLM_PROMPT_MAX_TAGS = max(8, int(os.getenv("LLM_PROMPT_MAX_TAGS", "72")))
LLM_NEGATIVE_MAX_TAGS = max(8, int(os.getenv("LLM_NEGATIVE_MAX_TAGS", "32")))
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
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, normal quality, jpeg "
    "artifacts, signature, watermark, username, blurry"
)

USER_TEXT_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "the",
    "to",
    "view",
    "with",
}

STYLE_SPAM_TAGS = {
    "artstation",
    "trending on artstation",
    "concept art",
    "illustration",
    "digital painting",
    "8 k",
    "8k",
    "8 k resolution",
    "greg rutkowski",
    "alphonse mucha",
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


def _split_tag_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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


def determine_resolution(prompt_text: str, user_text: str) -> tuple[int, int]:
    combined_text = (prompt_text + " " + user_text).lower()

    portrait_keywords = [
        "portrait",
        "vertical",
        "standing",
        "full body",
        "1girl",
        "1boy",
        "cowboy shot",
        "upper body",
    ]

    landscape_keywords = [
        "landscape",
        "scenery",
        "horizontal",
        "wide shot",
        "panoramic",
        "cityscape",
        "background",
        "nature",
    ]

    if any(keyword in combined_text for keyword in landscape_keywords):
        return 1216, 832
    if any(keyword in combined_text for keyword in portrait_keywords):
        return 832, 1216

    return 1024, 1024


def _sanitize_llm_text(value: str) -> str:
    cleaned = value.replace("**", "").strip()

    for prefix in ("prompt:", "negative prompt:", "tags:", "output:", "response:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()

    return cleaned.strip("`\"'")


def _strip_code_fences(value: str) -> str:
    candidate = value.strip().lstrip("\ufeff")

    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    return candidate


def _normalize_tag_csv(value: str, *, max_items: int) -> str:
    candidate = _sanitize_llm_text(value)
    if not candidate:
        return ""

    if "," not in candidate:
        return candidate

    items = _split_tag_items(candidate)
    items = _dedupe_keep_order(items)
    return ", ".join(items[:max_items])


def _looks_like_tag_prompt(value: str) -> bool:
    normalized = " ".join(value.strip().split())
    if not normalized:
        return False

    if "\n" in value:
        return False

    sentence_punctuation_count = sum(normalized.count(char) for char in ".!?;")
    if sentence_punctuation_count >= 2:
        return False

    comma_count = normalized.count(",")
    word_count = len(normalized.split())

    if comma_count == 0 and word_count > 3:
        return False

    if comma_count < 2 and word_count > 10:
        return False

    if ": " in normalized and comma_count < 2:
        return False

    return True


def _extract_user_keywords(user_text: str) -> set[str]:
    normalized = user_text.casefold().replace("-", " ")
    keywords = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", normalized)
        if token not in USER_TEXT_STOPWORDS
    }
    return keywords


def _is_generic_style_spam(prompt: str, user_text: str) -> bool:
    tags = [item.casefold() for item in _split_tag_items(prompt)]
    if not tags:
        return False

    spam_hits = 0
    for tag in tags:
        if tag in STYLE_SPAM_TAGS:
            spam_hits += 1
            continue

        if "artstation" in tag:
            spam_hits += 1
            continue

        if "rutkowski" in tag or "mucha" in tag:
            spam_hits += 1

    if spam_hits < 3:
        return False

    prompt_text = prompt.casefold().replace("-", " ")
    user_keywords = _extract_user_keywords(user_text)
    keyword_hits = sum(1 for keyword in user_keywords if keyword in prompt_text)
    return keyword_hits == 0


def _build_prompt_from_user_text(user_text: str) -> str:
    normalized = user_text.strip().lower()
    if not normalized:
        return ""

    normalized = re.sub(r"\bview of\b", "", normalized)
    normalized = re.sub(r"\bwith\b", ", ", normalized)
    normalized = re.sub(r"\bin the\b", ", ", normalized)
    normalized = re.sub(r"\bin\b", ", ", normalized)
    normalized = re.sub(r"\band\b", ", ", normalized)
    normalized = re.sub(r"\bof\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ,")

    items = _split_tag_items(normalized)
    if not items:
        items = [normalized]

    cleaned_items = []
    for item in items:
        item = item.strip(" ,")
        if not item:
            continue
        cleaned_items.append(item)

    cleaned_items = _dedupe_keep_order(cleaned_items)
    return ", ".join(cleaned_items[:LLM_PROMPT_MAX_TAGS])


def _extract_json_object(raw_response: str) -> dict:
    candidate = _strip_code_fences(raw_response)

    if not candidate:
        raise json.JSONDecodeError("Empty LLM response", raw_response, 0)

    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise json.JSONDecodeError(
                "LLM response does not contain JSON", raw_response, 0
            )
        candidate = candidate[start : end + 1]

    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")

    return parsed


def _extract_json_string_field(raw_response: str, field_name: str) -> str:
    pattern = rf'"{re.escape(field_name)}"\s*:\s*"(?P<value>(?:[^"\\]|\\.)*)(?:"|$)'
    match = re.search(pattern, raw_response, flags=re.DOTALL)
    if not match:
        return ""

    raw_value = match.group("value")
    try:
        return json.loads(f'"{raw_value}"')
    except json.JSONDecodeError:
        return raw_value


def _extract_prompt_from_parsed(parsed: dict, user_text: str) -> tuple[str, bool]:
    for key in ("prompt", "tags", "prompt_tags", "tag_list"):
        candidate = _normalize_tag_csv(
            _stringify_prompt_value(parsed.get(key)),
            max_items=LLM_PROMPT_MAX_TAGS,
        )
        if candidate and _looks_like_tag_prompt(candidate):
            return candidate, True

    fallback = _normalize_tag_csv(
        _build_prompt_fallback(parsed, user_text),
        max_items=LLM_PROMPT_MAX_TAGS,
    )
    if fallback and fallback != user_text.strip() and _looks_like_tag_prompt(fallback):
        return fallback, True

    return "", False


def _extract_prompt_from_text(raw_response: str) -> str:
    candidate = _strip_code_fences(raw_response)
    if not candidate:
        return ""

    partial_json_prompt = _extract_json_string_field(candidate, "prompt")
    if partial_json_prompt:
        return _normalize_tag_csv(partial_json_prompt, max_items=LLM_PROMPT_MAX_TAGS)

    if candidate.startswith("{") and '"prompt"' not in candidate:
        return ""

    lines = []
    for line in candidate.splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue

        lowered = clean_line.casefold()
        if lowered.startswith("negative prompt"):
            break

        for prefix in ("prompt:", "tags:", "output:", "response:"):
            if lowered.startswith(prefix):
                clean_line = clean_line[len(prefix) :].strip()
                lowered = clean_line.casefold()
                break

        if clean_line:
            lines.append(clean_line)

    if lines:
        candidate = lines[0]

    candidate = candidate.split("negative prompt", 1)[0].strip()
    candidate = candidate.strip("{}[]")
    return _normalize_tag_csv(candidate, max_items=LLM_PROMPT_MAX_TAGS)


def _normalize_negative_prompt(value) -> str:
    candidate = _normalize_tag_csv(
        _stringify_prompt_value(value), max_items=LLM_NEGATIVE_MAX_TAGS
    )
    if not candidate:
        return DEFAULT_NEGATIVE_PROMPT

    if len(candidate) > 500:
        return DEFAULT_NEGATIVE_PROMPT

    return candidate


def _derive_generate_url() -> str:
    if LLM_GENERATE_URL.strip():
        return LLM_GENERATE_URL.strip()

    if LLM_API_URL.endswith("/api/chat"):
        return f"{LLM_API_URL[:-len('/api/chat')]}/api/generate"

    return LLM_API_URL


def _build_generate_prompt(user_text: str, *, strict: bool) -> str:
    prefix = LLM_PROMPT_PREFIX.strip()

    if not strict:
        return f"{prefix} {user_text}".strip()

    return (
        f"{prefix} "
        f"{user_text} "
        "Return only comma-separated Illustrious SDXL tags for the main prompt. "
        "Do not add artists, ArtStation tags, ratings, or repeated quality spam unless explicitly requested. "
        "Do not write sentences or explanations. "
        "Tags:"
    )


def _build_generate_payload(user_text: str, *, strict: bool) -> dict:
    return {
        "model": LLM_MODEL,
        "prompt": _build_generate_prompt(user_text, strict=strict),
        "stream": False,
        "raw": True,
        "keep_alive": LLM_KEEP_ALIVE,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": LLM_NUM_PREDICT,
        },
    }


def _build_chat_instruction(*, strict: bool) -> str:
    schema_text = json.dumps(LLM_RESPONSE_SCHEMA, ensure_ascii=False)
    parts = [
        "Convert this description into Illustrious SDXL tags.",
        "The prompt field must contain only a comma-separated tag list.",
        "Do not write prose, stories, explanations, lore, credits, titles, or markdown.",
    ]

    if strict:
        parts.append(
            "Your previous reply was invalid. Reply with JSON only and keep the prompt short and tag-like."
        )

    parts.append(f"Return only a JSON object that matches this schema: {schema_text}")
    return " ".join(parts)


def _build_chat_payload(user_text: str, *, strict: bool) -> dict:
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _build_chat_instruction(strict=strict)},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "format": LLM_RESPONSE_SCHEMA,
        "keep_alive": LLM_KEEP_ALIVE,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": LLM_NUM_PREDICT,
        },
    }


def _get_llm_request_url() -> str:
    if LLM_REQUEST_MODE == "chat_json":
        return LLM_API_URL
    return _derive_generate_url()


def _build_llm_payload(user_text: str, *, strict: bool) -> dict:
    if LLM_REQUEST_MODE == "chat_json":
        return _build_chat_payload(user_text, strict=strict)
    return _build_generate_payload(user_text, strict=strict)


async def get_prompt_from_llm(user_text: str) -> dict:
    request_url = _get_llm_request_url()

    async with httpx.AsyncClient(timeout=_get_llm_timeout()) as client:
        try:
            last_error = None

            for attempt in range(1, LLM_MAX_RETRIES + 1):
                payload = _build_llm_payload(user_text, strict=attempt > 1)

                print("\n" + "-" * 50)
                print(
                    f"[INFO] SENDING REQUEST TO OLLAMA (attempt {attempt}/{LLM_MAX_RETRIES}, mode={LLM_REQUEST_MODE})"
                )
                print(f"URL: {request_url}")
                print(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
                print("-" * 50 + "\n")

                resp = await client.post(request_url, json=payload)
                resp.raise_for_status()

                response_payload = resp.json()
                raw_response = (
                    response_payload.get("message", {}).get("content")
                    or response_payload.get("response", "")
                ).strip()

                print("\n" + "-" * 50)
                print(
                    f"[INFO] RECEIVED RESPONSE FROM OLLAMA (attempt {attempt}/{LLM_MAX_RETRIES})"
                )
                print(raw_response)
                print("-" * 50 + "\n")

                if not raw_response:
                    last_error = _build_error(
                        "llm",
                        "LLM returned an empty response body.",
                        status_code=502,
                        endpoint=request_url,
                        model=LLM_MODEL,
                        attempt=attempt,
                        response_preview=_preview_text(response_payload),
                    )
                    if attempt < LLM_MAX_RETRIES:
                        logger.warning(
                            "LLM returned an empty response on attempt %s", attempt
                        )
                        continue
                    return last_error

                parsed = None
                parse_error = None
                prompt = ""

                try:
                    parsed = _extract_json_object(raw_response)
                    prompt, is_valid_prompt = _extract_prompt_from_parsed(
                        parsed, user_text
                    )
                    if not is_valid_prompt:
                        prompt = ""
                except (json.JSONDecodeError, ValueError) as exc:
                    parse_error = exc

                if not prompt:
                    prompt = _extract_prompt_from_text(raw_response)

                if prompt and _is_generic_style_spam(prompt, user_text):
                    prompt = ""

                if not prompt or not _looks_like_tag_prompt(prompt):
                    error_stage = (
                        "llm_parse" if parse_error is not None else "llm_validation"
                    )
                    error_message = (
                        "Failed to parse LLM response into a usable prompt."
                        if parse_error is not None
                        else "LLM response did not contain a valid tag-style prompt."
                    )
                    last_error = _build_error(
                        error_stage,
                        error_message,
                        status_code=502,
                        endpoint=request_url,
                        model=LLM_MODEL,
                        mode=LLM_REQUEST_MODE,
                        attempt=attempt,
                        raw_response=_preview_text(raw_response),
                        prompt_preview=_preview_text(prompt),
                        exception=(
                            repr(parse_error) if parse_error is not None else None
                        ),
                    )
                    if attempt < LLM_MAX_RETRIES:
                        logger.warning(
                            "LLM returned unusable prompt on attempt %s", attempt
                        )
                        continue
                    fallback_prompt = _build_prompt_from_user_text(user_text)
                    if fallback_prompt:
                        logger.warning(
                            "Using deterministic prompt fallback after unusable LLM response"
                        )
                        prompt = fallback_prompt
                    else:
                        return last_error

                negative_prompt = DEFAULT_NEGATIVE_PROMPT
                if isinstance(parsed, dict):
                    negative_prompt = _normalize_negative_prompt(
                        parsed.get("negative_prompt") or parsed.get("negative")
                    )

                steps = LLM_DEFAULT_STEPS
                cfg_scale = LLM_DEFAULT_CFG_SCALE
                sampler_name = LLM_DEFAULT_SAMPLER

                if isinstance(parsed, dict):
                    raw_steps = parsed.get("steps", LLM_DEFAULT_STEPS)
                    try:
                        steps = int(raw_steps)
                    except (TypeError, ValueError):
                        steps = LLM_DEFAULT_STEPS

                    raw_cfg_scale = parsed.get("cfg_scale", LLM_DEFAULT_CFG_SCALE)
                    try:
                        cfg_scale = float(raw_cfg_scale)
                    except (TypeError, ValueError):
                        cfg_scale = LLM_DEFAULT_CFG_SCALE

                    sampler_candidate = _stringify_prompt_value(
                        parsed.get("sampler_name") or parsed.get("sampler")
                    )
                    if sampler_candidate:
                        sampler_name = _sanitize_llm_text(sampler_candidate)

                width, height = determine_resolution(prompt, user_text)

                return {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "steps": steps,
                    "cfg_scale": cfg_scale,
                    "sampler_name": sampler_name,
                    "scheduler": "Karras",
                    "width": width,
                    "height": height,
                }

            return last_error or _build_error(
                "llm",
                "LLM request failed without a detailed error.",
                status_code=500,
                endpoint=request_url,
                model=LLM_MODEL,
                mode=LLM_REQUEST_MODE,
            )

        except (httpx.TimeoutException, TimeoutError) as exc:
            print("[ERROR] Ollama request timed out")
            logger.exception("LLM request timed out")
            return _build_error(
                "llm",
                "LLM request timed out.",
                status_code=504,
                endpoint=request_url,
                model=LLM_MODEL,
                mode=LLM_REQUEST_MODE,
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
            print(
                f"[ERROR] Ollama HTTP status error {exc.response.status_code}: {response_text}"
            )
            logger.exception("LLM returned HTTP %s", exc.response.status_code)
            return _build_error(
                "llm",
                "LLM service returned a non-success status.",
                status_code=502,
                endpoint=request_url,
                model=LLM_MODEL,
                mode=LLM_REQUEST_MODE,
                upstream_status=exc.response.status_code,
                response_preview=_preview_text(response_text),
            )
        except httpx.RequestError as exc:
            print(f"[ERROR] Failed to reach Ollama: {repr(exc)}")
            logger.exception("Failed to reach LLM service")
            return _build_error(
                "llm",
                "Failed to reach LLM service.",
                status_code=502,
                endpoint=request_url,
                model=LLM_MODEL,
                mode=LLM_REQUEST_MODE,
                exception=repr(exc),
            )
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None and hasattr(response, "status_code"):
                response_text = getattr(response, "text", "").strip()
                print(
                    f"[ERROR] Ollama HTTP status error {response.status_code}: {response_text}"
                )
                logger.exception("LLM returned HTTP %s", response.status_code)
                return _build_error(
                    "llm",
                    "LLM service returned a non-success status.",
                    status_code=502,
                    endpoint=request_url,
                    model=LLM_MODEL,
                    mode=LLM_REQUEST_MODE,
                    upstream_status=response.status_code,
                    response_preview=_preview_text(response_text),
                )
            print(f"[ERROR] Unexpected LLM error: {repr(exc)}")
            logger.exception("Unexpected LLM error")
            return _build_error(
                "llm",
                "Unexpected LLM error.",
                status_code=500,
                endpoint=request_url,
                model=LLM_MODEL,
                mode=LLM_REQUEST_MODE,
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

    print("\n" + "=" * 50)
    print("[INFO] SENDING REQUEST TO FORGE")
    print(f"URL: {FORGE_API_URL}")
    print(
        f"Resolution: {default_settings.get('width')}x{default_settings.get('height')}"
    )
    print(f"Prompt (start): {default_settings.get('prompt')[:100]}...")
    print("Waiting for response from Forge...")
    print("=" * 50 + "\n")

    async with httpx.AsyncClient(timeout=_get_forge_timeout()) as client:
        try:
            resp = await client.post(FORGE_API_URL, json=default_settings)
            resp.raise_for_status()
            data = resp.json()

            images = data.get("images", [])
            if not images:
                print("[ERROR] Forge returned an empty images array.")
                return "", _build_error(
                    "forge",
                    "Forge returned no images.",
                    status_code=502,
                    endpoint=FORGE_API_URL,
                    response_preview=_preview_text(data),
                )

            print("[INFO] IMAGE SUCCESSFULLY GENERATED IN FORGE.")

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
            with open(filename, "wb") as file_obj:
                file_obj.write(base64.b64decode(images[0]))

            return filename, info
        except (httpx.TimeoutException, TimeoutError) as exc:
            print("[ERROR] Forge request timed out.")
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
            print(
                f"[ERROR] Forge HTTP status error {exc.response.status_code}: {response_text}"
            )
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
            print(f"[ERROR] Failed to reach Forge: {repr(exc)}")
            logger.exception("Failed to reach Forge service")
            return "", _build_error(
                "forge",
                "Failed to reach Forge service.",
                status_code=502,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
        except (ValueError, OSError, binascii.Error) as exc:
            print(f"[ERROR] Forge response processing failed: {repr(exc)}")
            logger.exception("Forge response processing failed")
            return "", _build_error(
                "forge",
                "Forge response could not be processed.",
                status_code=500,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
        except Exception as exc:
            print(f"[ERROR] Unexpected Forge error: {repr(exc)}")
            logger.exception("Unexpected Forge error")
            return "", _build_error(
                "forge",
                "Unexpected Forge error.",
                status_code=500,
                endpoint=FORGE_API_URL,
                exception=repr(exc),
            )
