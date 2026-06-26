"""FastAPI server for Nullifier v4.

Concurrency model:
  Pipeline runs in a daemon thread, puts events into a threading.queue.Queue.
  _execute_run() (async task) drains that queue via asyncio.to_thread, persists
  each event to SQLite, and fans out to per-client asyncio.Queues.
  Confirmation gate uses a concurrent.futures.Future — the pipeline thread blocks
  on cf.result(); the WebSocket handler calls cf.set_result() when the client responds.
"""

import asyncio
import concurrent.futures
import queue
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import events as ev
from .pipeline import run_pipeline
from .store import runs as db
from .tools.flag_store import add_flag, list_all_flags
from .tools.llm_client import health_check_local
from .tools.r_bridge import health_check as health_check_r


# ── In-process state ────────────────────────────────────────────────────────

@dataclass
class RunHandle:
    """Per-run bridge between the sync pipeline thread and the async event loop."""
    sync_queue: queue.Queue = field(default_factory=queue.Queue)
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    confirm_cf: concurrent.futures.Future | None = None
    pending_stage1: dict | None = None

    def build_confirm_callback(self):
        """Returns a callable for pipeline.py's confirm_callback parameter."""
        def _gate(stage1: dict) -> dict | None:
            self.pending_stage1 = stage1
            self.confirm_cf = concurrent.futures.Future()
            # confirmation_required event was already yielded by the pipeline
            # before calling this callback — it's already in sync_queue.
            try:
                return self.confirm_cf.result(timeout=600)  # 10-min user timeout
            except concurrent.futures.TimeoutError:
                return None  # abort on timeout
        return _gate

    def resolve_confirm(self, result: dict | None) -> None:
        if self.confirm_cf and not self.confirm_cf.done():
            self.confirm_cf.set_result(result)


# run_id → RunHandle (only present while run is active)
_active: dict[str, RunHandle] = {}

# run_id → set of asyncio.Queue (one per connected WebSocket client)
_subscribers: dict[str, set[asyncio.Queue]] = {}


# ── Application lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Nullifier API", version="0.4.0", lifespan=lifespan)

_STATIC = Path(__file__).parent / "static"


# ── REST endpoints ───────────────────────────────────────────────────────────

class CreateRunRequest(BaseModel):
    raw_input: str
    max_papers: int = 4
    skip_librarian: bool = False


@app.post("/api/runs", status_code=201)
async def create_run(req: CreateRunRequest):
    if len(req.raw_input.strip()) < 50:
        raise HTTPException(status_code=422, detail="raw_input too short (min 50 chars)")
    run_id = db.create_run(req.raw_input, req.max_papers)
    asyncio.create_task(_execute_run(run_id, req.raw_input, req.max_papers, req.skip_librarian))
    return {"run_id": run_id}


@app.get("/api/runs")
async def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["events"] = db.get_events(run_id)
    return run


@app.delete("/api/runs/{run_id}", status_code=200)
async def cancel_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"Run already {run['status']}")
    handle = _active.get(run_id)
    if handle:
        handle.cancel_flag.set()
        handle.resolve_confirm(None)  # unblock confirmation gate if waiting
    db.set_status(run_id, "cancelled")
    return {"status": "cancelled"}


@app.get("/api/health")
async def health():
    ok, msg = health_check_local()
    return {"local_llm": {"ok": ok, "message": msg}, "r": health_check_r()}


# ── Flags ────────────────────────────────────────────────────────────────────

class CreateFlagRequest(BaseModel):
    hypothesis_summary: str
    domain: str = "unknown"
    entities: list[str] = []
    paper_title: str
    paper_abstract_excerpt: str
    agent_classification: str
    agent_justification: str = ""
    user_classification: str
    user_reason: str = ""


