"""پل WSGI برای سرور — همان API، بدون نیاز به سوکت و `http.server`.

چرا این ماژول؟
  سرور محلی ما روی `http.server` است (صفر وابستگی). اما روی Vercel/Cloud Run و هر
  میزبان serverless دیگری، ورودی یک تابع WSGI/ASGI است و سوکت‌بازکردن ممکن نیست.
  به‌جای بازنویسی ۱۰۰۰ خط مسیریابی، همان `Handler` را با یک «درگاه جعلی» صدا می‌زنیم:
  بدنهٔ درخواست در `rfile` و پاسخ در `wfile` — بعد پاسخ را به WSGI برمی‌گردانیم.

  نتیجه: **یک کد مسیریابی، دو میزبان** (محلی با `python3 -m leadgen.server` و ابری با
  `api/index.py`). هیچ قراردادی عوض نمی‌شود.

نکتهٔ عملیاتی: در حالت ابری، اجراها باید به صف بروند (`LEADGEN_QUEUE=always`) چون تابع
serverless نمی‌تواند چند دقیقه باز بماند؛ ورکرِ واقعی در Codespaces کار را برمی‌دارد.
"""
from __future__ import annotations

import io
import os
import sys
import urllib.parse
from email.message import Message
from pathlib import Path
from typing import Callable, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# هدایت داده/خروجی به مسیر نوشتنی — روی Vercel فقط /tmp نوشتنی است
os.environ.setdefault("LEADGEN_DATA_DIR", "/tmp/leadgen-data")

MAX_BODY = 32 * 1024 * 1024          # سقف بدنهٔ درخواست (وارد کردن CSV بزرگ)

_IGNORED_HEADERS = {"wsgi.input", "wsgi.errors", "wsgi.url_scheme", "wsgi.multithread",
                    "wsgi.multiprocess", "wsgi.run_once", "wsgi.version", "wsgi.errors",
                    "wsgi.file_wrapper"}

# هدرهایی که `BaseHTTPRequestHandler` می‌سازد ولی پروتکل WSGI اجازهٔ برگرداندشان را نمی‌دهد
# (میزبان serverless مثل Vercel با دیدنشان پاسخ را نامعتبر می‌داند → FUNCTION_INVOCATION_FAILED).
_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailers", "upgrade",
               "proxy-authenticate", "proxy-authorization", "server", "date",
               "content-length"}


def _headers_from_environ(environ: dict) -> Message:
    """هدرهای WSGI را به `email.Message` تبدیل می‌کند (همان نوع `self.headers`)."""
    message = Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            if name in ("Content-Type", "Content-Length"):     # این دو از خود environ می‌آیند
                continue
            message[name] = value
    if environ.get("CONTENT_TYPE"):
        message["Content-Type"] = environ["CONTENT_TYPE"]
    length = environ.get("CONTENT_LENGTH")
    if length:
        message["Content-Length"] = str(length)
    return message


def _body_from_environ(environ: dict) -> bytes:
    stream = environ.get("wsgi.input")
    if stream is None:
        return b""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > 0:
        return stream.read(min(length, MAX_BODY))
    try:
        return stream.read(MAX_BODY) or b""
    except Exception:
        return b""


# وقتی Vercel همهٔ مسیرها را به `/api/index` بازنویسی می‌کند، `PATH_INFO` همان مقصد می‌شود؛
# مسیر واقعی درخواست در این هدرها می‌آید (هر کدام بود).
_ORIGINAL_PATH_HEADERS = ("HTTP_X_VERCEL_ORIGINAL_PATH", "HTTP_X_MATCHED_PATH",
                          "HTTP_X_ORIGINAL_PATH", "HTTP_X_FORWARDED_URI")
_REWRITE_DESTINATIONS = ("/api/index", "/api/index/", "")


