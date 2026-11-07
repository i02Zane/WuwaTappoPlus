"""Fixed-operation HTTPS API; anonymous credentials never reach page code."""

import json
import hashlib
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from .paths import DATA_DIR
from .plaza_auth import CredentialStore

DEFAULT_BASE = "https://api.i02zane.site/wuwa-tappo-plus/v1"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlazaError(Exception):
    def __init__(self, code="CONNECTION_FAILED", retry=0):
        self.code, self.retry = code, retry
        super().__init__(code)


def valid_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def valid_time(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


class PlazaClient:
    def __init__(self, base=DEFAULT_BASE, directory=DATA_DIR):
        url = urlsplit(base)
        if (url.scheme != "https" or not url.hostname or url.port not in (None, 443)
                or url.username or url.password or url.query or url.fragment
                or url.path != "/wuwa-tappo-plus/v1"):
            raise ValueError("Invalid plaza base")
        self.base = base
        self.credentials = CredentialStore(directory, base)
        self.opener = urllib.request.build_opener(NoRedirect)
        self.token, self.loaded = None, False
        self.lock = threading.Lock()
        self.retry_at = 0
        self.list_retry_at = 0
        self.write_retry_at = 0

    def http(self, method, path, body=None, key=None, generation=None, anonymous=False):
        headers = {"Accept": "application/json"}
        if not anonymous:
            headers["Authorization"] = "Bearer " + self.token
        if key:
            headers["Idempotency-Key"] = key
        if generation:
            headers["If-Match"] = generation
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=encoded, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        except Exception:
            raise PlazaError() from None
        try:
            with response:
                raw = response.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise PlazaError("INVALID_RESPONSE")
                retry = response.headers.get("Retry-After", "0")
                retry = min(3600, int(retry)) if str(retry).isdigit() else 30
                if response.status == 401:
                    raise PlazaError("UNAUTHORIZED")
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise PlazaError("INVALID_RESPONSE") from None
                if not isinstance(payload, dict):
                    raise PlazaError("INVALID_RESPONSE")
                if response.status not in (200, 201):
                    code = (payload.get("error") or {}).get("code", "CONNECTION_FAILED")
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,64}", code):
                        code = "CONNECTION_FAILED"
                    until = time.monotonic() + max(1, retry)
                    if code == "LIST_COOLDOWN":
                        self.list_retry_at = until
                    elif code == "WRITE_COOLDOWN":
                        self.write_retry_at = until
                    elif response.status in (429, 503) and code not in ("MAINTENANCE", "PUBLISHING_DISABLED"):
                        self.retry_at = until
                    raise PlazaError(code, retry)
                return payload
        except PlazaError:
            raise
        except Exception:
            raise PlazaError() from None

    def row(self, row, private=False):
        if not isinstance(row, dict) or not valid_uuid(row.get("id")):
            raise PlazaError("INVALID_RESPONSE")
        room = row.get("room_code")
        if not isinstance(room, str) or not re.fullmatch(r"[0-9A-Za-z]{4,16}", room):
            raise PlazaError("INVALID_RESPONSE")
        result = {"id": row["id"], "room_code": room}
        for field in ("published_at", "updated_at", "expires_at"):
            if not valid_time(row.get(field)):
                raise PlazaError("INVALID_RESPONSE")
            result[field] = row[field]
        if private:
            if not valid_uuid(row.get("generation")):
                raise PlazaError("INVALID_RESPONSE")
            result["generation"] = row["generation"]
        return result

    def send(self, item):
        op, args = item.get("op"), item.get("args") or {}
        allowed = {"list": {"page", "refresh"}, "own": set(), "publish": {"room_code", "generation", "key"},
                   "withdraw": {"generation", "key"}, "reset": set()}
        if op not in allowed or not isinstance(args, dict) or set(args) - allowed[op] or set(item) - {"id", "op", "args"}:
            raise PlazaError("INVALID_ACTION")
        if op == "list":
            page, refresh = args.get("page", 0), args.get("refresh", True)
            if type(page) is not int or not 0 <= page <= 49 or type(refresh) is not bool:
                raise PlazaError("INVALID_ACTION")
        if op in ("publish", "withdraw"):
            if not valid_uuid(args.get("key")) or (args.get("generation") is not None and not valid_uuid(args["generation"])):
                raise PlazaError("INVALID_ACTION")
            if op == "withdraw" and args.get("generation") is None:
                raise PlazaError("INVALID_ACTION")
            if op == "publish" and (not isinstance(args.get("room_code"), str) or not re.fullmatch(r"[0-9A-Za-z]{4,16}", args["room_code"])):
                raise PlazaError("INVALID_ACTION")
        with self.lock:
            deadline = max(self.retry_at, self.list_retry_at if op == "list" else self.write_retry_at if op == "publish" else 0)
            if time.monotonic() < deadline:
                raise PlazaError("RATE_LIMITED", max(1, int(deadline - time.monotonic()) + 1))
            try:
                if op == "reset":
                    self.credentials.clear()
                    self.token, self.loaded = None, False
                    return {}
                if not self.loaded:
                    self.token = self.credentials.load()
                    self.loaded = True
                if self.token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{43}", self.token):
                    raise PlazaError("IDENTITY_STORAGE_ERROR")
                if self.token is None:
                    result = self.http("POST", "/sessions", {}, anonymous=True)
                    token = result.get("token", "")
                    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                        raise PlazaError("INVALID_RESPONSE")
                    self.credentials.save(token)
                    self.token = token
            except PlazaError:
                raise
            except Exception:
                raise PlazaError("IDENTITY_STORAGE_ERROR") from None
            if op == "list":
                payload = self.http("GET", "/rooms?page=%d&refresh=%d" % (page, refresh))
                if (not isinstance(payload.get("items"), list) or len(payload["items"]) > 20
                        or type(payload.get("page")) is not int or payload["page"] != page
                        or type(payload.get("has_more")) is not bool or not valid_time(payload.get("next_refresh_at"))):
                    raise PlazaError("INVALID_RESPONSE")
                result = {"items": [self.row(row) for row in payload["items"]], "page": page,
                          "has_more": payload["has_more"], "next_refresh_at": payload["next_refresh_at"]}
            else:
                if op == "own":
                    payload = self.http("GET", "/me/room")
                elif op == "publish":
                    payload = self.http("PUT", "/me/room", {"room_code": args["room_code"], "generation": args.get("generation")}, key=args["key"])
                else:
                    payload = self.http("DELETE", "/me/room", key=args["key"], generation=args["generation"])
                if "item" not in payload or (op == "publish" and payload["item"] is None):
                    raise PlazaError("INVALID_RESPONSE")
                result = {"item": self.row(payload["item"], True) if payload["item"] is not None else None}
            if not valid_time(payload.get("server_time")):
                raise PlazaError("INVALID_RESPONSE")
            return {**result, "server_time": payload["server_time"],
                    "identity_epoch": hashlib.sha256(("wuwa-plaza-local-binding:" + self.token).encode("ascii")).hexdigest()}
