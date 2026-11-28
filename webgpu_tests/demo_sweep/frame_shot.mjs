// node frame_shot.mjs <cdpPort> <url> <frames> <width> <height> <out.png>
// Holds requestAnimationFrame after <frames> callbacks and screenshots the page.
import fs from 'fs';
const [port, url, frames, width, height, out] = process.argv.slice(2);
const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pend = {};
const send = (m, p = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method: m, params: p })); });
ws.onmessage = e => { const d = JSON.parse(e.data); if (d.id && pend[d.id]) pend[d.id](d.result); };
await new Promise(r => ws.onopen = r);
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: +height, deviceScaleFactor: 1, mobile: false });
await send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__f = 0; window.__frozen = false; const N = ${+frames}; const oraf = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = function (cb) { return oraf(function (t) { if (window.__frozen) return; window.__f++; cb(t); if (window.__f >= N) window.__frozen = true; }); };` });
await send('Page.navigate', { url });
for (let i = 0; i < 240; i++) { await new Promise(r => setTimeout(r, 500)); const v = await send('Runtime.evaluate', { expression: 'window.__frozen', returnByValue: true }); if (v.result.value) break; }
await new Promise(r => setTimeout(r, 1000));
const s = await send('Page.captureScreenshot', { format: 'png' });
fs.writeFileSync(out, Buffer.from(s.data, 'base64'));
process.exit(0);
