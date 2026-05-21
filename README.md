# Neironchik
Ai that can help u to generate images using stable diffusion that is already downloaded on your pc(important)

## Environment

Supported variables:

- `DATABASE_URL` - SQLite database path, default `sqlite+aiosqlite:///./database.db`
- `LLM_API_URL` - Ollama endpoint, default `http://127.0.0.1:11434/api/generate`
- `FORGE_API_URL` - Forge endpoint, default `http://127.0.0.1:7860/sdapi/v1/txt2img`
- `LLM_MODEL` - model name for prompt generation, default `qwen2.5:1.5b`
- `IMAGES_DIR` - folder for generated images, default `images`