def _filter_flags(flags: list[dict], domain: str | None, correction: str | None, q: str | None) -> list[dict]:
    out = flags
    if domain:
        out = [f for f in out if (f.get("domain") or "").lower() == domain.lower()]
    if correction:
        # correction is "agent->user", e.g. "supports->contradicts"
        out = [f for f in out
               if f"{f.get('agent_classification')}->{f.get('user_classification')}" == correction]
    if q:
        ql = q.lower()
        out = [f for f in out
               if ql in (f.get("paper_title") or "").lower()
               or ql in (f.get("paper_abstract_excerpt") or "").lower()
               or ql in (f.get("hypothesis_summary") or "").lower()]
    return out


@app.get("/api/flags")
async def get_flags(domain: str | None = None, correction: str | None = None, q: str | None = None):
    return _filter_flags(list_all_flags(), domain, correction, q)


@app.post("/api/flags", status_code=201)
async def create_flag(req: CreateFlagRequest):
    add_flag(
        hypothesis_summary=req.hypothesis_summary,
        domain=req.domain,
        entities=req.entities,
        paper_title=req.paper_title,
        paper_abstract_excerpt=req.paper_abstract_excerpt,
        agent_classification=req.agent_classification,
        agent_justification=req.agent_justification,
        user_classification=req.user_classification,
        user_reason=req.user_reason,
    )
    return {"status": "created"}


