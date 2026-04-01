from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Sequence

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

log = logging.getLogger("vk_sheet")

DEDUP_SHEET_DEFAULT = "__vk_dedup"


def _load_sheets_credentials():
    """Локально: GOOGLE_APPLICATION_CREDENTIALS=путь к json. На хостинге: GOOGLE_SERVICE_ACCOUNT_JSON или …_B64."""
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    b64 = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64") or "").strip()
    path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if b64:
        raw = base64.b64decode(b64).decode("utf-8")
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if path and os.path.isfile(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    raise RuntimeError(
        "Нужны учётные данные Google: GOOGLE_SERVICE_ACCOUNT_JSON, "
        "GOOGLE_SERVICE_ACCOUNT_JSON_B64 или GOOGLE_APPLICATION_CREDENTIALS (путь к файлу)"
    )


def _sheet_a1_prefix(sheet: str) -> str:
    s = sheet.strip()
    if re.search(r"[^A-Za-z0-9_\u0080-\uFFFF]", s) or "'" in s:
        return "'" + s.replace("'", "''") + "'"
    return s


def _parse_sheet_cols(range_a1: str) -> tuple[str, str, str]:
    s = (range_a1 or "").strip()
    if not s:
        raise ValueError("empty GOOGLE_SHEETS_RANGE")
    bang = s.find("!")
    if bang == -1:
        sheet = "Лист1"
        rest = s
    else:
        sheet = s[:bang].strip().strip("'\"")
        rest = s[bang + 1 :].strip()
    m = re.match(r"^([A-Za-z]+)\s*:\s*([A-Za-z]+)$", rest)
    if m:
        return sheet, m.group(1).upper(), m.group(2).upper()
    m = re.match(r"^([A-Za-z]+)\d+\s*:\s*([A-Za-z]+)\d+$", rest)
    if m:
        return sheet, m.group(1).upper(), m.group(2).upper()
    raise ValueError(
        f"Unsupported GOOGLE_SHEETS_RANGE {range_a1!r}; "
        "use e.g. Отчеты!A:E or Отчеты!A1:E5000"
    )


def _col_letters_to_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"bad column {letters!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _column_span_width(c1: str, c2: str) -> int:
    return _col_letters_to_index(c2) - _col_letters_to_index(c1) + 1


def _next_data_row(rows: list[list[Any]]) -> int:
    last = 0
    for i, row in enumerate(rows, start=1):
        if row and any(str(c).strip() for c in row):
            last = i
    return last + 1


def _sheet_id_and_row_count(service: Any, spreadsheet_id: str, title: str) -> tuple[int, int]:
    meta = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties(rowCount)))",
        )
        .execute()
    )
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == title:
            sid = int(props["sheetId"])
            rc = int(props.get("gridProperties", {}).get("rowCount", 1000))
            return sid, rc
    raise ValueError(f"лист не найден: {title!r}")


def _dedup_sheet_name() -> str:
    s = (os.environ.get("GOOGLE_SHEETS_DEDUP_SHEET") or "").strip()
    return s or DEDUP_SHEET_DEFAULT


def _ensure_dedup_sheet(service: Any, spreadsheet_id: str) -> str:
    name = _dedup_sheet_name()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(title))")
        .execute()
    )
    titles = {sh["properties"]["title"] for sh in meta.get("sheets", [])}
    if name in titles:
        return name
    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": name}}}]},
        )
        .execute()
    )
    return name


def _existing_dedup_ids(service: Any, spreadsheet_id: str, dedup_sheet: str) -> set[str]:
    prefix = _sheet_a1_prefix(dedup_sheet)
    r = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{prefix}!A1:A50000")
        .execute()
    )
    out: set[str] = set()
    for row in r.get("values") or []:
        if row and str(row[0]).strip():
            out.add(str(row[0]).strip())
    return out


def _append_dedup_id(service: Any, spreadsheet_id: str, dedup_sheet: str, msg_id: str) -> None:
    prefix = _sheet_a1_prefix(dedup_sheet)
    (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=f"{prefix}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[msg_id]]},
        )
        .execute()
    )


def _last_row_matches(
    rows: list[list[Any]], width: int, row_vals: list[str]
) -> bool:
    if not rows:
        return False
    last = rows[-1]
    cells = [str(c).strip() for c in last[:width]]
    while len(cells) < width:
        cells.append("")
    return cells == row_vals


def _ensure_grid_has_row(
    service: Any, spreadsheet_id: str, sheet_title: str, next_row: int
) -> None:
    """Добавляет строки в лист, если next_row выходит за gridProperties.rowCount."""
    sheet_id, row_count = _sheet_id_and_row_count(service, spreadsheet_id, sheet_title)
    if next_row <= row_count:
        return
    extra = next_row - row_count + 200
    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "appendDimension": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "length": extra,
                        }
                    }
                ]
            },
        )
        .execute()
    )


def append_row(
    range_a1: str,
    values: Sequence[str | int],
    *,
    vk_dedupe_key: str | None = None,
) -> None:
    """
    Добавляет строку строго в колонки из range (например A:E).

    values().append в Google Sheets иногда смещает строку (например в D:H),
    если «таблица» определилась не с колонки A — поэтому используем
    чтение диапазона и явный update в A{n}:E{n}.

    vk_dedupe_key: стабильный ключ события ВК (id сообщения или peer+cmid) —
    при повторной доставке колбэка строка не дублируется (лист __vk_dedup).
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID")

    sheet, c1, c2 = _parse_sheet_cols(range_a1)
    width = _column_span_width(c1, c2)
    row_vals = [str(v) for v in values]
    if len(row_vals) < width:
        row_vals.extend([""] * (width - len(row_vals)))
    else:
        if len(row_vals) > width:
            log.warning(
                "строка длиннее диапазона %s: передано %s ячеек, лишнее обрезано "
                "(для колонки «Снятия» задайте GOOGLE_SHEETS_RANGE с F, например Отчеты!A:F)",
                range_a1,
                len(row_vals),
            )
        row_vals = row_vals[:width]

    creds = _load_sheets_credentials()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    prefix = _sheet_a1_prefix(sheet)
    read_rng = f"{prefix}!{c1}1:{c2}50000"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=read_rng)
        .execute()
    )
    rows = result.get("values") or []
    if vk_dedupe_key:
        dedup = _ensure_dedup_sheet(service, spreadsheet_id)
        sid = vk_dedupe_key.strip()
        if sid in _existing_dedup_ids(service, spreadsheet_id, dedup):
            log.info("skip sheet: duplicate VK dedupe_key=%r", sid)
            return
    elif _last_row_matches(rows, width, row_vals):
        log.info("skip sheet: last row identical (no dedupe key)")
        return

    # Не брать «нижнюю» строку из result["range"]: при запросе A1:E50000 API
    # подставляет конец сетки листа (напр. …E1009), а не последнюю строку с данными —
    # получится next_row за пределами таблицы и ошибка 400.
    next_row = _next_data_row(rows)
    _ensure_grid_has_row(service, spreadsheet_id, sheet, next_row)
    write_rng = f"{prefix}!{c1}{next_row}:{c2}{next_row}"
    body = {"values": [row_vals]}
    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=write_rng,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )

    if vk_dedupe_key:
        try:
            dedup = _ensure_dedup_sheet(service, spreadsheet_id)
            _append_dedup_id(service, spreadsheet_id, dedup, vk_dedupe_key.strip())
        except Exception:
            log.exception("dedup append failed for key=%r", vk_dedupe_key)
