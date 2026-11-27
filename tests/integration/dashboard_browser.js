// Real-browser check of the static dashboard (docs/dashboard/index.html) through the Chrome DevTools Protocol.
// No packages: Node 22+ (global fetch/WebSocket) and a local Chrome/Chromium/Edge. No network, no model calls.
// Usage: node dashboard_browser.js <index.html> [screenshot-dir]
// Prints one JSON line {checks, fails: [...], shots: [...], notes: {...}}; exit code 0 even with fails (pytest asserts).
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const page = path.resolve(process.argv[2]);
const shotDir = process.argv[3] || "";
const fileUrl = "file:///" + page.replace(/\\/g, "/").replace(/^\/+/, "");
const candidates = [process.env.CHROME, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter(Boolean);
const chrome = candidates.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } });
if (!chrome) { process.stdout.write(JSON.stringify({ skipped: "no Chrome" }) + "\n"); process.exit(0); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const shots = [];
const notes = {};
let checks = 0;
const ok = (cond, label) => { checks += 1; if (!cond) fails.push(label); return cond; };

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "aah-cdp-"));
  const proc = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = "";
    const t = setTimeout(() => reject(new Error("Chrome did not start")), 20000);
    proc.stderr.on("data", (d) => { buf += d; const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf); if (m) { clearTimeout(t); resolve(m[1]); } });
  });
  const port = /:(\d+)\//.exec(wsUrl)[1];
  const list = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
  const target = list.find((x) => x.type === "page");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0;
  const pending = new Map();
  const events = [];
  const waiters = [];
  ws.addEventListener("message", (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); if (msg.error) p.reject(new Error(msg.error.message)); else p.resolve(msg.result); return; }
    if (msg.method) { events.push(msg); waiters.slice().forEach((w) => { if (w.method === msg.method) { waiters.splice(waiters.indexOf(w), 1); w.resolve(msg); } }); }
  });
  const send = (method, params) => new Promise((resolve, reject) => { id += 1; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params: params || {} })); });
  const waitFor = (method, ms) => new Promise((resolve) => { const w = { method, resolve }; waiters.push(w); setTimeout(() => { const i = waiters.indexOf(w); if (i >= 0) { waiters.splice(i, 1); resolve(null); } }, ms || 8000); });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("eval: " + (r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text));
    return r.result.value;
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Log.enable");
  await send("Network.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));" });

  const setView = async (w, h, mobile, scheme) => {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }, { name: "prefers-reduced-motion", value: "reduce" }] });
  };
  const load = async (hash) => {
    const done = waitFor("Page.loadEventFired", 15000);
    await send("Page.navigate", { url: fileUrl + hash });
    await done;
    await sleep(250);
  };
  const go = async (hash) => { await evalJs("location.hash=" + JSON.stringify(hash) + ";0"); await sleep(250); };
  const shot = async (name) => {
    if (!shotDir) return;
    const m = await send("Page.getLayoutMetrics");
    const cs = m.cssContentSize || m.contentSize;
    const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: Math.ceil(cs.width), height: Math.min(Math.ceil(cs.height), 16000), scale: 1 } });
    const file = path.join(shotDir, name + ".png");
    fs.writeFileSync(file, Buffer.from(r.data, "base64"));
    shots.push(file);
  };

  // Layout probes run inside the page.
  const PROBE = `(() => {
    const out = { overflow: [], textOverlap: [], textOutside: [], nodeOverlap: [], errors: [] };
    const W = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > W + 1) {
      const bad = [...document.querySelectorAll('main *')].filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.right <= W + 1) return false;
        return !el.closest('.tablewrap, .gview, .tabs, .fig');
      }).slice(0, 5).map((el) => el.tagName + '.' + String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className).slice(0, 40) + ' right=' + Math.round(el.getBoundingClientRect().right));
      out.overflow.push('scrollWidth ' + document.documentElement.scrollWidth + ' > ' + W + ' ' + bad.join(' | '));
    }
    const visible = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const inter = (a, b, pad) => a.left < b.right - pad && b.left < a.right - pad && a.top < b.bottom - pad && b.top < a.bottom - pad;
    document.querySelectorAll('svg.arch, svg.flow').forEach((svg) => {
      if (!visible(svg)) return;
      const texts = [...svg.querySelectorAll('text')].filter(visible).map((t) => ({ t: t.textContent, r: t.getBoundingClientRect(), node: t.closest('.anode') }));
      for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) if (inter(texts[i].r, texts[j].r, 1)) out.textOverlap.push('arch: ' + texts[i].t + ' / ' + texts[j].t);
      texts.forEach((x) => { if (!x.node) return; const b = x.node.querySelector('rect.nb').getBoundingClientRect(); if (x.r.left < b.left - 0.5 || x.r.right > b.right + 0.5) out.textOutside.push('arch: ' + x.t); });
      const boxes = [...svg.querySelectorAll('rect.nb')].map((r) => r.getBoundingClientRect());
      const lines = [...svg.querySelectorAll('text')].filter(visible).filter((t) => !t.closest('.anode'));
      lines.forEach((t) => { const r = t.getBoundingClientRect(); boxes.forEach((b) => { if (inter(r, b, 1)) out.textOverlap.push('arch label over node: ' + t.textContent); }); });
    });
    document.querySelectorAll('svg.gsvg').forEach((svg) => {
      const nodes = [...svg.querySelectorAll('.gn')].filter((g) => g.style.display !== 'none');
      const rects = nodes.map((g) => ({ g, r: g.querySelector('rect.nbox').getBoundingClientRect() }));
      rects.forEach((a, i) => {
        rects.slice(i + 1).forEach((b) => { if (inter(a.r, b.r, 1)) out.nodeOverlap.push(a.g.dataset.id + ' / ' + b.g.dataset.id); });
        [...a.g.querySelectorAll('text')].forEach((t) => { const r = t.getBoundingClientRect(); if (r.width && (r.left < a.r.left - 0.5 || r.right > a.r.right + 0.5)) out.textOutside.push('graph: ' + t.textContent); });
        const lb = a.g.querySelector('.lbt'), rg = a.g.querySelector('.rgt');
        if (lb && rg && inter(lb.getBoundingClientRect(), rg.getBoundingClientRect(), 0.5)) out.textOverlap.push('graph label/badge: ' + lb.textContent + ' / ' + rg.textContent);
      });
    });
    if (document.querySelector('#view .err')) out.errors.push(document.querySelector('#view .err').textContent.slice(0, 160));
    out.csp = window.__csp || [];
    return out;
  })()`;
  const probe = async (label) => {
    const r = await evalJs(PROBE);
    ok(!r.overflow.length, label + " horizontal overflow: " + r.overflow.join("; "));
    ok(!r.textOverlap.length, label + " text overlap: " + r.textOverlap.slice(0, 4).join("; "));
    ok(!r.textOutside.length, label + " text outside its box: " + r.textOutside.slice(0, 4).join("; "));
    ok(!r.nodeOverlap.length, label + " graph nodes overlap: " + r.nodeOverlap.slice(0, 4).join("; "));
    ok(!r.errors.length, label + " render error: " + r.errors.join("; "));
    ok(!r.csp.length, label + " CSP violation: " + r.csp.join("; "));
  };

  const data = await (async () => { await setView(1366, 900, false, "light"); await load(""); return evalJs("(() => { const A = window.AAHDashboard; const o = A.options({}); return { tasks: o.repoTasks, graphs: o.graphKeys, picks: A.picks({}) && { ok: (A.picks({}).ok || {}).run_id, fail: (A.picks({}).fail || {}).run_id, dcc: [(A.picks({}).dcc || {}).task_id, (A.picks({}).dcc || {}).condition], exp: A.picks({}).exp, syn: (A.picks({}).graphSyn || {}).key, live: (A.picks({}).graphLive || {}).key, repo: (A.picks({}).repo || {}).task_id } }; })()"); })();
  notes.picks = data.picks;
  const exp = data.picks.exp;
  const screens = [
    ["overview", "#overview"], ["benchmark", "#benchmark"], ["trace", "#trace?task=AAH-D04&cond=E"],
    ["graph-repo", "#graph?gmode=repo&rtask=" + data.picks.repo], ["graph-diag-synthetic", "#graph?gmode=diag&graph=" + encodeURIComponent(data.picks.syn)],
    ["graph-diag-live", "#graph?gmode=diag&graph=" + encodeURIComponent(data.picks.live)], ["context", "#context?task=" + data.picks.dcc[0] + "&cond=" + data.picks.dcc[1]],
    ["replay", "#replay?case=" + encodeURIComponent(exp + "|" + data.picks.ok) + "&step=3"], ["audit", "#audit"],
  ];
  const modes = [["desktop", 1366, 900, false], ["mobile", 390, 844, true]];
  for (const [mname, w, h, mobile] of modes) {
    for (const scheme of ["light", "dark"]) {
      await setView(w, h, mobile, scheme);
      await load("");
      for (const [name, hash] of screens) {
        await go(hash);
        await probe(mname + "/" + scheme + "/" + name);
        await shot(mname + "-" + scheme + "-" + name);
      }
      for (const t of data.tasks) { await go("#graph?gmode=repo&rtask=" + t); await probe(mname + "/" + scheme + "/graph-repo " + t); }
      for (const g of data.graphs) { await go("#graph?gmode=diag&graph=" + encodeURIComponent(g)); await probe(mname + "/" + scheme + "/graph-diag " + g.slice(0, 40)); }
    }
  }
  // architecture: every condition view, desktop and mobile
  for (const [mname, w, h, mobile] of modes) {
    await setView(w, h, mobile, "light");
    await load("#overview");
    await evalJs("document.querySelector('details.archfold').open = true;0");
    for (const c of ["A", "B", "C", "D", "E", "all"]) {
      await evalJs("document.querySelector('[data-arch=\"" + c + "\"]').click();0");
      await sleep(60);
      ok(await evalJs("document.querySelector('.archbox').dataset.cond") === c, mname + " arch condition " + c);
      await probe(mname + "/arch " + c);
    }
  }

  // ---- interactions (desktop, light)
  await setView(1366, 900, false, "light");
  await load("#overview");
  ok(await evalJs("document.querySelector('[data-tab=overview]').getAttribute('aria-selected')") === "true", "overview tab selected on load");
  await evalJs("document.querySelector('[data-tab=benchmark]').click();0"); await sleep(150);
  await evalJs("document.querySelector('[data-tab=audit]').click();0"); await sleep(150);
  ok((await evalJs("location.hash")).startsWith("#audit"), "tab click updates hash");
  await evalJs("history.back();0"); await sleep(400);
  ok((await evalJs("location.hash")).startsWith("#benchmark") && await evalJs("document.querySelector('[data-tab=benchmark]').getAttribute('aria-selected')") === "true", "browser back returns to the previous tab");
  await evalJs("history.forward();0"); await sleep(400);
  ok((await evalJs("location.hash")).startsWith("#audit"), "browser forward");
  // keyboard tabs
  await evalJs("document.querySelector('[data-tab=audit]').focus();0");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 });
  await sleep(200);
  ok((await evalJs("location.hash")).startsWith("#replay") && await evalJs("document.activeElement.dataset.tab") === "replay", "arrow key moves between tabs");
  // quick-tour links resolve to representative cases
  await load("#overview");
  const quick = await evalJs("[...document.querySelectorAll('a.qlink, a.audit-link')].map(a => a.getAttribute('href'))");
  ok(quick.length === 5 && quick.every(Boolean), "four feature links and the audit link have targets");
  for (const href of quick) {
    await go(href);
    const r = await evalJs("({ notice: !!document.querySelector('.callout.warn[role=status]'), err: !!document.querySelector('#view .err'), hash: location.hash })");
    ok(!r.notice && !r.err, "quick link opens without a not-found notice: " + href);
  }
  // replay controls
  await go("#replay?case=" + encodeURIComponent(exp + "|" + data.picks.ok) + "&step=0");
  ok(await evalJs("document.querySelectorAll('.stepper li').length") === 8, "replay shows 8 steps");
  await evalJs("document.querySelector('[data-act=next]').click();0"); await sleep(150);
  ok(/step=1/.test(await evalJs("location.hash")), "replay next");
  await evalJs("document.querySelector('[data-act=first]').click();0"); await sleep(150);
  ok(/step=0/.test(await evalJs("location.hash")), "replay first");
  const histBefore = await evalJs("history.length");
  await evalJs("document.querySelector('[data-act=play]').click();0"); await sleep(200);
  ok(/일시정지/.test(await evalJs("document.querySelector('[data-act=play]').textContent")), "play button becomes pause");
  await sleep(4000);
  const stepNow = +(/step=(\d+)/.exec(await evalJs("location.hash")) || [0, 0])[1];
  ok(stepNow >= 1, "auto-play advances (step " + stepNow + ")");
  await evalJs("document.querySelector('[data-act=play]').click();0"); await sleep(300);
  const paused = await evalJs("location.hash");
  await sleep(4000);
  ok(paused === await evalJs("location.hash") && /재생/.test(await evalJs("document.querySelector('[data-act=play]').textContent")), "pause stops auto-play");
  ok(await evalJs("history.length") === histBefore, "auto-play does not add history entries");
  // graph interactions
  await go("#graph?gmode=diag&graph=" + encodeURIComponent(data.picks.syn));
  const vb0 = await evalJs("document.querySelector('.gsvg').getAttribute('viewBox')");
  await evalJs("document.querySelector('[data-gz=in]').click();0");
  ok(await evalJs("document.querySelector('.gsvg').getAttribute('viewBox')") !== vb0, "zoom in changes the viewport");
  await evalJs("document.querySelector('[data-gz=fit]').click();0");
  ok(await evalJs("document.querySelector('.gsvg').getAttribute('viewBox')") === vb0.split(" ").map((v) => (+v).toFixed(1)).join(" "), "fit restores the full view");
  await evalJs("document.querySelector('.gn.cand').dispatchEvent(new MouseEvent('click', {bubbles: true}));0"); await sleep(100);
  ok(await evalJs("!!document.querySelector('.gn.sel') && /원인 후보/.test(document.querySelector('.gdetail').textContent)"), "clicking a candidate shows its detail");
  ok(/gnode=/.test(await evalJs("location.hash")), "selected node is kept in the address");
  await evalJs("document.querySelector('[data-gf^=\"k:FILE\"]').click();0");
  ok(await evalJs("[...document.querySelectorAll('.gn[data-kind=FILE]')].every(g => g.style.display === 'none')"), "node filter hides FILE nodes");
  await evalJs("document.querySelector('[data-gpath]').click();0");
  ok(await evalJs("document.querySelector('.gsvg').classList.contains('pathonly')"), "path highlight toggles");
  await evalJs("document.querySelector('[data-gz=reset]').click();0");
  ok(await evalJs("!document.querySelector('.gn.sel') && [...document.querySelectorAll('.gn')].every(g => g.style.display === '')"), "reset clears selection and filters");
  // copy button
  await go("#audit");
  await evalJs("document.querySelector('[data-copy]').click();0"); await sleep(300);
  ok(/복사됨|Ctrl/.test(await evalJs("document.querySelector('[data-copy]').textContent")), "copy button responds");
  ok(/^[0-9a-f]{64}$/.test(await evalJs("(document.querySelector('meta[name=aah-bundle-sha256]')||{}).content")), "page carries the full data hash");
  // bad addresses
  await go("#nope?release=zzz&exp=zzz");
  ok(await evalJs("!!document.querySelector('.callout.warn[role=status]') && document.querySelector('[data-tab=overview]').getAttribute('aria-selected') === 'true'"), "unknown screen and data basis show a notice");
  await go("#replay?case=missing&step=99");
  ok(await evalJs("/찾지 못해/.test(document.querySelector('#view').textContent) && document.querySelectorAll('.stepper li.on').length === 1"), "missing replay case shows a notice and a valid case");
  await go("#graph?gmode=repo&rtask=AAH-ZZZ");
  ok(await evalJs("/그래프가 없어/.test(document.querySelector('#view').textContent) && !!document.querySelector('.gsvg')"), "missing repository graph task shows a notice");
  // theme toggle
  await go("#overview");
  await evalJs("document.querySelector('[data-act=theme]').click();0");
  ok(await evalJs("document.documentElement.dataset.theme") === "light", "theme toggle: light");
  await evalJs("document.querySelector('[data-act=theme]').click();0");
  ok(await evalJs("document.documentElement.dataset.theme") === "dark" && await evalJs("getComputedStyle(document.body).backgroundColor") === "rgb(33, 33, 33)", "theme toggle: dark colors");
  await evalJs("document.querySelector('[data-act=theme]').click();0");

  // nothing left the file: no network requests other than the page itself and data: URLs; no console errors
  const requests = events.filter((e) => e.method === "Network.requestWillBeSent").map((e) => e.params.request.url).filter((u) => !u.startsWith("file:") && !u.startsWith("data:"));
  ok(!requests.length, "no network requests: " + requests.slice(0, 3).join(", "));
  const errors = events.filter((e) => (e.method === "Runtime.exceptionThrown") || (e.method === "Runtime.consoleAPICalled" && e.params.type === "error") ||
    (e.method === "Log.entryAdded" && e.params.entry.level === "error")).map((e) => JSON.stringify(e.params).slice(0, 200));
  ok(!errors.length, "no console errors: " + errors.slice(0, 3).join(" | "));
  ws.close();
  proc.kill();
  await sleep(300);
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* Chrome may still hold files */ }
  process.stdout.write(JSON.stringify({ checks, fails, shots, notes }) + "\n");
})().catch((err) => { process.stdout.write(JSON.stringify({ checks, fails: fails.concat(["harness error: " + (err && err.stack || err)]), shots, notes }) + "\n"); process.exit(0); });
