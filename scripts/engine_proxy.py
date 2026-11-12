#!/usr/bin/env python3
"""پراکسی احراز هویت برای موتور اسکراپر (gosom/google-maps-scraper).

چرا لازم است؟
  خودِ موتور هیچ احراز هویتی ندارد و به‌همین دلیل در docker-compose روی
  `127.0.0.1` بسته شده است. اما در Codespaces گاهی می‌خواهید موتور را از جای دیگری
  (لپ‌تاپ، یک ورکر دیگر، یا یک پیش‌نمایش عمومی) صدا بزنید. این اسکریپت یک لایهٔ نازک
  `X-API-Key` جلوی موتور می‌گذارد و بقیهٔ درخواست‌ها را بدون تغییر به آن می‌سپارد.

  ⚠ این پراکسی رمزنگاری اضافه نمی‌کند؛ فقط احراز هویت. برای بیرون از Codespace حتماً
    از forward port با HTTPS خودِ Codespaces استفاده کنید، نه باز کردن پورت خام.

استفاده:
    python3 scripts/engine_proxy.py --port 8081 --api-key "$SCRAPER_API_KEY"
    SCRAPER_BASE_URL=https://<codespace>-8081.app.github.dev SCRAPER_API_KEY=... \
        python3 -m leadgen.worker --once

  سپس در سمت کلاینت، همان `SCRAPER_BASE_URL` + `SCRAPER_API_KEY` را در `.env` بگذارید
  (موتورِ `leadgen.engine.DockerEngine` هدر `X-API-Key` را خودش می‌فرستد).

بدون هیچ وابستگی بیرونی — فقط کتابخانهٔ استاندارد پایتون.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
              "x-api-key"}
MAX_BODY = 32 * 1024 * 1024          # CSV خروجی موتور می‌تواند بزرگ باشد


class ProxyError(RuntimeError):
    pass


def make_handler(upstream: str, api_key: str, verbose: bool = False):
    upstream = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "leadgen-engine-proxy/1.0"

        # ── ابزارها ──────────────────────────────────────────────────────────
        def log_message(self, fmt: str, *args) -> None:
            if verbose:
                sys.stderr.write("[proxy] " + (fmt % args) + "\n")

        def _authorized(self) -> bool:
            if not api_key:
                return True                       # بدون کلید = فقط localhost (مثل موتور)
            given = self.headers.get("X-API-Key") or ""
            if not given:
                auth = self.headers.get("Authorization") or ""
                if auth.lower().startswith("bearer "):
                    given = auth.split(" ", 1)[1].strip()
            return secrets.compare_digest(given, api_key)

        def _reply(self, status: int, body: bytes, content_type: str = "application/json",
                   extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _forward(self, method: str) -> None:
            if not self._authorized():
                self._json(401, {"error": "unauthorized",
                                 "hint": "هدر X-API-Key (یا Authorization: Bearer) لازم است"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            if body and len(body) > MAX_BODY:
                self._json(413, {"error": "payload too large"})
                return

            url = upstream + self.path
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            request = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=1800) as response:
                    payload = response.read()
                    extra = {k: v for k, v in response.headers.items()
                             if k.lower() not in HOP_BY_HOP}
                    self._reply(response.status, payload,
                                response.headers.get("Content-Type") or "application/octet-stream",
                                extra)
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                self._reply(exc.code, payload,
                            exc.headers.get("Content-Type") or "application/json" if exc.headers
                            else "application/json")
            except urllib.error.URLError as exc:
                self._json(502, {"error": "upstream unreachable",
                                 "detail": str(getattr(exc, "reason", exc)),
                                 "hint": "آیا موتور بالا است؟ docker compose up -d"})
            except TimeoutError:
                self._json(504, {"error": "upstream timeout"})

        # ── مسیرها ───────────────────────────────────────────────────────────
        def do_GET(self):
            if self.path in ("/", "/healthz", "/_proxy"):
                self._json(200, {"proxy": "leadgen-engine-proxy", "upstream": upstream,
                                 "auth": bool(api_key),
                                 "hint": "مسیرهای موتور: /api/v1/jobs، /api/v1/jobs/{id}"})
                return
            self._forward("GET")

        def do_HEAD(self):
            self._forward("HEAD")

        def do_POST(self):
            self._forward("POST")

        def do_PUT(self):
            self._forward("PUT")

        def do_PATCH(self):
            self._forward("PATCH")

        def do_DELETE(self):
            self._forward("DELETE")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="پراکسی X-API-Key برای موتور اسکراپر")
    parser.add_argument("--host", default=os.environ.get("PROXY_HOST", "0.0.0.0"),
                        help="آدرس bind (پیش‌فرض 0.0.0.0 برای forward شدن در Codespaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PROXY_PORT", "8081")))
    parser.add_argument("--upstream", default=os.environ.get("SCRAPER_LOCAL_URL",
                                                             "http://127.0.0.1:8080"),
                        help="آدرس محلی موتور (همان docker-compose)")
    parser.add_argument("--api-key", default=os.environ.get("SCRAPER_API_KEY", ""),
                        help="کلید مورد انتظار؛ خالی = بدون احراز هویت (فقط localhost)")
    parser.add_argument("--generate-key", action="store_true",
                        help="یک کلید تصادفی بساز و چاپ کن (برای .env)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.generate_key:
        print(secrets.token_urlsafe(32))
        return 0

    api_key = args.api_key
    if not api_key and args.host not in ("127.0.0.1", "localhost"):
        api_key = secrets.token_urlsafe(24)
        print(f"⚠ کلیدی داده نشد؛ یک کلید تصادفی ساخته شد. در .env بگذارید:\n"
              f"    SCRAPER_API_KEY={api_key}", file=sys.stderr)

    handler = make_handler(args.upstream, api_key, verbose=args.verbose)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    httpd.daemon_threads = True
    print(f"▶ پراکسی موتور روی http://{args.host}:{args.port} → {args.upstream} "
          f"(احراز هویت: {'روشن' if api_key else 'خاموش'})")
    print("  Ctrl+C برای توقف")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n■ پراکسی متوقف شد")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
