#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import ssl
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
START_DATE = date(1968, 4, 1)
QUOTE_URL = "https://www.lbma.org.uk/prices-and-data/precious-metal-prices#/table"
HISTORY_URL = "https://prices.lbma.org.uk/json/gold_pm.json"
USER_AGENT = "usd-to-jpy-gold-local-updater/1.0"
SYSTEM_CA_BUNDLE = Path("/etc/ssl/cert.pem")
CSV_COLUMNS = ["日期", "价格", "URL"]


@dataclass(frozen=True)
class GoldPrice:
    day: date
    price: str
    url: str = QUOTE_URL

    def to_row(self) -> dict[str, str]:
        return {
            "日期": self.day.isoformat(),
            "价格": self.price,
            "URL": self.url,
        }

    def to_json(self) -> dict[str, str]:
        return {
            "date": self.day.isoformat(),
            "price": self.price,
            "url": self.url,
        }


ProgressCallback = Callable[[dict], None]


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def decimal_text(value: str | float | Decimal) -> str:
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "n/a", "-"}:
        raise ValueError("empty decimal value")
    return str(Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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


def gold_root(root: Path) -> Path:
    return root / "gold"


def year_csv_path(root: Path, year: int) -> Path:
    return gold_root(root) / f"gold-usd-{year}.csv"


def all_csv_path(root: Path) -> Path:
    return gold_root(root) / "gold-usd-all.csv"


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def available_years(root: Path) -> list[int]:
    folder = gold_root(root)
    if not folder.exists():
        return []
    years = []
    for path in folder.glob("gold-usd-[0-9][0-9][0-9][0-9].csv"):
        try:
            years.append(int(path.stem.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return sorted(set(years))


def price_from_csv_row(row: dict[str, str]) -> GoldPrice | None:
    date_text = row.get("日期") or row.get("date") or row.get("Date")
    if not date_text:
        return None
    try:
        day = parse_day(date_text)
        return GoldPrice(
            day=day,
            price=decimal_text(row.get("价格") or row.get("price") or row.get("Price") or row.get("close") or row.get("Close")),
            url=row.get("URL") or row.get("url") or QUOTE_URL,
        )
    except (ValueError, ArithmeticError, InvalidOperation):
        return None


def read_csv(path: Path) -> list[GoldPrice]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [price_from_csv_row(row) for row in csv.DictReader(handle)]
    return sorted([row for row in rows if row], key=lambda item: item.day)


def read_year_csv(root: Path, year: int) -> list[GoldPrice]:
    return read_csv(year_csv_path(root, year))


def read_all_csv(root: Path) -> list[GoldPrice]:
    path = all_csv_path(root)
    if path.exists():
        return read_csv(path)
    rows: list[GoldPrice] = []
    for year in available_years(root):
        rows.extend(read_year_csv(root, year))
    return sorted(rows, key=lambda item: item.day)


def write_csv(path: Path, rows: Iterable[GoldPrice]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for price in sorted(rows, key=lambda item: item.day):
            writer.writerow(price.to_row())
    return path


def write_all(root: Path, rows: Iterable[GoldPrice]) -> dict:
    by_day = {price.day: price for price in rows}
    prices = [by_day[day] for day in sorted(by_day)]
    write_csv(all_csv_path(root), prices)
    years: dict[int, list[GoldPrice]] = {}
    for price in prices:
        years.setdefault(price.day.year, []).append(price)
    paths = []
    for year, year_rows in sorted(years.items()):
        paths.append(str(write_csv(year_csv_path(root, year), year_rows)))
    return {"rows": len(prices), "years": sorted(years), "paths": paths}


def latest_price_date(root: Path) -> date | None:
    rows = read_all_csv(root)
    if not rows:
        return None
    return max(row.day for row in rows)


def parse_history_json(text: str, start: date, end: date) -> list[GoldPrice]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LBMA gold price response was not valid JSON") from exc
    rows: list[GoldPrice] = []
    for entry in payload:
        try:
            day = parse_day(str(entry.get("d")))
        except (TypeError, ValueError):
            continue
        if day < start or day > end:
            continue
        values = entry.get("v") or []
        if not values or values[0] is None:
            continue
        try:
            rows.append(GoldPrice(day=day, price=decimal_text(values[0]), url=QUOTE_URL))
        except (ValueError, ArithmeticError, InvalidOperation):
            continue
    return sorted(rows, key=lambda item: item.day)


def fetch_history(start: date, end: date, timeout: float = 30.0) -> list[GoldPrice]:
    request = Request(HISTORY_URL, headers={"User-Agent": USER_AGENT})
    try:
        with secure_urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching gold history") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching gold history: {exc.reason}") from exc
    return parse_history_json(raw.decode(charset, errors="ignore"), start, end)


def merge_rows(existing: Iterable[GoldPrice], incoming: Iterable[GoldPrice]) -> list[GoldPrice]:
    rows = {price.day: price for price in existing}
    for price in incoming:
        rows[price.day] = price
    return [rows[day] for day in sorted(rows)]


def sync_range(
    root: Path,
    start: date,
    end: date,
    *,
    include_latest: bool = True,
    progress: ProgressCallback | None = None,
) -> dict:
    if end < start:
        return {"downloaded": 0, "stored": len(read_all_csv(root)), "first": None, "last": None, "csv": []}

    def emit(index: int, total: int, message: str, status: str = "running") -> None:
        if progress:
            progress({"index": index, "total": total, "date": end.isoformat(), "status": status, "message": message})

    total_steps = 4 if include_latest else 3
    existing = read_all_csv(root)
    emit(1, total_steps, f"正在下载 XAU/USD 日线：{start.isoformat()} ~ {end.isoformat()}")
    downloaded = fetch_history(start, end)
    emit(2, total_steps, f"历史日线下载完成：{len(downloaded)} 条")

    if include_latest:
        latest = downloaded[-1] if downloaded else (existing[-1] if existing else None)
        if latest:
            emit(3, total_steps, f"最新 LBMA PM Fix：{latest.price} USD/oz · {latest.day.isoformat()}")
        else:
            emit(3, total_steps, "没有可写入的黄金价格")

    merged = merge_rows(existing, downloaded)
    result = write_all(root, merged)
    first = merged[0].day.isoformat() if merged else None
    last = merged[-1].day.isoformat() if merged else None
    emit(total_steps, total_steps, f"黄金数据写入完成：{len(merged)} 条", "done")
    return {
        "downloaded": len(downloaded),
        "stored": len(merged),
        "first": first,
        "last": last,
        "csv": result["paths"] + [str(all_csv_path(root))],
    }


def sync_missing(root: Path, today: date | None = None, progress: ProgressCallback | None = None) -> dict:
    today = today or date.today()
    latest = latest_price_date(root)
    start = START_DATE if latest is None else latest + timedelta(days=1)
    if start > today:
        start = today
    return sync_range(root, start, today, progress=progress)


def summary(root: Path, year: int | None = None) -> dict:
    rows = read_year_csv(root, year) if year else read_all_csv(root)
    return {
        "scope": year or "all",
        "count": len(rows),
        "first": rows[0].day.isoformat() if rows else None,
        "last": rows[-1].day.isoformat() if rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and maintain XAU/USD gold spot daily prices.")
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="Fetch missing gold history and latest quote.")
    update_parser.add_argument("--start", default=None, help="YYYY-MM-DD. Defaults to last stored date + 1.")
    update_parser.add_argument("--end", default=None, help="YYYY-MM-DD. Defaults to today.")
    update_parser.add_argument("--json-lines", action="store_true", help="Print progress events as JSON lines.")

    summary_parser = subparsers.add_parser("summary", help="Print gold CSV summary.")
    summary_parser.add_argument("--year", type=int, default=None)

    args = parser.parse_args()
    root = args.root.resolve()

    if args.command == "summary":
        print(json.dumps(summary(root, args.year), ensure_ascii=False, indent=2))
        return 0

    if args.command == "update":
        end = parse_day(args.end) if args.end else date.today()
        if args.start:
            start = parse_day(args.start)
        else:
            latest = latest_price_date(root)
            start = START_DATE if latest is None else latest + timedelta(days=1)

        def emit(event: dict) -> None:
            if args.json_lines:
                print(json.dumps(event, ensure_ascii=False), flush=True)

        stats = sync_range(root, start, end, progress=emit)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        if args.json_lines:
            time.sleep(0.01)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
