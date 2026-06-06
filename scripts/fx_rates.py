#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
START_DATE = date(2026, 1, 1)
CSV_COLUMNS = ["日期", "TTB", "TTM", "TTS", "URL"]
MURC_URL_TEMPLATE = "https://www.murc-kawasesouba.jp/fx/past/index.php?id={yymmdd}"
MIZUHO_URL_TEMPLATE = "https://www.mizuhobank.co.jp/market/historical/backnumber_b/pdf/fx-quotation{yymmdd}.pdf"
USER_AGENT = "usd-to-jpy-local-updater/1.0"
SYSTEM_CA_BUNDLE = Path("/etc/ssl/cert.pem")


@dataclass(frozen=True)
class Rate:
    day: date
    ttb: str
    ttm: str
    tts: str
    url: str

    def to_row(self) -> dict[str, str]:
        return {
            "日期": self.day.isoformat(),
            "TTB": self.ttb,
            "TTM": self.ttm,
            "TTS": self.tts,
            "URL": self.url,
        }

    def to_json(self) -> dict[str, str]:
        return {
            "date": self.day.isoformat(),
            "ttb": self.ttb,
            "ttm": self.ttm,
            "tts": self.tts,
            "url": self.url,
        }


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def decimal_text(value: str | float | Decimal) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def midpoint(tts: str, ttb: str) -> str:
    return decimal_text((Decimal(tts) + Decimal(ttb)) / Decimal("2"))


def yymmdd(day: date) -> str:
    return day.strftime("%y%m%d")


def mizuho_pdf_url(day: date) -> str:
    return MIZUHO_URL_TEMPLATE.format(yymmdd=yymmdd(day))


def murc_url(day: date) -> str:
    return MURC_URL_TEMPLATE.format(yymmdd=yymmdd(day))


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (nth - 1))


