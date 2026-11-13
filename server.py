"""
JobPilot server — FastAPI in front of dashboard.py's logic, serving the React app (web/dist).

    .venv/bin/python server.py            # http://localhost:8765, opens it in the agent Chrome
    .venv/bin/python server.py --no-open  # what the background service (com.jobpilot.dashboard) runs

Same API as the old stdlib server (dashboard.state / dispatch / _timeline — one implementation), plus:
    GET /api/events        server-sent events: the whole state, pushed whenever it changes (one producer
                           computes it for every open tab; nothing runs while no tab is open)
    GET /api/job/{id}      one job with its answers (qa) — left out of the state to keep it small
    WS  /api/live/{job}    the agent's window for that job, as JPEG frames (CDP Page.startScreencast)
Safety as before: Host must be localhost (blocks DNS rebinding), every POST needs X-JobPilot (a page on
another site can't send it without a CORS preflight, which this server never approves), and the live
socket only accepts this app's own origins.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

import dashboard as d
import store

WEB = d.HERE / "web" / "dist"
HOSTS = {f"localhost:{d.PORT}", f"127.0.0.1:{d.PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS} | {"http://localhost:5173", "http://127.0.0.1:5173"}   # + the Vite dev server


class Feed:
    """The state, computed once for every SSE client: every 1.5 s while a task runs, 4 s otherwise, at once
    after an action (poke) — and not at all while no client is connected."""
    body, version, clients = "", 0, 0
    poke = asyncio.Event()
    changed = asyncio.Condition()

    @classmethod
    async def run(cls) -> None:
        while True:
            busy = False
            if cls.clients:
                try:
                    s = await run_in_threadpool(d.state)
                    busy = any(t["status"] == "running" for t in s["tasks"])
                    body = json.dumps(s, default=str)
                    if body != cls.body:
                        cls.body, cls.version = body, cls.version + 1
                        async with cls.changed:
                            cls.changed.notify_all()
                except Exception as e:  # noqa: BLE001 — a bad read must not stop the feed for good
                    print(f"state failed: {type(e).__name__}: {e}", flush=True)
            cls.poke.clear()
            try:
                await asyncio.wait_for(cls.poke.wait(), 1.5 if busy else 4)
            except TimeoutError:
                pass


@asynccontextmanager
async def lifespan(_app):
    d._adopt()                                                     # pick up tasks still running from before a restart
    threading.Thread(target=d._watch_loop, daemon=True).start()    # submission watcher
    feed = asyncio.create_task(Feed.run())
    yield
    feed.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.headers.get("host", "") not in HOSTS:
        return PlainTextResponse("forbidden", 403)
    if request.method == "POST" and request.headers.get("x-jobpilot") != "1":
        return PlainTextResponse("forbidden", 403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else response.headers.get("Cache-Control", "no-cache")
    return response


# ── reads ──

@app.get("/api/state")
def api_state():
    return d.state()


@app.get("/api/task/{tid}")
def api_task(tid: int):
    t = d.TASKS.get(tid)
    return {"task": t, "log": d._tail(d.TASK_LOGS / f"{tid}.log", 400)} if t else JSONResponse({"error": "no such task"}, 404)


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    j = d.job_view(job_id) if d.JOB.match(job_id) else None
    return j or JSONResponse({"error": "no such job"}, 404)


@app.get("/api/resume-diff")
def api_resume_diff(url: str):
    """For the agent Chrome's side panel: which job this tab's URL belongs to, and what its tailored resume
    changed against the base one."""
    import resume_diff
    j = resume_diff.job_for_url(url)
    if not j:
        return {"match": False}
    return {"match": True, **resume_diff.diff_for_job(j), "job": {"id": j["id"], "company": j["company"], "title": j["title"]}}


@app.get("/api/resume-pdf/{job_id}/{which}")
def api_resume_pdf(job_id: str, which: str):
    """The tailored PDF that was uploaded for this job, or the base resume."""
    import resume_diff
    j = store.get_job(job_id) if d.JOB.match(job_id) else None
    if j and which == "annotated":
        a = resume_diff.annotated(j)
        return FileResponse(a, media_type="text/html") if a else PlainTextResponse("", 404)
    f = resume_diff.tailored_pdf(j) if (j and which == "tailored") else \
        (resume_diff.RESUME_DIR / "resume.pdf") if which == "base" else None
    return FileResponse(f, media_type="application/pdf") if f and f.exists() else PlainTextResponse("", 404)


@app.get("/api/log/{job_id}")
def api_log(job_id: str):
    return d._timeline(job_id) if d.JOB.match(job_id) else JSONResponse({"error": "bad job id"}, 400)


@app.get("/api/file/{name}")
def api_file(name: str):
    if name not in d.FILES:
        return JSONResponse({"error": "not an editable file"}, 404)
    return {"text": d._read(d.FILES[name][0]), "name": d.FILES[name][0].name}


@app.get("/artifacts/{name}")
def artifact(name: str):
    f = d.ART / name
    return FileResponse(f, media_type="image/png") if re.fullmatch(r"[0-9a-f]{16}\.png", name) and f.exists() else PlainTextResponse("", 404)


# ── every action ──

@app.post("/api/{act}")
@app.post("/api/{act}/{arg}")
async def api_act(act: str, request: Request, arg: str = ""):
    raw = (await request.body())[:12_000_000]
    code, out = await run_in_threadpool(d.dispatch, act, arg, raw)
    Feed.poke.set()                                     # every open tab sees the result now, not in 4 s
    return JSONResponse(out, code)


# ── live: state pushes and the agent's screen ──

@app.get("/api/events")
async def events(request: Request):
    async def stream():
        Feed.clients += 1
        Feed.poke.set()                                 # a new tab gets the state at once
        seen = 0
        try:
            while not await request.is_disconnected():
                if Feed.version != seen and Feed.body:
                    seen = Feed.version
                    yield f"data: {Feed.body}\n\n"
                    continue
                try:
                    async with Feed.changed:
                        await asyncio.wait_for(Feed.changed.wait(), 15)
                except TimeoutError:
                    yield ": still here\n\n"            # keeps proxies and the browser from timing out
        finally:
            Feed.clients -= 1
    return StreamingResponse(stream(), media_type="text/event-stream")


def _page_for(job_id: str) -> dict | None:
    """The agent-Chrome tab showing this job: its listing, a page of its form (recipe), or any page the agent
    went to during this run (it often follows Apply to another site) — the most recently visited first."""
    j = store.get_job(job_id)
    if not j:
        return None
    tabs = d._open_tabs()
    visited = re.findall(r'"url": "(https?://[^"]+)"', d._read(d.ART / f"{job_id}.log"))
    for u in reversed(visited):
        k = d.apply_agent._urlkey(u)
        if t := next((t for t in reversed(tabs) if d.apply_agent._urlkey(t["url"]).startswith(k)), None):
            return t
    return d._tab_for(j, store.get_extra(j, "recipe"), tabs)


@app.websocket("/api/live/{job_id}")
async def live(ws: WebSocket, job_id: str):
    if ws.headers.get("host", "") not in HOSTS | {"localhost:5173", "127.0.0.1:5173"} or ws.headers.get("origin") not in ORIGINS:
        return await ws.close(code=1008)
    await ws.accept()
    tab = await run_in_threadpool(_page_for, job_id) if d.JOB.match(job_id) else None
    if not tab:
        await ws.send_json({"error": "No window for this job in the agent's Chrome yet."})
        return await ws.close()
    try:
        # a second CDP client on the page: watching never steals focus or interferes with the agent
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as cdp:
            await cdp.send(json.dumps({"id": 1, "method": "Page.startScreencast",
                                       "params": {"format": "jpeg", "quality": 60, "maxWidth": 1280, "maxHeight": 900}}))
            await ws.send_json({"ok": True, "title": tab.get("title", ""), "url": tab.get("url", "")})
            async for msg in cdp:
                m = json.loads(msg)
                if m.get("method") == "Page.screencastFrame":
                    await ws.send_text(m["params"]["data"])
                    await cdp.send(json.dumps({"id": 2, "method": "Page.screencastFrameAck",
                                               "params": {"sessionId": m["params"]["sessionId"]}}))
    except (WebSocketDisconnect, websockets.ConnectionClosed, OSError):
        pass


# ── the React app (built into web/dist) ──

@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"error": "not found"}, 404)
    if not (WEB / "index.html").exists():
        return PlainTextResponse("The app isn't built yet: npm --prefix web run build", 503)
    f = (WEB / path).resolve()
    if path and f.is_file() and WEB.resolve() in f.parents:
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else None)
    return FileResponse(WEB / "index.html")               # client-side routes (/review/…, /live) load the app


def main() -> None:
    open_page = "--no-open" not in sys.argv
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", d.PORT)) == 0:      # the background service already serves it
            print(f"JobPilot already running → http://localhost:{d.PORT}")
            if open_page:
                d._show_in_agent_chrome()
            return
    if open_page:
        threading.Timer(1.5, d._show_in_agent_chrome).start()
    print(f"JobPilot → http://localhost:{d.PORT}" + ("  (background service)" if d.MANAGED else "  (Ctrl+C to stop)"), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=d.PORT, log_level="warning")


if __name__ == "__main__":
    main()
