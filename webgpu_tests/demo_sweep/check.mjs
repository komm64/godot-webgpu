#!/usr/bin/env node
// CDP page runner for webgpu_tests/demo_sweep (Node 22+, built-in WebSocket & fetch).

import fs from 'node:fs';
import path from 'node:path';

const INIT_SCRIPT = `(() => {
  const sweep = { gpu: [], frames: 0, firstFrame: null };
  window.__sweep = sweep;

  if (navigator.gpu && typeof navigator.gpu.requestAdapter === 'function') {
    const origRequestAdapter = navigator.gpu.requestAdapter.bind(navigator.gpu);
    navigator.gpu.requestAdapter = async function (...args) {
      const adapter = await origRequestAdapter(...args);
      if (adapter && typeof adapter.requestDevice === 'function' && !adapter.__sweepWrapped) {
        adapter.__sweepWrapped = true;
        const origRequestDevice = adapter.requestDevice.bind(adapter);
        adapter.requestDevice = async function (...dArgs) {
          const device = await origRequestDevice(...dArgs);
          if (device && !device.__sweepWrapped) {
            device.__sweepWrapped = true;
            device.addEventListener('uncapturederror', (e) => {
              const msg = e && e.error ? (e.error.message ?? String(e.error)) : String(e);
              sweep.gpu.push(String(msg).slice(0, 300));
            });
          }
          return device;
        };
      }
      return adapter;
    };
  }

  function startFrameLoop() {
    if (window.__sweepRafStarted) return;
    window.__sweepRafStarted = true;
    function tick() {
      sweep.frames += 1;
      if (sweep.firstFrame === null) {
        const canvas = document.getElementById('canvas') || document.querySelector('canvas');
        if (canvas) {
          const rect = canvas.getBoundingClientRect();
          if (canvas.width > 0 && canvas.height > 0 && rect.width > 0 && rect.height > 0) {
            sweep.firstFrame = Math.round(performance.now());
          }
        }
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  if (document.readyState === 'complete') {
    startFrameLoop();
  } else {
    window.addEventListener('load', startFrameLoop, { once: true });
  }
})();`;

function parseArgs(argv) {
  const args = { seconds: 15 };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--help' || flag === '-h') {
      console.log(
        'Usage: node check.mjs --cdp-port <port> --url <url> --shot <png> [--seconds 15]'
      );
      process.exit(0);
    }
    const val = argv[i + 1];
    if (flag === '--cdp-port') {
      args.cdpPort = Number(val);
      i += 1;
    } else if (flag === '--url') {
      args.url = val;
      i += 1;
    } else if (flag === '--seconds') {
      args.seconds = Number(val);
      i += 1;
    } else if (flag === '--shot') {
      args.shot = val;
      i += 1;
    } else {
      throw new Error(`Unknown argument: ${flag}`);
    }
  }
  if (!args.cdpPort || !args.url || !args.shot) {
    throw new Error('Required arguments: --cdp-port, --url, --shot');
  }
  return args;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatRemoteArg(arg) {
  if (!arg) return '';
  if (arg.value !== undefined) return String(arg.value);
  if (arg.description) return String(arg.description);
  return String(arg.type || '');
}

