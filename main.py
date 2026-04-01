from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from datetime import datetime, timezone
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from parser_reports import parse_report
from sheets_writer import append_row

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vk_sheet")

app = FastAPI()


def _vk_confirmation() -> str:
    return os.environ.get("VK_CALLBACK_CONFIRMATION", "")


def _vk_secret_expected() -> str | None:
    s = os.environ.get("VK_CALLBACK_SECRET", "").strip()
    return s or None


def _sheet_range() -> str:
    return os.environ.get("GOOGLE_SHEETS_RANGE", "Лист1!A:L")


def _sheet_text_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if re.match(r"^\d{1,2}[\./]\d{1,2}([\./]\d{2,4})?$", s):
        return "'" + s.replace("/", ".")
    return s


def _vk_message_dict(obj: Any) -> dict:
    if not isinstance(obj, dict):
        return {}
    m = obj.get("message")
    if isinstance(m, dict):
        return m
    if obj.get("text") is not None or obj.get("from_id") is not None:
        return obj
    return {}


def _parsed_to_row(parsed: dict, from_id: int, received_iso: str) -> list[str]:
    rt = parsed.get("report_type") or "unknown"
    if rt == "moderation":
        h = parsed.get("playtime_hours")
        pd = (parsed.get("playtime_display") or "").strip()
        time_cell = pd or (f"{h} часов" if h else "")
        pun = parsed.get("punishments_total") or parsed.get("punishments_note") or ""
        return [
            str(parsed.get("nick") or ""),
            _sheet_text_date(str(parsed.get("report_date") or "")),
            time_cell,
            str(pun).strip(),
            str(parsed.get("modes") or ""),
        ]
    if rt == "vacation":
        # Отгулы/отпуска не пишем в эту таблицу (у неё формат только A:E)
        return []
    return []


@app.get("/")
def health() -> dict:
    return {"ok": True}


async def _vk_body_dict(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    text = raw.decode("utf-8-sig").strip()
    if not text:
        return {}
    try:
        if text.startswith("{"):
            return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        q = parse_qs(text, keep_blank_values=True)
        if "json" in q and q["json"]:
            return json.loads(q["json"][0])
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("vk body: %r", text[:400])
        raise


@app.api_route("/vk/callback", methods=["GET", "POST"], response_model=None)
async def vk_callback(request: Request):
    if request.method == "GET":
        return Response(content=b"ok", media_type="text/plain", status_code=200)

    try:
        body = await _vk_body_dict(request)
    except json.JSONDecodeError:
        return PlainTextResponse("bad json", status_code=400)

    event_type = body.get("type")

    if event_type == "confirmation":
        code = _vk_confirmation()
        if not code:
            log.error("no VK_CALLBACK_CONFIRMATION")
            return PlainTextResponse("", status_code=500)
        return Response(
            content=code.encode("utf-8"),
            media_type="text/plain",
            status_code=200,
        )

    secret_expected = _vk_secret_expected()
    if secret_expected:
        got = str(body.get("secret", "")).strip()
        if got != secret_expected:
            log.warning(
                "secret mismatch: got_len=%s expected_len=%s (проверь VK_CALLBACK_SECRET и поле «Секретный ключ» в Callback API)",
                len(got),
                len(secret_expected),
            )
            return PlainTextResponse("forbidden", status_code=403)

    if event_type != "message_new":
        return PlainTextResponse("ok")

    obj = body.get("object") or {}
    msg = _vk_message_dict(obj)
    if msg.get("out"):
        return PlainTextResponse("ok")

    text = (msg.get("text") or "").strip()
    from_id = int(msg.get("from_id") or 0)

    if not text:
        log.info("skip: empty text from_id=%s", from_id)
        return PlainTextResponse("ok")

    t0 = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    parsed = parse_report(text)
    row = _parsed_to_row(parsed, from_id, t0)
    if not row:
        log.info(
            "skip sheet: parse=%s from_id=%s text_sample=%r",
            parsed.get("report_type"),
            from_id,
            text[:120],
        )
        return PlainTextResponse("ok")

    try:
        append_row(_sheet_range(), row)
    except Exception:
        log.exception("sheet")
        return PlainTextResponse("sheet err", status_code=500)

    log.info("sheet ok from_id=%s nick=%r", from_id, row[0])
    return PlainTextResponse("ok")