def _original_path(environ: dict) -> str | None:
    """مسیر واقعی درخواست را از هدرهای میزبان برمی‌گرداند (اگر بازنویسی شده باشد)."""
    path = environ.get("PATH_INFO") or "/"
    if path.rstrip("/") not in {d.rstrip("/") for d in _REWRITE_DESTINATIONS if d}:
        return None
    for key in _ORIGINAL_PATH_HEADERS:
        value = (environ.get(key) or "").strip()
        if not value:
            continue
        parsed = urllib.parse.urlsplit(value)
        candidate = parsed.path or "/"
        query = parsed.query
        if query:
            environ["QUERY_STRING"] = environ.get("QUERY_STRING") or query
        if candidate.startswith("/"):
            return candidate
    return None


def _full_path(environ: dict) -> str:
    """مسیر + کوئری به شکل ASCII-safe (همان چیزی که یک خط درخواست HTTP می‌پذیرد).

    هدرهایی مثل `x-vercel-original-path` ممکن است کوئری فارسیِ **رمزنگاری‌نشده** داشته باشند؛
    بدون quote کردن، ساخت `requestline` با latin-1 می‌شکند (UnicodeEncodeError).
    """
    path = _original_path(environ) or environ.get("PATH_INFO") or "/"
    if not path.startswith("/"):
        path = "/" + path
    path = urllib.parse.quote(path, safe="/%~")
    query = environ.get("QUERY_STRING") or ""
    if query:
        # «%» را نگه می‌داریم تا رشتهٔ از قبل رمز شده دوباره رمز نشود
        query = urllib.parse.quote(query, safe="=&%+")
        return f"{path}?{query}"
    return path


def _wsgi_status(status_line: str) -> str:
    """«HTTP/1.1 200 OK» → «200 OK».

    طبق PEP 3333 مقدار `status` در `start_response` باید «کد + دلیل» باشد و **بدون** پیشوند
    پروتکل. میزبان‌های سخت‌گیر (Vercel/Cloud Run) در غیر این صورت پاسخ را رد می‌کنند.
    """
    parts = [part for part in (status_line or "").strip().split(" ", 2) if part]
    if parts and parts[0].upper().startswith("HTTP/"):
        parts = parts[1:]
    if not parts or not parts[0].isdigit():
        return "500 Internal Server Error"
    if len(parts) == 1:
        parts.append(_REASONS.get(parts[0], "OK"))
    return f"{parts[0]} {parts[1]}"


_REASONS = {"200": "OK", "201": "Created", "202": "Accepted", "204": "No Content",
            "301": "Moved Permanently", "302": "Found", "304": "Not Modified",
            "400": "Bad Request", "401": "Unauthorized", "403": "Forbidden",
            "404": "Not Found", "405": "Method Not Allowed", "409": "Conflict",
            "413": "Payload Too Large", "422": "Unprocessable Entity",
            "500": "Internal Server Error", "502": "Bad Gateway",
            "503": "Service Unavailable", "504": "Gateway Timeout"}


def _wsgi_headers(headers: list[tuple[str, str]], payload: bytes) -> list[tuple[str, str]]:
    """هدرهای hop-by-hop را حذف و `Content-Length` درست را با بدنه هم‌تراز می‌کند."""
    out = [(name, value) for name, value in headers
           if name.strip().lower() not in _HOP_BY_HOP]
    out.append(("Content-Length", str(len(payload))))
    return out


