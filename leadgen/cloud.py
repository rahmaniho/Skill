"""کلاینت Supabase (PostgREST) — فقط کتابخانهٔ استاندارد پایتون، بدون وابستگی بیرونی.

چرا PostgREST و نه درایور Postgres؟
  • صفر وابستگی: همان فلسفهٔ پروژه (`pip install` لازم نیست) و روی Vercel هم کار می‌کند.
  • فقط HTTPS/443 — پورت‌های دیتابیس (۵۴۳۲) در محیط‌های serverless معمولاً بسته‌اند.
  • توابع SQL (RPC) سمت سرور اجرا می‌شوند، پس جست‌وجوی قوی یک round-trip است.

`opener` قابل تزریق است تا تست‌ها بدون شبکهٔ واقعی، رفتار کلاینت را بسنجند.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from . import config

def jsonable(value: Any, depth: int = 0) -> Any:
    """هر مقدار پایتون را به چیزی تبدیل می‌کند که `json.dumps`/PostgREST بپذیرد.

    چرا لازم است؟ پایپ‌لاین در payloadهای خود شیء‌هایی مثل `Niche` می‌گذارد؛ اگر بدون
    تبدیل به موتور یا پایگاه‌داده برسند، کل اجرا با TypeError می‌شکند.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 6:
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(val, depth + 1) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item, depth + 1) for item in value]
    for attr in ("to_dict", "as_dict", "dict", "__dataclass_fields__"):
        candidate = getattr(value, attr, None)
        if attr == "__dataclass_fields__":
            import dataclasses
            if dataclasses.is_dataclass(value):
                return jsonable(dataclasses.asdict(value), depth + 1)
            continue
        if callable(candidate):
            try:
                return jsonable(candidate(), depth + 1)
            except Exception:
                continue
    return str(value)


BATCH = 200          # سقف ردیف در هر درخواست درج (PostgREST چند مگابایت را می‌پذیرد)
DEFAULT_TIMEOUT = 45


class CloudError(RuntimeError):
    """خطای ارتباط با Supabase — با کد وضعیت و جزئیات برای تشخیص سریع."""

    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = (detail or "")[:600]

    def __str__(self) -> str:  # pragma: no cover - نمایش برای کاربر
        base = super().__str__()
        if self.status:
            base = f"{base} (HTTP {self.status})"
        if self.detail:
            base = f"{base}: {self.detail}"
        return base


