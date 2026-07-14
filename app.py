"""Interview Copilot — accounts backend (Render-ready).

Token-based (JWT) auth API designed to run on Render (free tier) with Postgres,
and be called cross-origin by the static front-end on PythonAnywhere.

Features
  - Google Sign-In: browser gets a Google ID token, posts it here, we verify it
    with Google's library, upsert the member, and return our own session JWT.
    (Google has already verified the email, so this needs NO email sending.)
  - Email + verification code (인증번호) signup/login: we email a 6-digit code
    (SMTP, e.g. a Gmail app password) and verify it. Optional — only enabled when
    SMTP_* env vars are set.
  - Member management: GET /api/admin/members (behind ADMIN_KEY) lists members.
  - Plan field per member (free/pro) for later payment gating (Ko-fi/Stripe webhook).

Persistence: SQLAlchemy. Uses DATABASE_URL (Render Postgres) in prod, falls back
to a local SQLite file for dev. Render's free web disk is ephemeral, so Postgres
is required in prod (a free Render Postgres instance works).

Env vars (set in Render dashboard):
  DATABASE_URL        Render Postgres URL (auto-injected if you attach a DB)
  JWT_SECRET          long random string (session token signing)
  GOOGLE_CLIENT_ID    OAuth 2.0 Web client ID from Google Cloud Console
  CORS_ORIGINS        comma list, e.g. https://geoinfomatic.pythonanywhere.com
  ADMIN_KEY           secret for /api/admin/* (send as X-Admin-Key header)
  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM   (optional, for email codes)
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import random
import smtplib
import ssl
from email.message import EmailMessage
from functools import wraps

import jwt
from flask import Flask, g, jsonify, request
from flask_cors import CORS
from sqlalchemy import (Column, DateTime, Integer, String, create_engine, func,
                        select)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------- config
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
# Fail closed in production: on Render (RENDER env var is always set there) a
# missing JWT_SECRET would let anyone forge admin tokens with the known dev
# default — refuse to boot instead.
if os.environ.get("RENDER") and not os.environ.get("JWT_SECRET"):
    raise RuntimeError("JWT_SECRET must be set in production")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
# Optional: restrict which Ko-fi membership tiers grant Pro (comma-separated
# tier names). Empty = any subscription payment counts (fine with one tier).
KOFI_PRO_TIERS = {t.strip() for t in os.environ.get("KOFI_PRO_TIERS", "").split(",") if t.strip()}
# Logged-in members whose email is here can use /api/admin/* with just their
# session token (no separate ADMIN_KEY) — so the owner who signed in with Google
# sees the dashboard without pasting a key.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# Ko-fi webhook (ko-fi.com/manage/webhooks): paste that page's Verification Token
# here so /api/webhook/kofi can auto-activate Pro on membership payments.
# Fail-closed: with no token set the endpoint refuses everything.
KOFI_TOKEN = os.environ.get("KOFI_VERIFICATION_TOKEN", "")
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:8771,https://geoinfomatic.pythonanywhere.com"
).split(",") if o.strip()]

SMTP = {
    "host": os.environ.get("SMTP_HOST", ""),
    "port": int(os.environ.get("SMTP_PORT", "587")),
    "user": os.environ.get("SMTP_USER", ""),
    "pw": os.environ.get("SMTP_PASS", ""),
    "from": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
}
EMAIL_ENABLED = bool(SMTP["host"] and SMTP["user"] and SMTP["pw"])

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///accounts.db")
# Render gives postgres://… ; SQLAlchemy wants postgresql+psycopg2://
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg2://", 1)

engine = create_engine(DB_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    google_sub = Column(String(64), index=True)   # Google account id (if google login)
    pw_salt = Column(String(64))                   # for email/password members
    pw_hash = Column(String(128))
    email_verified = Column(Integer, default=0)
    plan = Column(String(16), default="free")
    pro_until = Column(DateTime)                   # Ko-fi grants expire (no cancel webhook); NULL = permanent/manual
    provider = Column(String(16), default="email")  # email | google | kofi
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime)


class Code(Base):
    __tablename__ = "email_codes"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=False)
    code_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0)


Base.metadata.create_all(engine)
# Lightweight migration: create_all never ALTERs existing tables, so add the
# pro_until column in place on already-deployed DBs (no-op once present).
try:
    with engine.begin() as _c:
        _c.exec_driver_sql("ALTER TABLE users ADD COLUMN pro_until TIMESTAMP")
except Exception:
    pass  # column already exists (or dialect quirk) — model stays the source of truth


def effective_plan(u):
    """Stored plan, downgraded when a Ko-fi grant has lapsed (NULL = permanent)."""
    if u.plan == "pro" and u.pro_until and u.pro_until < dt.datetime.utcnow():
        return "free"
    return u.plan or "free"

app = Flask(__name__)
CORS(app, origins=CORS_ORIGINS, supports_credentials=False)


# ---------------------------------------------------------------- helpers
def db():
    if "db" not in g:
        g.db = Session()
    return g.db


@app.teardown_appcontext
def _close(exc):
    s = g.pop("db", None)
    if s is not None:
        s.close()


def make_token(user):
    payload = {
        "uid": user.id, "email": user.email,
        "exp": dt.datetime.utcnow() + dt.timedelta(days=30),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        data = jwt.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None
    return db().get(User, data.get("uid"))


def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        u = current_user()
        if not u:
            return jsonify({"error": "로그인이 필요합니다."}), 401
        g.user = u
        return fn(*a, **k)
    return w


def user_json(u, token=None):
    out = {"email": u.email, "name": u.name, "plan": effective_plan(u), "provider": u.provider}
    if token:
        out["token"] = token
    return out


def _hash_code(email, code):
    return hashlib.sha256(f"{email}:{code}:{JWT_SECRET}".encode()).hexdigest()


def send_code_email(to, code):
    msg = EmailMessage()
    msg["Subject"] = "[Interview Copilot] 인증번호"
    msg["From"] = SMTP["from"]
    msg["To"] = to
    msg.set_content(f"인증번호는 {code} 입니다. 5분 안에 입력하세요.\n\n— Interview Copilot")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP["host"], SMTP["port"], timeout=15) as s:
        s.starttls(context=ctx)
        s.login(SMTP["user"], SMTP["pw"])
        s.send_message(msg)


def _valid_email(e):
    return isinstance(e, str) and "@" in e and "." in e.split("@")[-1] and len(e) <= 255


# ---------------------------------------------------------------- routes
APP_VERSION = "2026-06-30-kofi-webhook"


@app.route("/")
@app.route("/healthz")
def health():
    return jsonify({"ok": True, "email_enabled": EMAIL_ENABLED, "google": bool(GOOGLE_CLIENT_ID),
                    "kofi_webhook": bool(KOFI_TOKEN), "v": APP_VERSION})


@app.route("/api/auth/google", methods=["POST"])
def auth_google():
    """Body: {id_token}. Verify Google ID token, upsert member, return our JWT."""
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "구글 로그인이 설정되지 않았습니다."}), 503
    tok = (request.get_json(silent=True) or {}).get("id_token", "")
    try:
        from google.oauth2 import id_token as gtok
        from google.auth.transport import requests as greq
        info = gtok.verify_oauth2_token(tok, greq.Request(), GOOGLE_CLIENT_ID)
    except Exception as e:
        return jsonify({"error": f"구글 토큰 검증 실패: {e}"}), 401
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        return jsonify({"error": "구글 이메일 인증이 필요합니다."}), 401
    s = db()
    u = s.scalar(select(User).where(User.email == email))
    if not u:
        u = User(email=email, name=info.get("name"), google_sub=info.get("sub"),
                 provider="google", email_verified=1, plan="free")
        s.add(u)
    else:
        u.google_sub = info.get("sub")
        u.name = u.name or info.get("name")
        u.email_verified = 1
    u.last_login = dt.datetime.utcnow()
    s.commit()
    return jsonify(user_json(u, make_token(u)))


@app.route("/api/auth/email/start", methods=["POST"])
def email_start():
    """Body: {email}. Generate + email a 6-digit code."""
    if not EMAIL_ENABLED:
        return jsonify({"error": "이메일 인증이 설정되지 않았습니다(SMTP 미설정)."}), 503
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not _valid_email(email):
        return jsonify({"error": "올바른 이메일을 입력하세요."}), 400
    code = f"{random.randint(0, 999999):06d}"
    s = db()
    s.query(Code).filter(Code.email == email).delete()
    s.add(Code(email=email, code_hash=_hash_code(email, code),
               expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=5)))
    s.commit()
    try:
        send_code_email(email, code)
    except Exception as e:
        return jsonify({"error": f"메일 발송 실패: {e}"}), 502
    return jsonify({"sent": True})


@app.route("/api/auth/email/verify", methods=["POST"])
def email_verify():
    """Body: {email, code, name?}. Verify code → create/login member → JWT."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    s = db()
    rec = s.scalar(select(Code).where(Code.email == email))
    if not rec or rec.expires_at < dt.datetime.utcnow():
        return jsonify({"error": "인증번호가 만료되었거나 없습니다."}), 400
    rec.attempts += 1
    if rec.attempts > 6:
        s.query(Code).filter(Code.email == email).delete(); s.commit()
        return jsonify({"error": "시도 횟수를 초과했습니다. 다시 받으세요."}), 429
    if not hmac.compare_digest(rec.code_hash, _hash_code(email, code)):
        s.commit()
        return jsonify({"error": "인증번호가 올바르지 않습니다."}), 400
    s.query(Code).filter(Code.email == email).delete()
    u = s.scalar(select(User).where(User.email == email))
    if not u:
        u = User(email=email, name=data.get("name"), provider="email",
                 email_verified=1, plan="free")
        s.add(u)
    else:
        u.email_verified = 1
    u.last_login = dt.datetime.utcnow()
    s.commit()
    return jsonify(user_json(u, make_token(u)))