@app.get("/api/flags/export")
async def export_flags():
    return JSONResponse(
        list_all_flags(),
        headers={"Content-Disposition": "attachment; filename=nullifier-flags.json"},
    )


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/runs/{run_id}")
async def ws_run(websocket: WebSocket, run_id: str):
    await websocket.accept()

    run = db.get_run(run_id)
    if not run:
        await websocket.close(code=4004, reason="Run not found")
        return

    # Register subscriber BEFORE replaying DB to avoid missing live events.
    # The event loop is single-threaded so no events can arrive between
    # registration and the DB replay below.
    client_q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, set()).add(client_q)

    try:
        # Replay already-persisted events (gives reconnecting clients full history)
        for event_row in db.get_events(run_id):
            if not await _safe_send_json(websocket, event_row):
                return

        # Terminal runs: replay and close
        if run["status"] in ("completed", "failed", "cancelled"):
            await _safe_close(websocket)
            return

        # Stream live events; concurrently handle incoming WS messages
        recv_task = asyncio.create_task(_safe_receive(websocket))

        while True:
            drain_task = asyncio.create_task(client_q.get())
            try:
                done, _ = await asyncio.wait(
                    {drain_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                await _cancel_task(drain_task)
                await _cancel_task(recv_task)
                raise

            if drain_task in done:
                item = drain_task.result()
                if item is None:  # sentinel: run ended
                    await _cancel_task(recv_task)
                    await _safe_close(websocket)
                    break
                if not await _safe_send_json(websocket, item):
                    await _cancel_task(recv_task)
                    break

            if recv_task in done:
                msg = recv_task.result()
                if msg is None:  # client disconnected
                    await _cancel_task(drain_task)
                    break
                await _cancel_task(drain_task)
                _handle_ws_message(run_id, msg)
                recv_task = asyncio.create_task(_safe_receive(websocket))

    finally:
        _subscribers.get(run_id, set()).discard(client_q)


async def _safe_send_json(ws: WebSocket, payload: dict) -> bool:
    """Send one JSON payload; return False when the client is already gone."""
    try:
        await ws.send_json(payload)
        return True
    except (WebSocketDisconnect, RuntimeError, OSError):
        return False


async def _safe_close(ws: WebSocket) -> None:
    try:
        await ws.close()
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass


async def _cancel_task(task: asyncio.Task | None) -> None:
    if not task or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _safe_receive(ws: WebSocket) -> dict | None:
    """Receive one JSON message; return None on disconnect or error."""
    try:
        return await ws.receive_json()
    except (WebSocketDisconnect, Exception):
        return None


def _handle_ws_message(run_id: str, msg: dict) -> None:
    handle = _active.get(run_id)
    if not handle:
        return
    msg_type = msg.get("type", "")
    if msg_type == "confirm_sections":
        updated, _ = ev.apply_section_edits(handle.pending_stage1 or {}, msg.get("edits", {}))
        handle.resolve_confirm(updated)
    elif msg_type == "confirm_hypothesis":  # legacy
        handle.resolve_confirm(handle.pending_stage1)
    elif msg_type == "edit_hypothesis":  # legacy
        updated = dict(handle.pending_stage1 or {})
        updated["core_hypothesis"] = msg.get("hypothesis", "")
        handle.resolve_confirm(updated)
    elif msg_type == "abort_run":
        handle.cancel_flag.set()
        handle.resolve_confirm(None)


# ── Pipeline execution ───────────────────────────────────────────────────────

async def _execute_run(run_id: str, raw_input: str, max_papers: int, skip_librarian: bool = False) -> None:
    """Async task: drives the sync pipeline, persists events, fans out to WS clients."""
    handle = RunHandle()
    _active[run_id] = handle
    db.set_status(run_id, "running")

    formalized = evidence = verdict = analyst = None

    def _run_sync() -> None:
        nonlocal formalized, evidence, verdict, analyst
        try:
            for event in run_pipeline(
                raw_input,
                confirm_callback=handle.build_confirm_callback(),
                max_papers=max_papers,
                cancel_check=handle.cancel_flag.is_set,
                skip_librarian=skip_librarian,
            ):
                handle.sync_queue.put(event)
                if event.type == "run_completed":
                    formalized = event.payload["formalized"]
                    evidence = event.payload["evidence"]
                    verdict = event.payload["verdict"]
                    analyst = event.payload["analyst"]
        except Exception as e:
            handle.sync_queue.put(ev.run_failed(str(e)))
        finally:
            handle.sync_queue.put(None)  # sentinel always sent

    threading.Thread(target=_run_sync, daemon=True).start()

    seq = 0
    try:
        while True:
            event = await asyncio.to_thread(handle.sync_queue.get)
            if event is None:
                break

            row = {
                "seq": seq,
                "type": event.type,
                "payload": event.payload,
                "ts": event.ts,
            }
            db.append_event(run_id, seq, event.type, event.payload, event.ts)
            seq += 1

            for q in list(_subscribers.get(run_id, set())):
                q.put_nowait(row)

        # ── Finalise run status ──────────────────────────────────────────
        if handle.cancel_flag.is_set():
            db.set_status(run_id, "cancelled")
        elif formalized is not None:
            db.save_results(run_id, formalized, evidence, verdict, analyst)
            db.set_status(run_id, "completed")
        else:
            db.set_status(run_id, "failed")

    except Exception as e:
        db.set_status(run_id, "failed")
        err = ev.run_failed(str(e))
        row = {"seq": seq, "type": err.type, "payload": err.payload, "ts": err.ts}
        db.append_event(run_id, seq, err.type, err.payload, err.ts)
        for q in list(_subscribers.get(run_id, set())):
            q.put_nowait(row)

    finally:
        _active.pop(run_id, None)
        # Broadcast sentinel so all WS clients close cleanly
        for q in list(_subscribers.get(run_id, set())):
            q.put_nowait(None)


# ── Static frontend ──────────────────────────────────────────────────────────
# Assets are mounted precisely at /assets so the SPA catch-all below doesn't
# intercept them.  Root-level files (favicon, icons) get explicit routes.
# The catch-all /{full_path:path} returns index.html for all React Router paths.
if _STATIC.exists():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon() -> FileResponse:
        return FileResponse(_STATIC / "favicon.svg")

    @app.get("/icons.svg", include_in_schema=False)
    async def _icons() -> FileResponse:
        return FileResponse(_STATIC / "icons.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> FileResponse:
        return FileResponse(_STATIC / "index.html")
