#!/usr/bin/env python3
"""گرفتن `GMAIL_REFRESH_TOKEN` برای ارسال گزارش لیدها از راه Gmail API.

چرا این اسکریپت؟ روی Vercel (و هر محیط serverless) خروجی SMTP بسته است؛ تنها راه
ارسال ایمیل، Gmail API با یک refresh token است. این اسکریپت همان کاری را می‌کند که
یک‌بار دستی باید انجام دهید و هیچ وابستگی بیرونی ندارد (فقط کتابخانهٔ استاندارد).

پیش‌نیاز: در console.cloud.google.com
  1) یک پروژه بسازید و «Gmail API» را فعال کنید.
  2) OAuth consent screen → External → نام برنامه + ایمیل خودتان را در
     «Test users» اضافه کنید.
  3) Credentials → Create credentials → OAuth client ID → **Desktop app**.
  4) Client ID و Client secret را در `.env` بگذارید:
        GMAIL_CLIENT_ID=....apps.googleusercontent.com
        GMAIL_CLIENT_SECRET=GOCSPX-...
        GMAIL_SENDER=you@gmail.com

اجرا:
    python3 scripts/gmail_token.py            # مرورگر باز می‌شود و کد را خودش می‌گیرد
    python3 scripts/gmail_token.py --manual   # فقط نشانی را چاپ کن، کد را دستی بده
    python3 scripts/gmail_token.py --test     # توکن موجود را آزمایش کن (بدون ارسال)
    python3 scripts/gmail_token.py --send-test you@gmail.com   # یک ایمیل آزمایشی بفرست

خروجی موفق، خطی مثل این چاپ می‌کند که باید در `.env` (یا متغیرهای Vercel) بگذارید:
    GMAIL_REFRESH_TOKEN=1//0g...
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.compose"
REDIRECT_PATH = "/oauth2callback"


def load_env_file() -> dict:
    """`.env` را می‌خواند (بدون نیاز به python-dotenv)."""
    path = Path(__file__).resolve().parent.parent / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def credentials(explicit: dict | None = None) -> dict:
    from leadgen import config
    file_values = load_env_file()
    client_id = (explicit or {}).get("client_id") or config.env("GMAIL_CLIENT_ID") \
        or file_values.get("GMAIL_CLIENT_ID", "")
    client_secret = (explicit or {}).get("client_secret") or config.env("GMAIL_CLIENT_SECRET") \
        or file_values.get("GMAIL_CLIENT_SECRET", "")
    sender = config.env("GMAIL_SENDER") or file_values.get("GMAIL_SENDER", "")
    refresh = config.env("GMAIL_REFRESH_TOKEN") or file_values.get("GMAIL_REFRESH_TOKEN", "")
    if not (client_id and client_secret):
        raise SystemExit(
            "❌ GMAIL_CLIENT_ID و GMAIL_CLIENT_SECRET لازم است.\n"
            "   در console.cloud.google.com یک OAuth Client از نوع «Desktop app» بسازید\n"
            "   و مقدارها را در .env بگذارید (بالای همین فایل توضیح داده شده است).")
    return {"client_id": client_id, "client_secret": client_secret,
            "sender": sender, "refresh_token": refresh}


def post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"❌ HTTP {exc.code} از {url}:\n{detail[:800]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"❌ اتصال به گوگل برقرار نشد: {getattr(exc, 'reason', exc)}\n"
                         "   (در محیط‌های بدون اینترنت، این اسکریپت را روی لپ‌تاپ اجرا کنید.)") from exc


def auth_url(client_id: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent"})


def capture_code(port: int, redirect_path: str = REDIRECT_PATH) -> tuple[str, str]:
    """یک سرور موقت روی localhost بالا می‌آورد و کد را از redirect می‌گیرد."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != redirect_path:
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            captured["code"] = (params.get("code") or [""])[0]
            captured["error"] = (params.get("error") or [""])[0]
            body = ("<html dir='rtl'><meta charset='utf-8'>"
                    "<body style='font-family:Tahoma,sans-serif;padding:40px;text-align:center'>"
                    "<h2>✅ کد دریافت شد</h2><p>می‌توانید این پنجره را ببندید و به ترمینال برگردید.</p>"
                    "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    redirect_uri = f"http://localhost:{port}{redirect_path}"
    print(f"▶ منتظر ورود به حساب گوگل… (redirect: {redirect_uri})")
    while "code" not in captured and "error" not in captured:
        server.handle_request()
    server.server_close()
    if captured.get("error"):
        raise SystemExit(f"❌ گوگل خطا داد: {captured['error']}")
    return captured["code"], redirect_uri


def exchange(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return post_form(TOKEN_URL, {"code": code, "client_id": client_id,
                                 "client_secret": client_secret, "redirect_uri": redirect_uri,
                                 "grant_type": "authorization_code"})


def access_token(creds: dict) -> str:
    if not creds.get("refresh_token"):
        raise SystemExit("❌ GMAIL_REFRESH_TOKEN در .env نیست؛ اول همین اسکریپت را اجرا کنید.")
    data = post_form(TOKEN_URL, {"client_id": creds["client_id"],
                                 "client_secret": creds["client_secret"],
                                 "refresh_token": creds["refresh_token"],
                                 "grant_type": "refresh_token"})
    token = data.get("access_token")
    if not token:
        raise SystemExit(f"❌ access_token گرفته نشد: {data}")
    return token


def api_get(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"❌ HTTP {exc.code}:\n{detail[:600]}") from exc


def send_test_email(creds: dict, to: str) -> dict:
    from leadgen import delivery
    message = EmailMessage()
    message["From"] = creds["sender"] or to
    message["To"] = to
    message["Subject"] = "آزمایش LeadGen — اتصال Gmail برقرار است"
    message.set_content("سلام،\n\nاین یک ایمیل آزمایشی از LeadGen است.\n"
                        "اگر این پیام را می‌بینید، گزارش لیدهای داغ هم از همین مسیر "
                        "ارسال خواهد شد.\n\n— LeadGen")
    raw = base64.urlsafe_b64encode(bytes(message)).decode("ascii").rstrip("=")
    token = access_token(creds)
    request = urllib.request.Request(SEND_URL, data=json.dumps({"raw": raw}).encode("utf-8"),
                                     method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"❌ ارسال ناموفق (HTTP {exc.code}):\n{detail[:600]}\n"
                         "   اگر «unauthorized_sender» دیدید، GMAIL_SENDER باید همان حسابی باشد "
                         "که توکن برایش گرفته شده.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="گرفتن refresh token برای Gmail API")
    parser.add_argument("--port", type=int, default=8085, help="پورت بازگشت محلی (پیش‌فرض 8085)")
    parser.add_argument("--manual", action="store_true",
                        help="سرور محلی بالا نیاور؛ فقط نشانی را چاپ کن و کد را از آرگومان بگیر")
    parser.add_argument("--code", default="", help="کد authorization (در حالت --manual)")
    parser.add_argument("--redirect-uri", default="", help="redirect_uri ثبت‌شده در کنسول گوگل")
    parser.add_argument("--client-id", default="", help="جای‌گزین مقدار .env")
    parser.add_argument("--client-secret", default="", help="جای‌گزین مقدار .env")
    parser.add_argument("--no-browser", action="store_true", help="مرورگر را باز نکن")
    parser.add_argument("--test", action="store_true", help="توکن موجود را آزمایش کن")
    parser.add_argument("--send-test", metavar="EMAIL", default="",
                        help="یک ایمیل آزمایشی به این نشانی بفرست")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    creds = credentials({"client_id": args.client_id, "client_secret": args.client_secret})

    if args.test:
        profile = api_get(PROFILE_URL, access_token(creds))
        print(f"✔ توکن معتبر است — حساب: {profile.get('emailAddress')} "
              f"(messages: {profile.get('messagesTotal')})")
        return 0

    if args.send_test:
        result = send_test_email(creds, args.send_test)
        print(f"✔ ایمیل آزمایشی ارسال شد — id: {result.get('id')} thread: {result.get('threadId')}")
        return 0

    if args.manual or args.code:
        redirect_uri = args.redirect_uri or f"http://localhost:{args.port}{REDIRECT_PATH}"
        if not args.code:
            print("این نشانی را در مرورگر باز کنید، اجازه دهید و کد را از نشانی بازگشتی "
                  "کپی کنید:\n")
            print(auth_url(creds["client_id"], redirect_uri))
            print(f"\n(redirect_uri: {redirect_uri})")
            args.code = input("\nکد authorization را بچسبانید: ").strip()
        data = exchange(args.code, creds["client_id"], creds["client_secret"], redirect_uri)
    else:
        code, redirect_uri = None, ""
        print("نکته: در کنسول گوگل، «Authorized redirect URIs» را خالی بگذارید (کلاینت Desktop)\n"
              "      یا دقیقاً همین را ثبت کنید: "
              f"http://localhost:{args.port}{REDIRECT_PATH}")
        url = auth_url(creds["client_id"], f"http://localhost:{args.port}{REDIRECT_PATH}")
        if not args.no_browser:
            try:
                webbrowser.open(url)
                print("▶ مرورگر باز شد؛ حساب گوگل را انتخاب و اجازه دهید…")
            except Exception:
                args.no_browser = True
        if args.no_browser:
            print("\nاین نشانی را دستی در مرورگر باز کنید:\n" + url + "\n")
        code, redirect_uri = capture_code(args.port)
        data = exchange(code, creds["client_id"], creds["client_secret"], redirect_uri)

    refresh = data.get("refresh_token")
    if not refresh:
        raise SystemExit("❌ گوگل refresh_token نداد.\n"
                         "   علت معمول: حساب قبلاً اجازه داده است. در "
                         "myaccount.google.com/permissions دسترسی برنامه را حذف کنید\n"
                         "   و دوباره اجرا کنید (پارامتر prompt=consent هم گذاشته شده است).")

    print("\n✔ توکن گرفته شد. این خط را در .env (و در متغیرهای محیطی Vercel) بگذارید:\n")
    print(f"GMAIL_REFRESH_TOKEN={refresh}")
    print(f"\naccess_token فعلی: {str(data.get('access_token'))[:24]}… "
          f"(expires_in {data.get('expires_in')} ثانیه)")
    print("\nقدم بعدی:  python3 scripts/gmail_token.py --test")
    print("          python3 -m leadgen.delivery --preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