class SupabaseClient:
    """کلاینت کوچک REST برای Supabase: جدول‌ها + توابع (RPC)."""

    def __init__(self, url: str | None = None, key: str | None = None,
                 schema: str | None = None, opener: Callable | None = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.url = (url if url is not None else config.SUPABASE_URL).rstrip("/")
        self.key = key if key is not None else config.SUPABASE_KEY
        self.schema = schema or config.SUPABASE_SCHEMA or "public"
        self.timeout = timeout
        self._opener = opener or _default_opener
        if not self.url:
            raise CloudError("SUPABASE_URL تنظیم نشده است")
        if not self.key:
            raise CloudError("کلید Supabase تنظیم نشده است (SUPABASE_SERVICE_KEY)")

    # ── لایهٔ انتقال ──────────────────────────────────────────────────────────
    def _headers(self, prefer: str | None = None, method: str = "GET") -> dict:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
            "Accept-Profile": self.schema,
            "User-Agent": config.USER_AGENT,
        }
        if method in ("POST", "PATCH", "PUT", "DELETE"):
            headers["Content-Type"] = "application/json"
            headers["Content-Profile"] = self.schema
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def request(self, method: str, path: str, *, params: dict | None = None,
                body: Any = None, prefer: str | None = None,
                expect_json: bool = True) -> Any:
        query = ""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        if clean:
            query = "?" + urllib.parse.urlencode(clean, doseq=True)
        url = f"{self.url}/rest/v1/{path.lstrip('/')}{query}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method,
                                         headers=self._headers(prefer, method))
        try:
            response = self._opener(request, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # pragma: no cover
                detail = ""
            raise CloudError(f"Supabase پاسخ خطا داد — {method} {path}", exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise CloudError(f"اتصال به Supabase برقرار نشد ({getattr(exc, 'reason', exc)})") from exc
        status = getattr(response, "status", None) or getattr(response, "code", 200)
        raw = response.read()
        if isinstance(raw, str):  # pragma: no cover - برخی فیک‌ها str می‌دهند
            raw = raw.encode("utf-8")
        if not raw or status == 204:
            return None
        if not expect_json:
            return raw.decode("utf-8", "replace")
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CloudError(f"پاسخ Supabase JSON نبود — {method} {path}", status, str(exc)) from exc

    # ── توابع SQL ────────────────────────────────────────────────────────────
    def rpc(self, function: str, payload: dict | None = None) -> Any:
        """فراخوانی یک تابع Postgres از راه `/rest/v1/rpc/<function>`."""
        return self.request("POST", f"rpc/{function}", body=payload or {})

    # ── جدول‌ها ──────────────────────────────────────────────────────────────
    def rows(self, table: str, *, select: str = "*", order: str | None = None,
             limit: int | None = None, offset: int | None = None,
             count: bool = False, extra_params: dict | None = None,
             **filters: str) -> tuple[list[dict], int | None]:
        """خواندن ردیف‌ها با فیلترهای PostgREST (مثلاً `status="eq.pending"`).

        خروجی: `(rows, total)` — `total` فقط وقتی `count=True` باشد پر می‌شود.
        """
        params: dict[str, Any] = {"select": select}
        for key, value in filters.items():
            if value is not None:
                params[key.replace("__", ".")] = value
        if extra_params:
            params.update({k: v for k, v in extra_params.items() if v is not None})
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = int(limit)
        if offset:
            params["offset"] = int(offset)
        prefer = "count=exact" if count else None
        result = self.request("GET", table, params=params, prefer=prefer)
        rows = result if isinstance(result, list) else []
        return rows, None

    def count(self, table: str, **filters: str) -> int:
        params = {"select": "*"}
        for key, value in filters.items():
            if value is not None:
                params[key.replace("__", ".")] = value
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{table}?" + urllib.parse.urlencode(params, doseq=True),
            method="HEAD", headers=self._headers("count=exact", "GET"))
        try:
            response = self._opener(request, self.timeout)
        except urllib.error.HTTPError as exc:
            raise CloudError(f"شمارش {table} ناموفق بود", exc.code) from exc
        except urllib.error.URLError as exc:
            raise CloudError(f"اتصال به Supabase برقرار نشد ({getattr(exc, 'reason', exc)})") from exc
        headers = getattr(response, "headers", {}) or {}
        content_range = headers.get("Content-Range") or headers.get("content-range") or ""
        if hasattr(headers, "get_all"):  # pragma: no cover
            values = headers.get_all("Content-Range") or []
            content_range = values[-1] if values else content_range
        try:
            return int(str(content_range).split("/")[-1])
        except (ValueError, IndexError):
            return 0

    def insert(self, table: str, rows: Iterable[dict], *, upsert: bool = True,
               on_conflict: str = "id") -> int:
        """درج دسته‌ای ردیف‌ها (upsert روی کلید اصلی)."""
        payload = [r for r in rows if r]
        if not payload:
            return 0
        prefer = f"resolution=merge-duplicates,return=minimal" if upsert else "return=minimal"
        params = {"on_conflict": on_conflict} if upsert and on_conflict else None
        for start in range(0, len(payload), BATCH):
            chunk = payload[start:start + BATCH]
            self.request("POST", table, params=params, body=chunk, prefer=prefer)
        return len(payload)

    def update(self, table: str, values: dict, **filters: str) -> int:
        params = {k.replace("__", "."): v for k, v in filters.items() if v is not None}
        self.request("PATCH", table, params=params, body=values, prefer="return=minimal")
        return 1

    def delete(self, table: str, **filters: str) -> int:
        params = {k.replace("__", "."): v for k, v in filters.items() if v is not None}
        self.request("DELETE", table, params=params, prefer="return=minimal")
        return 1

    # ── سلامت ────────────────────────────────────────────────────────────────
    def health(self) -> dict:
        """یک فراخوانی سبک برای اطمینان از اتصال و وجود schema."""
        try:
            self.request("GET", "runs", params={"select": "id", "limit": 1})
            return {"ok": True, "backend": "supabase", "url": self.url, "schema": self.schema}
        except CloudError as exc:
            return {"ok": False, "backend": "supabase", "url": self.url,
                    "error": str(exc), "status": exc.status}


def _default_opener(request: urllib.request.Request, timeout: int):
    return urllib.request.urlopen(request, timeout=timeout)


_CLIENT: SupabaseClient | None = None


def client(force_new: bool = False) -> SupabaseClient:
    """کلاینت مشترک (تنبل) — با `force_new` برای تست/تغییر env ساخته می‌شود."""
    global _CLIENT
    if _CLIENT is None or force_new:
        _CLIENT = SupabaseClient()
    return _CLIENT


def available() -> bool:
    return config.supabase_ready()
