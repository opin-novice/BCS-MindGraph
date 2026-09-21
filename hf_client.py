"""
hf_client.py
============
Single shared Hugging Face Inference client for BCSBatighor GK.

This replaces the old CraftX/Qwen `call_llm()` helper that used to live
in mcq_generator.py (and was duplicated, slightly differently, inside
intent_builder.py's LLMExtractor). Every module that talks to an LLM
now goes through this one file, so there's exactly one place that
knows how to reach Hugging Face.

Configuration (.env or real environment variables)
----------------------------------------------------
HF_API_KEY / HF_API_TOKEN
    Your Hugging Face access token. Create one at
    https://huggingface.co/settings/tokens — a "read" token is enough
    for the public Inference API / Inference Providers; a token with
    the right permissions is needed for a private dedicated Endpoint.
    (Either env var name works — HF_API_TOKEN is HF's own convention,
    HF_API_KEY matches this project's old CRAFTX_API_KEY naming.)

HF_API_KEY_1, HF_API_KEY_2, ...
    Optional extra tokens for round-robin/failover, same convention
    main-pipeline.py already used for CraftX.

HF_MODEL
    Which model to call, e.g. "Qwen/Qwen2.5-72B-Instruct" (default),
    "meta-llama/Llama-3.3-70B-Instruct", etc. Must be a chat-capable
    model available via HF Inference Providers.

HF_ENDPOINT_URL
    Optional. Set this instead of HF_MODEL if you're running a
    dedicated Hugging Face Inference Endpoint. When set, the endpoint
    URL already pins one model, so `model` arguments are ignored.

Usage
-----
    from hf_client import call_llm
    text = call_llm(api_key, "Qwen/Qwen2.5-72B-Instruct",
                     system_prompt, user_prompt)
"""

import os
import time
import logging
from typing import Optional

from huggingface_hub import InferenceClient

log = logging.getLogger("hf_client")

DEFAULT_MODEL   = os.getenv("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")
HF_ENDPOINT_URL = os.getenv("HF_ENDPOINT_URL", "").strip() or None

# FIX: how many times to retry the SAME key on a quota/rate-limit signal
# before giving up on it (see call_llm's docstring). Tunable via env vars
# without touching code — e.g. set HF_QUOTA_MAX_RETRIES=0 to restore the
# old fail-immediately behavior.
QUOTA_MAX_RETRIES         = int(os.getenv("HF_QUOTA_MAX_RETRIES", "3"))
QUOTA_RETRY_DELAY_SECONDS = float(os.getenv("HF_QUOTA_RETRY_DELAY_SECONDS", "20"))
# A stalled connection must not hang forever -- it needs to fail so the
# existing retry loop below can actually retry it. Configurable since a
# dedicated Endpoint under load may legitimately need longer than the
# public Inference API.
REQUEST_TIMEOUT_SECONDS   = float(os.getenv("HF_REQUEST_TIMEOUT_SECONDS", "60"))

# Same signal list the old CraftXKeyManager used — kept so key-rotation
# logic in main-pipeline.py doesn't need to change behavior, just names.
RATE_LIMIT_SIGNALS = (
    "429", "rate limit", "quota exceeded", "too many requests",
    "rate_limit_exceeded", "overloaded", "402", "payment required",
)


def is_rate_limit_error(exc: Exception) -> bool:
    return any(s in str(exc).lower() for s in RATE_LIMIT_SIGNALS)


HF_PROVIDER = os.getenv("HF_PROVIDER", "auto")

def _build_client(api_key: str) -> InferenceClient:
    kwargs = {"token": api_key or None, "timeout": REQUEST_TIMEOUT_SECONDS}
    if HF_ENDPOINT_URL:
        kwargs["base_url"] = HF_ENDPOINT_URL
    else:
        kwargs["provider"] = HF_PROVIDER
    return InferenceClient(**kwargs)


def call_llm(
    client:        str,
    model:         str,
    system_prompt: str,
    user_prompt:   str,
    temperature:   float = 0.7,
    max_tokens:    int   = 4096,
    max_retries:   int   = 3,
    quota_retries: int   = QUOTA_MAX_RETRIES,
    quota_retry_delay: float = QUOTA_RETRY_DELAY_SECONDS,
) -> str:
    """
    Call a Hugging Face-hosted chat model, with exponential-backoff
    retry. Signature intentionally matches the old CraftX call_llm()
    so every existing call site (mcq_generator.py's agents,
    mcq_quality.py's LLMQualityEvaluator, intent_builder.py's
    LLMExtractor) keeps working unchanged — only the import moves.

    Parameters
    ----------
    client : str
        Hugging Face API token (kept as `client` for drop-in
        compatibility with the old signature).
    model : str
        HF model id, e.g. "Qwen/Qwen2.5-72B-Instruct". Ignored if
        HF_ENDPOINT_URL is set (the endpoint already pins a model).
    quota_retries : int
        FIX: quota/rate-limit signals (429, 402, "quota exceeded", ...)
        used to raise on the FIRST occurrence with no retry at all, on
        the theory that a monthly-credit 402 "won't clear by retrying
        the same key" — true for a genuinely exhausted account, but it
        meant a single transient/burst 429 (which DOES often clear
        within seconds) was treated identically to a hard monthly
        quota wall, and the caller (e.g. IntentBuilder, which has no
        key rotation at all) fell back to rule-based extraction
        permanently after the very first hiccup. Now the SAME key gets
        `quota_retries` real attempts with backoff before we give up on
        it — configurable via HF_QUOTA_MAX_RETRIES / HF_QUOTA_RETRY_DELAY_SECONDS.
        After that many attempts still fail with a quota signal, we
        raise (so a caller that DOES have multiple independent-account
        keys, like HFKeyManager, can still rotate promptly rather than
        also waiting through this same backoff on every key).
    quota_retry_delay : float
        Base delay in seconds between quota retries (doubles each
        attempt: e.g. 20s, 40s, 80s for the default of 3 retries).
    """
    hf = _build_client(client)
    model_id = model or DEFAULT_MODEL
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    last_exc: Exception = RuntimeError("LLM call failed with no exception captured")
    quota_attempt = 0
    for attempt in range(max_retries):
        try:
            call_kwargs = dict(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if not HF_ENDPOINT_URL:
                call_kwargs["model"] = model_id
            response = hf.chat_completion(**call_kwargs)
            return response.choices[0].message.content.strip()
        except Exception as exc:
            last_exc = exc
            if is_rate_limit_error(exc):
                quota_attempt += 1
                if quota_attempt <= quota_retries:
                    delay = quota_retry_delay * (2 ** (quota_attempt - 1))
                    log.warning(
                        "HF LLM call hit a quota/rate-limit signal — retry "
                        "%d/%d on the same key in %.0fs (a transient burst "
                        "limit can clear this; a real monthly-credit 402 "
                        "will not — if this keeps happening every call, "
                        "check https://huggingface.co/settings/billing): %s",
                        quota_attempt, quota_retries, delay, str(exc)[:200],
                    )
                    time.sleep(delay)
                    continue
                log.warning(
                    "HF LLM call still hitting a quota/rate-limit signal "
                    "after %d retries — giving up on this key so the "
                    "caller can rotate: %s", quota_retries, str(exc)[:200],
                )
                raise
            log.warning("HF LLM call attempt %d/%d failed: %s",
                        attempt + 1, max_retries, str(exc)[:200])
            if attempt < max_retries - 1:
                time.sleep(5 * (2 ** attempt))  # 5s, 10s, 20s
    raise last_exc