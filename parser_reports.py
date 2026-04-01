from __future__ import annotations

import re
from typing import Any

_DASH = r"[—\-–]"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_report(text: str) -> dict[str, Any]:
    raw = normalize_newlines((text or "").strip())
    if not raw:
        return {"report_type": "unknown", "raw": ""}

    if _looks_like_vacation(raw):
        return _parse_vacation(raw)
    if _looks_like_moderation(raw):
        return _parse_moderation(raw)
    return {"report_type": "unknown", "raw": raw[:8000]}


def _parse_main_line(line: str) -> tuple[int, str] | None:
    s = line.strip()
    if not s:
        return None
    # «6.12» без пробела после точки: общий шаблон с (?!\d) не матчится (как и подпункты 4.1)
    m6 = re.match(r"^\s*6\.\s*(\d+)\s*$", s)
    if m6:
        return 6, m6.group(1).strip()
    m = re.match(
        r"^\s*(?:▹\s*)?(?:\(([1-6])\)\s*(.*)|([1-6])\.(?!\d)\s*(.*)|([1-6])\)\s*(.*))$",
        s,
        re.I,
    )
    if not m:
        return None
    if m.group(1) is not None:
        return int(m.group(1)), (m.group(2) or "").strip()
    if m.group(3) is not None:
        return int(m.group(3)), (m.group(4) or "").strip()
    return int(m.group(5)), (m.group(6) or "").strip()


def _parse_sub_numbered(line: str) -> tuple[int, int, str] | None:
    m = re.match(r"^\s*(\d+)\.(\d+)\s+(.+)$", line.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3).strip()


def _finalize_items_dict(
    items: dict[int, str], four_subs: list[str], seen_main_4: bool
) -> dict[str, Any] | None:
    if not items.get(1):
        return None
    it = dict(items)
    # Пункт 6 иногда в одной строке с 5 (ВК склеил или одна строка): «… | МГ [12] 6. 12»
    if it.get(5) and not it.get(6):
        m = re.search(r"\s+6[\.．]\s*(.+)$", it[5])
        if m:
            it[5] = it[5][: m.start()].strip()
            it[6] = m.group(1).strip()
    if it.get(3) and it.get(4):
        it[3], it[4] = _maybe_swap_playtime_punish(it[3], it[4])
    modes_parts: list[str] = []
    if it.get(5):
        modes_parts.append(it[5])
    modes_parts.extend(four_subs)
    modes = " | ".join(p for p in modes_parts if p and p.strip()) or None
    pun = it.get(4)
    punishments_total, punishments_note = _punishments_from_line(pun, four_subs)
    rem = (it.get(6) or "").strip() or None
    return {
        "items": it,
        "four_subs": list(four_subs),
        "modes": modes,
        "punishments_total": punishments_total,
        "punishments_note": punishments_note,
        "removals": rem,
        "seen_main_4": seen_main_4,
    }


def _extract_numbered_block(lines: list[str]) -> dict[str, Any] | None:
    items: dict[int, str] = {}
    four_subs: list[str] = []
    mode = "seek"
    seen_main_4 = False

    def pack() -> dict[str, Any] | None:
        return _finalize_items_dict(items, four_subs, seen_main_4)

    for line in lines:
        s = re.sub(r"[\u200b-\u200d\ufeff]", "", line.strip()).replace("\uFF0E", ".")
        if not s:
            continue

        sub = _parse_sub_numbered(s)
        if sub and sub[0] == 4 and mode in ("g3", "g4s", "g4"):
            four_subs.append(f"4.{sub[1]} {sub[2]}")
            mode = "g4s"
            continue

        main = _parse_main_line(s)
        if not main:
            if mode == "g6" and items.get(6) is not None:
                items[6] = (items[6] + " " + s).strip()
                continue
            if mode == "g5" and items.get(5) is not None:
                if s.startswith("(") or not _parse_sub_numbered(s):
                    items[5] = (items[5] + " " + s).strip()
            continue

        n, content = main
        if n == 1:
            if items.get(1) and items.get(2):
                return pack()
            items.clear()
            four_subs.clear()
            seen_main_4 = False
            items[1] = content
            mode = "g1"
        elif n == 2 and items.get(1):
            items[2] = content
            mode = "g2"
        elif n == 3 and items.get(2):
            items[3] = content
            mode = "g3"
        elif n == 4 and mode in ("g3", "g4s"):
            items[4] = content
            seen_main_4 = True
            mode = "g4"
        elif n == 4 and mode == "g4" and items.get(4):
            # второй и следующие (4): сумма уже в первом, это разбивка по режимам
            four_subs.append(content)
        elif n == 5 and mode in ("g3", "g4s", "g4") and items.get(3):
            items[5] = content
            mode = "g5"
        elif n == 6 and mode in ("g3", "g4s", "g4", "g5") and items.get(5):
            items[6] = content
            mode = "g6"

    return pack()