function connectCdp(wsUrl, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let nextId = 1;
    const pending = new Map();
    const listeners = new Map();
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        try {
          ws.close();
        } catch {}
        reject(new Error(`CDP WebSocket connect timed out after ${timeoutMs}ms`));
      }
    }, timeoutMs);

    ws.addEventListener('open', () => {
      clearTimeout(timer);
      settled = true;
      resolve({
        on(method, fn) {
          if (!listeners.has(method)) listeners.set(method, []);
          listeners.get(method).push(fn);
        },
        send(method, params = {}, cmdTimeoutMs = 15000) {
          const id = nextId++;
          return new Promise((res, rej) => {
            const cmdTimer = setTimeout(() => {
              pending.delete(id);
              rej(new Error(`CDP ${method} timed out after ${cmdTimeoutMs}ms`));
            }, cmdTimeoutMs);
            pending.set(id, {
              resolve(val) {
                clearTimeout(cmdTimer);
                res(val);
              },
              reject(err) {
                clearTimeout(cmdTimer);
                rej(err);
              },
            });
            ws.send(JSON.stringify({ id, method, params }));
          });
        },
        close() {
          for (const [, p] of pending) {
            p.reject(new Error('CDP socket closed'));
          }
          pending.clear();
          try {
            ws.close();
          } catch {}
        },
      });
    });

    ws.addEventListener('message', (event) => {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === 'string' ? event.data : String(event.data));
      } catch {
        return;
      }
      if (msg.id && pending.has(msg.id)) {
        const entry = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) {
          entry.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        } else {
          entry.resolve(msg.result || {});
        }
      } else if (msg.method && listeners.has(msg.method)) {
        for (const fn of listeners.get(msg.method)) {
          try {
            fn(msg.params || {});
          } catch {}
        }
      }
    });

    ws.addEventListener('error', (err) => {
      if (!settled) {
        clearTimeout(timer);
        settled = true;
        reject(new Error(`CDP WebSocket error: ${err.message || 'connection failed'}`));
      }
    });

    ws.addEventListener('close', () => {
      for (const [, p] of pending) {
        p.reject(new Error('CDP socket closed'));
      }
      pending.clear();
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const base = `http://127.0.0.1:${args.cdpPort}`;

  // Open a blank tab first so addScriptToEvaluateOnNewDocument runs BEFORE the demo loads.
  const newRes = await fetch(`${base}/json/new?about:blank`, { method: 'PUT' });
  if (!newRes.ok) {
    throw new Error(`Failed to open CDP tab: HTTP ${newRes.status}`);
  }
  const target = await newRes.json();
  if (!target.webSocketDebuggerUrl) {
    throw new Error('CDP target missing webSocketDebuggerUrl');
  }

  const jsErrors = [];
  let cdp = null;
  let shotWritten = null;
  let sweep = { gpu: [], frames: 0, firstFrame: null };

  try {
    cdp = await connectCdp(target.webSocketDebuggerUrl);

    cdp.on('Runtime.consoleAPICalled', (params) => {
      if (params.type === 'error' || params.type === 'assert') {
        const text = (params.args || []).map(formatRemoteArg).join(' ').trim();
        if (text) jsErrors.push(text.slice(0, 300));
      }
    });

    cdp.on('Runtime.exceptionThrown', (params) => {
      const details = params.exceptionDetails || {};
      const text =
        (details.exception && details.exception.description) ||
        details.text ||
        'Uncaught exception';
      jsErrors.push(String(text).slice(0, 300));
    });

    cdp.on('Log.entryAdded', (params) => {
      const entry = params.entry || {};
      if (entry.level === 'error' && entry.text) {
        jsErrors.push(String(entry.text).slice(0, 300));
      }
    });

    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: INIT_SCRIPT });
    await cdp.send('Page.navigate', { url: args.url });

    await sleep(Math.max(0, args.seconds) * 1000);

    try {
      const evalRes = await cdp.send('Runtime.evaluate', {
        expression: 'window.__sweep',
        returnByValue: true,
      });
      if (evalRes.result && evalRes.result.value && typeof evalRes.result.value === 'object') {
        sweep = evalRes.result.value;
      }
    } catch (err) {
      jsErrors.push(`Failed to read window.__sweep: ${err.message}`.slice(0, 300));
    }

    try {
      const shotRes = await cdp.send('Page.captureScreenshot', { format: 'png' });
      if (shotRes.data) {
        fs.mkdirSync(path.dirname(args.shot), { recursive: true });
        fs.writeFileSync(args.shot, Buffer.from(shotRes.data, 'base64'));
        shotWritten = args.shot;
      }
    } catch (err) {
      jsErrors.push(`Screenshot failed: ${err.message}`.slice(0, 300));
    }
  } finally {
    if (cdp) cdp.close();
    if (target.id) {
      await fetch(`${base}/json/close/${target.id}`).catch(() => {});
    }
  }

  const gpuErrors = Array.isArray(sweep.gpu) ? sweep.gpu.map((m) => String(m).slice(0, 300)) : [];
  const firstErrors = [
    ...gpuErrors.map((m) => `[gpu] ${m}`),
    ...jsErrors.map((m) => `[js] ${m}`),
  ].slice(0, 5);

  const output = {
    first_frame_ms: typeof sweep.firstFrame === 'number' ? sweep.firstFrame : null,
    frames: typeof sweep.frames === 'number' ? sweep.frames : 0,
    gpu_errors: gpuErrors.length,
    js_errors: jsErrors.length,
    first_errors: firstErrors,
    shot: shotWritten,
  };
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

main().catch((err) => {
  process.stderr.write(`${err && err.stack ? err.stack : String(err)}\n`);
  process.exit(1);
});
