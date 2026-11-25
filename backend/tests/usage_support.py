import json
from datetime import datetime
from pathlib import Path

from youmo_workflow.domain.usage_pricing import UsagePricing


def prices() -> UsagePricing:
    return UsagePricing.model_validate_json(
        (Path(__file__).parents[2] / "config/deepseek-pricing.json").read_bytes()
    )


def write_call(directory: Path, identifier: str, *, missing_usage: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "attempt.json").write_text('{"max_calls":1}', encoding="utf-8")
    (directory / "payload.json").write_text(
        '{"model":"deepseek-flash","private_prompt":"NEVER_RETURN_THIS"}', encoding="utf-8"
    )
    body = {
        "id": identifier,
        "model": "deepseek-flash",
        "created": int(datetime.fromisoformat("2026-11-07T10:00:00+08:00").timestamp()),
        "choices": [{"message": {"content": "PRIVATE_MODEL_TEXT"}}],
        "usage": None
        if missing_usage
        else {
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "total_tokens": 300,
            "prompt_cache_hit_tokens": 20,
            "prompt_cache_miss_tokens": 80,
            "completion_tokens_details": {"reasoning_tokens": 150},
        },
    }
    target = directory / "provider-response.json"
    target.write_text(json.dumps(body), encoding="utf-8")
    return target