def _parse_response(raw: bytes) -> tuple[str, list[tuple[str, str]], bytes]:
    """پاسخ خامِ نوشته‌شده در `wfile` را به (status, headers, body) تجزیه می‌کند."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status_line = lines[0].decode("latin-1") if lines else "HTTP/1.1 500 Empty response"
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.decode("latin-1").partition(":")
        headers.append((name.strip(), value.strip()))
    return _wsgi_status(status_line), _wsgi_headers(headers, body), body


class _FakeHandler:
    """`Handler` را بدون سوکت اجرا می‌کند (فقط rfile/wfile)."""

    def __init__(self, handler_class, environ: dict, body: bytes):
        self.handler = handler_class.__new__(handler_class)
        self.handler.rfile = io.BytesIO(body)
        self.handler.wfile = io.BytesIO()
        self.handler.path = _full_path(environ)
        self.handler.headers = _headers_from_environ(environ)
        self.handler.command = (environ.get("REQUEST_METHOD") or "GET").upper()
        self.handler.request_version = "HTTP/1.1"
        self.handler.protocol_version = "HTTP/1.1"
        self.handler.client_address = (environ.get("REMOTE_ADDR") or "127.0.0.1", 0)
        self.handler.server = None
        self.handler.connection = None
        self.handler.close_connection = True
        self.handler._headers_buffer = []      # BaseHTTPRequestHandler در py3 لازم دارد
        # `log_request` این‌ها را می‌خواند؛ بدون آن‌ها send_response استثنا می‌دهد
        self.handler.requestline = f"{self.handler.command} {self.handler.path} HTTP/1.1"
        self.handler.raw_requestline = self.handler.requestline.encode("utf-8", "replace")

    def run(self) -> bytes:
        method = getattr(self.handler, f"do_{self.handler.command}", None)
        if method is None:
            self.handler.send_response(405)
            self.handler.send_header("Content-Type", "application/json; charset=utf-8")
            self.handler.end_headers()
            self.handler.wfile.write(
                b'{"error": "method not allowed: ' + self.handler.command.encode() + b'"}')
            return self.handler.wfile.getvalue()
        method()
        return self.handler.wfile.getvalue()


def application(environ: dict, start_response: Callable) -> Iterable[bytes]:
    """تابع WSGI — نقطهٔ ورود میزبان‌های ابری."""
    from leadgen import server as server_mod

    body = _body_from_environ(environ)
    try:
        raw = _FakeHandler(server_mod.Handler, environ, body).run()
    except Exception as exc:                   # آخرین تور ایمنی (تابع نباید ۵۰۰ خام بدهد)
        import json
        import traceback
        payload = json.dumps({"error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc().splitlines()[-6:]},
                             ensure_ascii=False).encode("utf-8")
        start_response("500 Internal Server Error",
                       [("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(payload)))])
        return [payload]

    status_line, headers, payload = _parse_response(raw)
    start_response(status_line, headers)
    return [payload] if payload else [b""]


# نام مستعار رایج (Vercel/Flask-style)
app = application


def handler(environ, start_response):            # pragma: no cover - سازگاری میزبان‌های دیگر
    return application(environ, start_response)


def make_environ(method: str = "GET", path: str = "/", query: str = "", body: bytes = b"",
                 content_type: str = "", headers: dict | None = None) -> dict:
    """ساخت یک environ کامل — برای آزمون محلی بدون شبکه."""
    environ = {
        "REQUEST_METHOD": method.upper(), "PATH_INFO": path, "QUERY_STRING": query,
        "SERVER_NAME": "localhost", "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0), "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr, "wsgi.multithread": False, "wsgi.multiprocess": False,
        "wsgi.run_once": False, "REMOTE_ADDR": "127.0.0.1",
    }
    if body:
        environ["CONTENT_LENGTH"] = str(len(body))
    if content_type:
        environ["CONTENT_TYPE"] = content_type
    for key, value in (headers or {}).items():
        environ["HTTP_" + key.upper().replace("-", "_")] = value
    return environ


def call(method: str = "GET", path: str = "/", query: str = "", body: bytes = b"",
         content_type: str = "", headers: dict | None = None) -> tuple[int, dict, bytes]:
    """فراخوانی مستقیم برنامه (بدون سرور) — برای آزمون و دیباگ.

    خروجی: `(status_code, headers_dict, body_bytes)`
    """
    captured: dict = {}

    def start_response(status: str, response_headers, exc_info=None):
        # «200 OK» → 200 (همان قالبی که میزبان‌های WSGI می‌گیرند)
        captured["status"] = int(str(status).split(" ", 1)[0])
        captured["status_line"] = status
        captured["headers"] = {k.lower(): v for k, v in response_headers}

    chunks = list(application(make_environ(method, path, query, body, content_type, headers),
                              start_response))
    return captured.get("status", 500), captured.get("headers", {}), b"".join(chunks)