def _maybe_swap_playtime_punish(a: str, b: str) -> tuple[str, str]:
    a, b = a.strip(), b.strip()
    time_rx = re.compile(
        r"час|часа|часов|\d+[,.]?\d*\s*час|\d+\s*ч\b|\d+\s*h\b|\d+h\b|\d+\s*m\b",
        re.I,
    )
    if re.match(r"^\d+$", a) and time_rx.search(b):
        return b, a
    if time_rx.search(a) and re.match(r"^\d+$", b):
        return a, b
    return a, b


def _punishments_from_line(line: str | None, four_subs: list[str]) -> tuple[str, str]:
    if not line and four_subs:
        nums: list[int] = []
        for s in four_subs:
            for m in re.finditer(r"(\d+)\s*наказ", s, re.I):
                nums.append(int(m.group(1)))
            if not nums:
                m2 = re.search(r"(\d+)\s*$", s)
                if m2:
                    nums.append(int(m2.group(1)))
        if nums:
            return str(sum(nums)), ""
        return "", ""
    if not line:
        return "", ""
    m = re.search(r"(\d+)", line)
    total = m.group(1) if m else ""
    return total, line.strip()


def _try_unnumbered_compact(lines: list[str]) -> dict[str, Any] | None:
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if len(nonempty) < 5:
        return None
    for ln in nonempty[:7]:
        if _parse_main_line(ln):
            return None
    nick, d2, d3, d4, d5 = nonempty[0], nonempty[1], nonempty[2], nonempty[3], nonempty[4]
    if not re.match(r"^\d{1,2}[\./]\d{1,2}(\.\d{2,4})?$", d2) and not re.search(
        r"\d{1,2}\s+[А-Яа-я]+\.?", d2
    ):
        return None
    items: dict[int, str] = {1: nick, 2: d2, 3: d3, 4: d4, 5: d5}
    if len(nonempty) > 5:
        items[6] = nonempty[5]
        if len(nonempty) > 6:
            items[6] = (items[6] + " " + " ".join(nonempty[6:])).strip()
    return _finalize_items_dict(items, [], True)


def _try_space_prefix_block(lines: list[str]) -> dict[str, Any] | None:
    """1 ник / 2 дата с пробелом без точки; дальше 3–5 строка подряд = время, наказания, режимы."""
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if len(nonempty) < 5:
        return None
    if re.match(r"^1\.\s", nonempty[0]):
        return None
    m1 = re.match(r"^1\s+(.+)$", nonempty[0])
    m2 = re.match(r"^2\s+(.+)$", nonempty[1])
    if not m1 or not m2:
        return None
    d = m2.group(1).strip()
    if not re.match(r"^\d{1,2}[\./]\d{1,2}(\.\d{2,4})?$", d):
        return None
    items: dict[int, str] = {
        1: m1.group(1).strip(),
        2: d,
        3: nonempty[2],
        4: nonempty[3],
        5: nonempty[4],
    }
    if len(nonempty) >= 6:
        items[6] = nonempty[5]
        tail = nonempty[6:]
        if tail:
            items[6] = (items[6] + " " + " ".join(tail)).strip()
    else:
        tail = nonempty[5:]
        if tail:
            items[5] = (items[5] + " " + " ".join(tail)).strip()
    return _finalize_items_dict(items, [], True)


def _looks_like_vacation(text: str) -> bool:
    t = text.lower()
    if re.search(r"отгул\s*/\s*отпуск", t):
        return True
    lines = [ln for ln in normalize_newlines(text).split("\n") if ln.strip()]
    for ln in lines[:15]:
        main = _parse_main_line(ln)
        if main:
            n, content = main
            if n == 1 and re.search(
                r"выходной|отгул|отпуск|беру\s+отгул|на\s+сегодня\s+беру",
                content,
                re.I,
            ):
                return True
            break
    head = "\n".join(lines[:6])
    if re.search(r"\bотгулы?\b", head, re.I) and not any(
        _parse_main_line(ln) is not None for ln in lines
    ):
        return True
    return False


def _looks_like_moderation(text: str) -> bool:
    lines = normalize_newlines(text).split("\n")
    blk = _extract_numbered_block(lines)
    if blk and blk["items"].get(1) and blk["items"].get(2):
        return True
    if _try_space_prefix_block(lines):
        return True
    if _try_unnumbered_compact(lines):
        return True
    if re.search(
        r"Наигранное\s+время|Общее\s+кол-во\s+наказаний|Режимы\s*[" + _DASH + r"]",
        text,
        re.I,
    ):
        return True
    if re.search(r"^\s*(?:▹\s*)?[1-6][\.\)]", text, re.I | re.MULTILINE):
        return True
    if re.search(r"(?m)^\s*\([1-6]\)\s+\S", text):
        return True
    if re.search(r"(?m)^\s*1\s+\S+.*\n\s*2\s+\d{1,2}[\./]", text):
        return True
    return False