def equinox_day(year: int, spring: bool) -> date:
    if spring:
        day = int(20.8431 + 0.242194 * (year - 1980) - (year - 1980) // 4)
        return date(year, 3, day)
    day = int(23.2488 + 0.242194 * (year - 1980) - (year - 1980) // 4)
    return date(year, 9, day)


def japanese_public_holidays(year: int) -> set[date]:
    holidays = {
        date(year, 1, 1),
        nth_weekday(year, 1, 0, 2),
        date(year, 2, 11),
        date(year, 2, 23),
        equinox_day(year, spring=True),
        date(year, 4, 29),
        date(year, 5, 3),
        date(year, 5, 4),
        date(year, 5, 5),
        nth_weekday(year, 7, 0, 3),
        date(year, 8, 11),
        nth_weekday(year, 9, 0, 3),
        equinox_day(year, spring=False),
        nth_weekday(year, 10, 0, 2),
        date(year, 11, 3),
        date(year, 11, 23),
    }
    for holiday in sorted(list(holidays)):
        if holiday.weekday() == 6:
            substitute = holiday + timedelta(days=1)
            while substitute in holidays:
                substitute += timedelta(days=1)
            holidays.add(substitute)
    current = date(year, 1, 2)
    while current < date(year, 12, 31):
        if current.weekday() < 5 and current not in holidays and current - timedelta(days=1) in holidays and current + timedelta(days=1) in holidays:
            holidays.add(current)
        current += timedelta(days=1)
    return holidays


def is_non_business_day(day: date) -> bool:
    bank_closure = (day.month, day.day) in {(1, 2), (1, 3), (12, 31)}
    return is_weekend(day) or bank_closure or day in japanese_public_holidays(day.year)


def latest_publishable_day(today: date | None = None) -> date:
    current = today or date.today()
    while is_non_business_day(current):
        current -= timedelta(days=1)
    return current


def is_expected_murc_response(final_url: str, day: date) -> bool:
    parsed = urlparse(final_url)
    if parsed.path.endswith("/fx/past/index.php") and parse_qs(parsed.query).get("id") == [yymmdd(day)]:
        return True
    return parsed.path.endswith("/fx/index.php") and day == latest_publishable_day()


def ssl_context() -> ssl.SSLContext | None:
    default_ca = ssl.get_default_verify_paths().openssl_cafile
    if default_ca and Path(default_ca).exists():
        return None
    if SYSTEM_CA_BUNDLE.exists():
        return ssl.create_default_context(cafile=str(SYSTEM_CA_BUNDLE))
    return None


def secure_urlopen(request: Request, timeout: float):
    context = ssl_context()
    if context is None:
        return urlopen(request, timeout=timeout)
    return urlopen(request, timeout=timeout, context=context)


def date_dir(root: Path, day: date) -> Path:
    return root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"


def year_csv_path(root: Path, year: int) -> Path:
    return root / f"{year:04d}" / f"usd-jpy-{year}.csv"


def year_csv_end(year: int, today: date | None = None) -> date:
    today = today or date.today()
    if year == today.year:
        return today
    return date(year, 12, 31)


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def read_rate_from_dir(root: Path, day: date) -> Rate | None:
    if is_non_business_day(day):
        return None
    folder = date_dir(root, day)
    try:
        ttb = decimal_text((folder / "TTB").read_text(encoding="utf-8").strip())
        ttm = decimal_text((folder / "TTM").read_text(encoding="utf-8").strip())
        tts = decimal_text((folder / "TTS").read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ArithmeticError):
        return None
    return Rate(day=day, ttb=ttb, ttm=ttm, tts=tts, url=mizuho_pdf_url(day))


def write_rate_to_dir(root: Path, rate: Rate) -> None:
    folder = date_dir(root, rate.day)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "TTB").write_text(rate.ttb, encoding="utf-8")
    (folder / "TTM").write_text(rate.ttm, encoding="utf-8")
    (folder / "TTS").write_text(rate.tts, encoding="utf-8")


def iter_year_rates_from_dirs(root: Path, year: int) -> list[Rate]:
    year_root = root / f"{year:04d}"
    rates: list[Rate] = []
    if not year_root.exists():
        return rates
    for month_dir in sorted(path for path in year_root.iterdir() if path.is_dir() and re.fullmatch(r"\d{2}", path.name)):
        for day_dir in sorted(path for path in month_dir.iterdir() if path.is_dir() and re.fullmatch(r"\d{2}", path.name)):
            try:
                day = date(year, int(month_dir.name), int(day_dir.name))
            except ValueError:
                continue
            rate = read_rate_from_dir(root, day)
            if rate:
                rates.append(rate)
    return sorted(rates, key=lambda item: item.day)


def rate_from_csv_row(row: dict[str, str]) -> Rate | None:
    date_text = row.get("日期") or row.get("date") or row.get("Date")
    if not date_text:
        return None
    try:
        day = parse_day(date_text)
        ttb = decimal_text(row.get("TTB") or row.get("ttb") or "")
        ttm = decimal_text(row.get("TTM") or row.get("ttm") or "")
        tts = decimal_text(row.get("TTS") or row.get("tts") or "")
    except (ValueError, ArithmeticError):
        return None
    return Rate(day=day, ttb=ttb, ttm=ttm, tts=tts, url=row.get("URL") or row.get("url") or mizuho_pdf_url(day))


def read_year_csv(root: Path, year: int) -> list[Rate]:
    path = year_csv_path(root, year)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [rate_from_csv_row(row) for row in csv.DictReader(handle)]
    return sorted([row for row in rows if row], key=lambda item: item.day)


def write_year_csv(root: Path, year: int, rates: Iterable[Rate]) -> Path:
    path = year_csv_path(root, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {rate.day: rate for rate in rates if rate.day.year == year}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for day in daterange(date(year, 1, 1), year_csv_end(year)):
            rate = rows.get(day)
            if rate:
                writer.writerow(rate.to_row())
            else:
                writer.writerow({"日期": day.isoformat(), "TTB": "", "TTM": "", "TTS": "", "URL": ""})
    return path


def build_year_csv(root: Path, year: int) -> Path:
    rates = iter_year_rates_from_dirs(root, year)
    return write_year_csv(root, year, rates)


def ensure_year_csv(root: Path, year: int) -> Path:
    path = year_csv_path(root, year)
    if not path.exists():
        return build_year_csv(root, year)
    return path


def latest_rate_date(root: Path, year: int) -> date | None:
    rates = read_year_csv(root, year) or iter_year_rates_from_dirs(root, year)
    if not rates:
        return None
    return max(rate.day for rate in rates)


def parse_murc_html(day: date, html: str) -> Rate | None:
    usd_index = html.find("USD")
    if usd_index < 0:
        return None
    window = html[usd_index : usd_index + 2500]
    values = re.findall(r't_right[^>]*>\s*([0-9,]+(?:\.\d+)?)', window)
    if len(values) < 2:
        return None
    tts = decimal_text(values[0].replace(",", ""))
    ttb = decimal_text(values[1].replace(",", ""))
    return Rate(day=day, ttb=ttb, ttm=midpoint(tts, ttb), tts=tts, url=mizuho_pdf_url(day))


def fetch_rate(day: date, timeout: float = 12.0) -> Rate | None:
    if is_non_business_day(day):
        return None
    request = Request(murc_url(day), headers={"User-Agent": USER_AGENT})
    try:
        with secure_urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "cp932"
            final_url = response.url
    except HTTPError as exc:
        if exc.code in {404, 410}:
            return None
        raise RuntimeError(f"HTTP {exc.code} while fetching {murc_url(day)}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching {murc_url(day)}: {exc.reason}") from exc
    if not is_expected_murc_response(final_url, day):
        return None
    html = raw.decode(charset, errors="ignore")
    return parse_murc_html(day, html)


ProgressCallback = Callable[[dict], None]


def sync_range(
    root: Path,
    start: date,
    end: date,
    *,
    force: bool = False,
    sleep_seconds: float = 0.15,
    progress: ProgressCallback | None = None,
) -> dict:
    if end < start:
        return {"checked": 0, "added": 0, "existing": 0, "skipped": 0, "errors": [], "csv": []}

    days = list(daterange(start, end))
    stats = {"checked": 0, "added": 0, "existing": 0, "skipped": 0, "errors": [], "csv": []}
    touched_years = set()
    for index, day in enumerate(days, start=1):
        event = {"index": index, "total": len(days), "date": day.isoformat(), "status": "checking"}
        if progress:
            progress(event)

        if is_non_business_day(day):
            stats["skipped"] += 1
            event.update({"status": "skipped", "message": "非营业日没有发布汇率数据"})
            if progress:
                progress(event)
            continue

        if not force and read_rate_from_dir(root, day):
            stats["existing"] += 1
            touched_years.add(day.year)
            event.update({"status": "existing", "message": "已有数据，跳过"})
            if progress:
                progress(event)
            continue

        stats["checked"] += 1
        try:
            rate = fetch_rate(day)
        except RuntimeError as exc:
            error = {"date": day.isoformat(), "message": str(exc)}
            stats["errors"].append(error)
            event.update({"status": "error", "message": str(exc)})
            if progress:
                progress(event)
            continue

        if not rate:
            stats["skipped"] += 1
            event.update({"status": "skipped", "message": "没有发布汇率数据"})
            if progress:
                progress(event)
            continue

        write_rate_to_dir(root, rate)
        stats["added"] += 1
        touched_years.add(day.year)
        event.update({"status": "added", "message": f"写入 TTM {rate.ttm}", "rate": rate.to_json()})
        if progress:
            progress(event)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    for year in sorted(touched_years or {start.year}):
        path = build_year_csv(root, year)
        stats["csv"].append(str(path))
    return stats


def sync_current_year(root: Path, today: date | None = None, progress: ProgressCallback | None = None) -> dict:
    today = today or date.today()
    year = today.year
    ensure_year_csv(root, year)
    latest = latest_rate_date(root, year)
    start = date(year, 1, 1) if latest is None else latest + timedelta(days=1)
    return sync_range(root, start, today, progress=progress)


def corrections_log_path(root: Path) -> Path:
    return root / "logs" / "corrections.jsonl"


def read_corrections(root: Path) -> list[dict]:
    path = corrections_log_path(root)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def append_correction_log(root: Path, before: Rate | None, after: Rate, reason: str) -> None:
    path = corrections_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": after.day.isoformat(),
        "before": before.to_json() if before else None,
        "after": after.to_json(),
        "reason": reason.strip(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def correct_rate(root: Path, day: date, ttb: str, ttm: str | None, tts: str, reason: str) -> Rate:
    before = read_rate_from_dir(root, day)
    after = Rate(
        day=day,
        ttb=decimal_text(ttb),
        ttm=decimal_text(ttm) if ttm else midpoint(decimal_text(tts), decimal_text(ttb)),
        tts=decimal_text(tts),
        url=mizuho_pdf_url(day),
    )
    write_rate_to_dir(root, after)
    existing = {rate.day: rate for rate in read_year_csv(root, day.year) or iter_year_rates_from_dirs(root, day.year)}
    existing[day] = after
    write_year_csv(root, day.year, existing.values())
    append_correction_log(root, before, after, reason)
    return after


def summary(root: Path, year: int) -> dict:
    path = ensure_year_csv(root, year)
    rows = read_year_csv(root, year)
    return {
        "year": year,
        "csv": str(path),
        "count": len(rows),
        "first": rows[0].day.isoformat() if rows else None,
        "last": rows[-1].day.isoformat() if rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and maintain USD/JPY daily TTB/TTM/TTS rates.")
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-csv", help="Build yearly CSV from YYYY/MM/DD folders.")
    build_parser.add_argument("--year", type=int, default=START_DATE.year)

    update_parser = subparsers.add_parser("update", help="Fetch missing dates and rebuild CSV.")
    update_parser.add_argument("--start", default=None, help="YYYY-MM-DD. Defaults to last stored date + 1.")
    update_parser.add_argument("--end", default=None, help="YYYY-MM-DD. Defaults to today.")
    update_parser.add_argument("--force", action="store_true", help="Refetch dates even when local files already exist.")
    update_parser.add_argument("--json-lines", action="store_true", help="Print progress events as JSON lines.")

    summary_parser = subparsers.add_parser("summary", help="Print CSV summary.")
    summary_parser.add_argument("--year", type=int, default=START_DATE.year)

    args = parser.parse_args()
    root = args.root.resolve()

    if args.command == "build-csv":
        path = build_year_csv(root, args.year)
        print(json.dumps(summary(root, args.year) | {"csv": str(path)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary":
        print(json.dumps(summary(root, args.year), ensure_ascii=False, indent=2))
        return 0

    if args.command == "update":
        end = parse_day(args.end) if args.end else date.today()
        if args.start:
            start = parse_day(args.start)
        else:
            latest = latest_rate_date(root, end.year)
            start = date(end.year, 1, 1) if latest is None else latest + timedelta(days=1)

        def emit(event: dict) -> None:
            if args.json_lines:
                print(json.dumps(event, ensure_ascii=False), flush=True)

        stats = sync_range(root, start, end, force=args.force, progress=emit)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