@app.route("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify({"anon": True})
    return jsonify(user_json(u))


def _is_admin():
    # (a) shared admin key header, or (b) a logged-in member on the email allowlist
    if ADMIN_KEY and hmac.compare_digest(
            request.headers.get("X-Admin-Key", "").encode("utf-8"), ADMIN_KEY.encode("utf-8")):
        return True
    u = current_user()
    return bool(u and u.email and u.email.lower() in ADMIN_EMAILS)


@app.route("/api/admin/members")
def admin_members():
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    s = db()
    rows = s.scalars(select(User).order_by(User.created_at.desc())).all()
    return jsonify({"count": len(rows), "members": [{
        "id": u.id, "email": u.email, "name": u.name, "plan": effective_plan(u),
        "pro_until": u.pro_until.isoformat() if u.pro_until else None,
        "provider": u.provider, "verified": bool(u.email_verified),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
    } for u in rows]})


@app.route("/api/admin/set-plan", methods=["POST"])
def admin_set_plan():
    """Body: {email, plan: free|pro}. Manually flip a member's plan (admin only)."""
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    plan = (data.get("plan") or "").strip().lower()
    if plan not in ("free", "pro"):
        return jsonify({"error": "plan must be 'free' or 'pro'"}), 400
    s = db()
    u = s.scalar(select(User).where(User.email == email))
    if not u:
        return jsonify({"error": "해당 이메일의 회원이 없습니다."}), 404
    u.plan = plan
    u.pro_until = None   # manual grants/downgrades are permanent (no auto-expiry)
    s.commit()
    return jsonify({"ok": True, "email": u.email, "plan": u.plan})


@app.route("/api/webhook/kofi", methods=["POST"])
def kofi_webhook():
    """Ko-fi payment webhook → auto-activate Pro.

    Ko-fi POSTs application/x-www-form-urlencoded with a 'data' field holding
    JSON (see ko-fi.com/manage/webhooks). We verify its verification_token
    against KOFI_VERIFICATION_TOKEN, then on any membership/subscription
    payment set plan='pro' for the member whose email matches the Ko-fi
    payment email (creating a stub member if they haven't logged in yet, so
    the upgrade is waiting for them when they do).
    """
    if not KOFI_TOKEN:
        return jsonify({"error": "webhook disabled (no KOFI_VERIFICATION_TOKEN)"}), 503
    try:
        payload = json.loads(request.form.get("data", "") or "{}")
    except Exception:
        return jsonify({"error": "bad payload"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "bad payload"}), 400
    tok = str(payload.get("verification_token", ""))
    if not hmac.compare_digest(tok.encode("utf-8"), KOFI_TOKEN.encode("utf-8")):
        return jsonify({"error": "forbidden"}), 403
    email = (payload.get("email") or "").strip().lower()
    typ = payload.get("type") or ""
    tier = payload.get("tier_name")
    is_sub = bool(payload.get("is_subscription_payment")) or typ == "Subscription"
    app.logger.info("kofi webhook: type=%s email=%s tier=%s msg=%s",
                    typ, email, tier, payload.get("message_id"))
    if not email or not is_sub or not _valid_email(email):
        return jsonify({"ok": True, "ignored": typ or "no email"})
    if KOFI_PRO_TIERS and (tier or "") not in KOFI_PRO_TIERS:
        return jsonify({"ok": True, "ignored": f"tier '{tier}' not in allowlist"})
    # Ko-fi has NO cancel/refund webhook, so grants must expire: each monthly
    # renewal re-arrives here and pushes pro_until forward again. Absolute
    # (now+35d), not additive — replays/retries stay idempotent.
    until = dt.datetime.utcnow() + dt.timedelta(days=35)

    def _grant(sess):
        u = sess.scalar(select(User).where(User.email == email))
        if not u:
            u = User(email=email, name=payload.get("from_name"), provider="kofi",
                     email_verified=1, plan="pro", pro_until=until)
            sess.add(u)
        else:
            u.plan = "pro"
            u.pro_until = until
        sess.commit()

    s = db()
    try:
        _grant(s)
    except Exception:
        # unique-email race (webhook retry vs signup): retry once on the update path
        s.rollback()
        _grant(s)
    return jsonify({"ok": True, "email": email, "plan": "pro", "pro_until": until.isoformat()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8800")), debug=False)
