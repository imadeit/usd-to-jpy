#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts import fx_rates


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
DEFAULT_PORT = 9343

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def json_response(handler: SimpleHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_payload(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def available_years() -> list[int]:
    years = set()
    for path in ROOT.glob("[0-9][0-9][0-9][0-9]"):
        if path.is_dir():
            years.add(int(path.name))
    for path in ROOT.glob("[0-9][0-9][0-9][0-9]/usd-jpy-[0-9][0-9][0-9][0-9].csv"):
        years.add(int(path.parent.name))
    return sorted(years)


def rates_payload(year: int) -> dict:
    csv_path = fx_rates.ensure_year_csv(ROOT, year)
    rows = [rate.to_json() for rate in fx_rates.read_year_csv(ROOT, year)]
    return {
        "ok": True,
        "year": year,
        "csvPath": str(csv_path.relative_to(ROOT)),
        "rows": rows,
        "corrections": fx_rates.read_corrections(ROOT)[-80:],
        "stats": {
            "count": len(rows),
            "first": rows[0]["date"] if rows else None,
            "last": rows[-1]["date"] if rows else None,
        },
    }


def all_rates_payload() -> dict:
    years = available_years()
    rows = []
    csv_paths = []
    for year in years:
        csv_path = fx_rates.ensure_year_csv(ROOT, year)
        csv_paths.append(str(csv_path.relative_to(ROOT)))
        rows.extend(rate.to_json() for rate in fx_rates.read_year_csv(ROOT, year))
    rows.sort(key=lambda row: row["date"])
    return {
        "ok": True,
        "scope": "all",
        "years": years,
        "csvPath": ", ".join(csv_paths),
        "rows": rows,
        "corrections": fx_rates.read_corrections(ROOT)[-80:],
        "stats": {
            "count": len(rows),
            "first": rows[0]["date"] if rows else None,
            "last": rows[-1]["date"] if rows else None,
        },
    }


def update_job(job_id: str, changes: dict) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(changes)
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()


def add_job_event(job_id: str, event: dict) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        events = job.setdefault("events", [])
        events.append(event)
        del events[:-120]
        total = event.get("total") or job.get("total") or 0
        index = event.get("index") or job.get("index") or 0
        job.update(
            {
                "total": total,
                "index": index,
                "progress": round(index / total * 100, 1) if total else 0,
                "currentDate": event.get("date"),
                "message": event.get("message") or event.get("status") or "",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
        )


def run_update(job_id: str, year: int | None, end_text: str | None) -> None:
    try:
        today = date.today()
        target_year = year or today.year
        end = fx_rates.parse_day(end_text) if end_text else (today if target_year == today.year else date(target_year, 12, 31))
        if end.year != target_year:
            end = date(target_year, 12, 31)
        fx_rates.ensure_year_csv(ROOT, target_year)
        latest = fx_rates.latest_rate_date(ROOT, target_year)
        start = date(target_year, 1, 1) if latest is None else latest + timedelta(days=1)

        update_job(
            job_id,
            {
                "status": "running",
                "year": target_year,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "message": "正在检查可更新日期...",
            },
        )
        stats = fx_rates.sync_range(ROOT, start, end, progress=lambda event: add_job_event(job_id, event))
        payload = rates_payload(target_year)
        update_job(
            job_id,
            {
                "status": "done",
                "progress": 100,
                "message": f"更新完成：新增 {stats['added']} 条，跳过 {stats['skipped']} 天。",
                "result": stats,
                "rates": payload,
            },
        )
    except Exception as exc:  # noqa: BLE001 - keep the local helper resilient and report the error to UI.
        update_job(job_id, {"status": "error", "message": str(exc), "progress": 100})


class RateHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"} or path.startswith("/assets/") or path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/rates":
            params = parse_qs(parsed.query)
            if (params.get("scope") or [""])[0] == "all":
                json_response(self, all_rates_payload())
                return
            year = int((params.get("year") or [str(date.today().year)])[0])
            json_response(self, rates_payload(year))
            return

        if parsed.path == "/api/years":
            json_response(self, {"ok": True, "years": available_years()})
            return

        if parsed.path == "/api/corrections":
            json_response(self, {"ok": True, "corrections": fx_rates.read_corrections(ROOT)})
            return

        if parsed.path == "/api/update-status":
            params = parse_qs(parsed.query)
            job_id = (params.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                json_response(self, {"ok": False, "error": "找不到这个更新任务。"}, status=HTTPStatus.NOT_FOUND)
                return
            json_response(self, {"ok": True, "job": job})
            return

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = read_payload(self)
        except json.JSONDecodeError:
            json_response(self, {"ok": False, "error": "请求 JSON 无法解析。"}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/update":
            job_id = uuid.uuid4().hex[:12]
            year = int(payload["year"]) if payload.get("year") else None
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "events": [],
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                    "message": "等待开始...",
                }
            thread = threading.Thread(target=run_update, args=(job_id, year, payload.get("end")), daemon=True)
            thread.start()
            json_response(self, {"ok": True, "job": JOBS[job_id]})
            return

        if parsed.path == "/api/correct":
            try:
                day = fx_rates.parse_day(str(payload.get("date") or ""))
                rate = fx_rates.correct_rate(
                    ROOT,
                    day,
                    str(payload.get("ttb") or ""),
                    str(payload.get("ttm") or "") or None,
                    str(payload.get("tts") or ""),
                    str(payload.get("reason") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            json_response(self, {"ok": True, "rate": rate.to_json(), "rates": rates_payload(day.year)})
            return

        json_response(self, {"ok": False, "error": "Unknown API endpoint."}, status=HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local USD/JPY dashboard and update API.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    server = ThreadingHTTPServer((args.host, args.port), RateHandler)
    print(f"USD/JPY dashboard: http://{args.host}:{args.port}/")
    print(f"Root: {ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
