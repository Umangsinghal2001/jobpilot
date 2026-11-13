// cdp-proxy — sits between an agent run (Playwright MCP + quickfill) and the agent Chrome, so the agent never
// takes focus from you while you review or correct a form in the same Chrome:
//   · every new tab opens in its OWN background window — still painted, so clicks work (a background TAB is
//     not painted and clicks time out; a minimized window neither)
//   · Page.bringToFront becomes a no-op (MCP's browser_tabs select and quickfill call it)
// Everything else passes through unchanged. Prints its port; exits when stdin closes (the run ended).
//   CDP=http://localhost:9222 node cdp-proxy.mjs  →  41234
import http from 'node:http';
import { createRequire } from 'node:module';

const { ws: WebSocket, wsServer: WebSocketServer } = createRequire(import.meta.url)('playwright-core/lib/utilsBundle');
const UP = process.env.CDP || 'http://localhost:9222';
const MAX = 512 * 1024 * 1024;   // full-page screenshots come back as big base64 messages

const srv = http.createServer(async (req, res) => {
  const r = await fetch(UP + req.url, { method: req.method }).catch(() => null);
  if (!r) { res.writeHead(502); return res.end(); }
  let body = await r.text();
  if (req.url.startsWith('/json/version')) {          // point Playwright's WebSocket at us, not at Chrome
    const v = JSON.parse(body);
    v.webSocketDebuggerUrl = `ws://127.0.0.1:${srv.address().port}${new URL(v.webSocketDebuggerUrl).pathname}`;
    body = JSON.stringify(v);
  }
  res.writeHead(r.status, { 'Content-Type': 'application/json' });
  res.end(body);
});

new WebSocketServer({ server: srv, maxPayload: MAX, perMessageDeflate: false }).on('connection', (client, req) => {
  const up = new WebSocket(UP.replace(/^http/, 'ws') + req.url, { maxPayload: MAX, perMessageDeflate: false });
  const pending = [];
  up.on('open', () => pending.splice(0).forEach(m => up.send(m)));
  up.on('message', (data, binary) => client.send(data, { binary }));
  client.on('message', raw => {
    let m;
    try { m = JSON.parse(raw.toString()); } catch { return; }
    if (m.method === 'Page.bringToFront')
      return client.send(JSON.stringify({ id: m.id, sessionId: m.sessionId, result: {} }));
    if (m.method === 'Target.createTarget')
      m.params = { ...m.params, newWindow: true, background: true, focus: false };   // focus:false — never steals focus from your window
    const out = JSON.stringify(m);
    up.readyState === WebSocket.OPEN ? up.send(out) : pending.push(out);
  });
  const end = () => { client.close(); up.close(); };
  up.on('close', end); up.on('error', end); client.on('close', end); client.on('error', end);
});

srv.listen(0, '127.0.0.1', () => console.log(srv.address().port));
process.stdin.on('end', () => process.exit(0)).resume();
