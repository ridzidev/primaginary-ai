import asyncio
import time
import re
from typing import Dict, Any, List, Optional
import httpx
import config

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")

def parse_duration(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    val = val.strip()
    parts = _DURATION_RE.findall(val)
    if not parts:
        try:
            return float(val)
        except ValueError:
            return None
    multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * multipliers[unit] for n, unit in parts)

class LLMClient:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self.total_tokens = 0
        self.call_count = 0

    async def get_available_models(self) -> List[str]:
        url = f"{config.API_BASE_URL}/models"
        headers = {"Authorization": f"Bearer {config.API_KEY}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    return sorted([m["id"] for m in data])
        except Exception:
            pass
        return []

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        if not config.API_KEY:
            raise RuntimeError(f"API_KEY belum disetel di {config.GLOBAL_DIR / '.env'}")

        url = f"{config.API_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.API_KEY}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": config.MODEL_NAME,
            "messages": messages,
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with self._lock:
            for attempt in range(6):
                now = time.monotonic()
                diff = now - self._last_call
                if diff < config.MIN_REQUEST_INTERVAL:
                    await asyncio.sleep(config.MIN_REQUEST_INTERVAL - diff)

                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(url, headers=headers, json=payload)
                except httpx.HTTPError as e:
                    if attempt >= 3:
                        raise RuntimeError(f"Koneksi HTTP ke LLM gagal: {e}") from e
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                finally:
                    self._last_call = time.monotonic()

                if resp.status_code == 200:
                    data = resp.json()
                    self.call_count += 1
                    usage = data.get("usage", {})
                    self.total_tokens += int(usage.get("total_tokens", 0))
                    return data["choices"][0]["message"]

                if resp.status_code == 401:
                    raise RuntimeError("Kunci API ditolak (401 Unauthorized). Periksa ~/.primaginary-ai/.env")

                if resp.status_code == 404:
                    raise LookupError(f"Model '{config.MODEL_NAME}' tidak ditemukan di provider.")

                if resp.status_code in (429, 500, 502, 503, 504):
                    retry_hdr = resp.headers.get("retry-after")
                    wait_sec = parse_duration(retry_hdr) or (2.0 ** attempt * 2.0)
                    wait_sec = min(wait_sec, 60.0)
                    print(f"\n[RATE-LIMIT / STATUS {resp.status_code}] Jeda {wait_sec:.1f} detik...")
                    await asyncio.sleep(wait_sec + 0.5)
                    continue

                raise RuntimeError(f"API Error {resp.status_code}: {resp.text[:300]}")

            raise RuntimeError("Request ke LLM gagal setelah beberapa kali percobaan.")