def _parse_vacation(text: str) -> dict[str, Any]:
    lines = normalize_newlines(text).split("\n")
    blk = _extract_numbered_block(lines)
    if blk and blk["items"].get(1):
        it = blk["items"]
        return {
            "report_type": "vacation",
            "vacation_type": it.get(1),
            "vacation_reason": it.get(2),
            "vacation_period": it.get(3),
            "raw": text[:8000],
        }
    vac_type = re.search(
        r"(?:▹\s*)?1[\.\)]\s*(.+)", text, re.I | re.MULTILINE
    )
    v1 = vac_type.group(1).strip() if vac_type else None
    return {
        "report_type": "vacation",
        "vacation_type": v1,
        "vacation_reason": None,
        "vacation_period": None,
        "raw": text[:8000],
    }


def _nick_cleanup(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^_+|_+$", "", s)
    return s.strip()


def _parse_moderation(text: str) -> dict[str, Any]:
    lines = normalize_newlines(text).split("\n")
    blk = _extract_numbered_block(lines)
    if not blk or not blk["items"].get(1):
        blk = _try_space_prefix_block(lines)
    if not blk or not blk["items"].get(1):
        compact = _try_unnumbered_compact(lines)
        if compact:
            blk = compact
        else:
            return _parse_moderation_loose(text)

    it = blk["items"]
    nick = _nick_cleanup(it.get(1) or "")
    report_date = (it.get(2) or "").strip()
    playtime_display = (it.get(3) or "").strip()
    punishments_total = blk.get("punishments_total") or ""
    modes = blk.get("modes")
    removals = blk.get("removals")

    ph = _extract_hours_numeric(playtime_display)
    return {
        "report_type": "moderation",
        "nick": nick or None,
        "report_date": report_date or None,
        "playtime_hours": ph,
        "playtime_display": playtime_display or None,
        "punishments_total": punishments_total or None,
        "punishments_note": ((it.get(4) or "").strip() or None) if not punishments_total else None,
        "modes": modes,
        "removals": removals,
        "raw": text[:8000],
    }


def _parse_moderation_loose(text: str) -> dict[str, Any]:
    nick = _first_match(r"(?:▹\s*)?\(1\)\s*(.+)", text, re.MULTILINE) or _first_match(
        r"(?:▹\s*)?1\)\s*_([^_\n]+)_", text
    ) or _first_match(r"(?:▹\s*)?1\)\s*(.+)", text)
    if nick:
        nick = _nick_cleanup(nick.split("\n", 1)[0])
    report_date = _first_match(
        r"(?:▹\s*)?\(2\)\s*(\d{1,2}[\./]\d{1,2}(?:[\./]\d{2,4})?)", text, re.I
    ) or _first_match(r"(?:▹\s*)?2\)\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", text) or _first_match(
        r"2[\.\)]\s*(\d{1,2}[\./]\d{1,2}(?:[\./]\d{2,4})?)", text, re.I
    )
    playtime = _first_match(
        r"Наигранное\s+время\s*[" + _DASH + r"]\s*(\d+)\s*час", text, re.I
    ) or _first_match(
        r"Наигранное\s+время\s*[" + _DASH + r"]\s*([\d,\.]+\s*(?:час|часа|часов|ч\b))", text, re.I
    )
    total_pun = _first_match(
        r"Общее\s+кол-во\s+наказаний\s*[" + _DASH + r"]\s*(\d+)", text, re.I
    ) or _first_match(r"Общее\s+количество\s+наказаний\s*[" + _DASH + r"]\s*(\d+)", text, re.I)

    modes = None
    m = re.search(r"Режимы\s*[" + _DASH + r"]\s*(.+?)(?:\n{2,}|\Z)", text, re.I | re.DOTALL)
    if m:
        modes = re.sub(r"\s+", " ", m.group(1).strip())

    pd = playtime if playtime else None
    ph = _extract_hours_numeric(pd) if pd else None
    return {
        "report_type": "moderation",
        "nick": nick,
        "report_date": report_date,
        "playtime_hours": ph,
        "playtime_display": pd,
        "punishments_total": total_pun,
        "modes": modes,
        "removals": None,
        "raw": text[:8000],
    }


def _extract_hours_numeric(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:час|часа|часов)", s, re.I)
    if m:
        return m.group(1).replace(",", ".")
    m = re.search(r"(\d+)\s*ч\b", s, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*h\b", s, re.I) or re.search(r"(\d+)h\b", s, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*m\b", s, re.I)
    if m:
        return None
    m = re.match(r"^(\d+)$", s.strip())
    if m and int(m.group(1)) < 72:
        return m.group(1)
    return None


def _first_match(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None
