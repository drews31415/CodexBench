// Renders every dashboard view for every release, experiment and selection without a browser (used by
// test_submission_bundle.py and test_static_site.py). Usage: node dashboard_render.js <dashboard.html>
// Prints one JSON line: {kind, views, releases, graphs, cases, fails: [...], probes: {...}}.
"use strict";
const fs = require("fs");
const vm = require("vm");

const page = fs.readFileSync(process.argv[2], "utf8");
const grab = (id) => {
  const open = '<script id="' + id + '" type="application/json">';
  const start = page.indexOf(open);
  if (start < 0) throw new Error("missing " + id);
  const body = page.slice(start + open.length, page.indexOf("</script>", start));
  return JSON.parse(body.split("<\\!--").join("<!--").split("<\\/").join("</"));
};
const open = "<script>\n";
const at = page.lastIndexOf(open);
const app = page.slice(at + open.length, page.indexOf("</script>", at));
const ctx = { console };
vm.createContext(ctx);
vm.runInContext(app, ctx);
const A = ctx.AAHDashboard;
const data = grab("aah-data");
const info = A.init(data, grab("aah-figures"));
const bad = /undefined|NaN|\[object Object\]|화면을 그리는 중 오류/;
const fails = [];
const probes = {};
const graphs = new Set();
const cases = new Set();
const repos = new Set();
let views = 0;
const enc = encodeURIComponent;
const check = (label, html) => {
  views += 1;
  const m = html.match(bad);
  if (m) fails.push(label + " :: " + html.slice(Math.max(0, m.index - 100), m.index + 40));
  return html;
};

probes.overview = check("overview default", A.view(A.parseHash("#overview")));
for (const rid of info.releases) {
  const scope = A.options({ tab: "overview", release: rid });
  probes["release:" + rid] = check("overview " + rid, A.view(A.parseHash("#overview?release=" + enc(rid))));
  for (const e of scope.exps) {
    const base = "release=" + enc(rid) + "&exp=" + enc(e);
    for (const tab of ["overview", "benchmark", "audit"]) {
      probes[tab + ":" + rid + ":" + e] = check(tab + " " + rid + " " + e, A.view(A.parseHash("#" + tab + "?" + base)));
    }
    for (const task of Object.keys((data.experiments[e] || {}).tasks || {})) {
      for (const c of A.COND) {
        check("trace " + base + " " + task + " " + c, A.view(A.parseHash("#trace?" + base + "&task=" + task + "&cond=" + c)));
        check("context " + base + " " + task + " " + c, A.view(A.parseHash("#context?" + base + "&task=" + task + "&cond=" + c)));
      }
    }
    const o = A.options({ tab: "graph", release: rid, exp: e });
    for (const g of o.graphKeys) {
      graphs.add(g);
      probes["graph:" + g] = check("graph " + g, A.view(A.parseHash("#graph?" + base + "&graph=" + enc(g))));
    }
    for (const t of o.repoTasks || []) {
      repos.add(t);
      probes["repo:" + t] = check("repo graph " + base + " " + t, A.view(A.parseHash("#graph?" + base + "&gmode=repo&rtask=" + enc(t))));
    }
    for (const k of o.caseKeys) {
      cases.add(k);
      for (let s = 0; s < info.steps; s += 1) {
        check("replay " + k + " " + s, A.view(A.parseHash("#replay?" + base + "&case=" + enc(k) + "&step=" + s)));
      }
    }
  }
}
probes.badHash = check("bad hash", A.view(A.parseHash("#nope?release=zzz&exp=zzz&task=Q&cond=Z&step=99&graph=x&case=y")));
if (A.picks) {
  probes.picks = JSON.stringify(A.picks({}), (k, v) => (k === "run" || k === "iv" || k === "exp" && typeof v === "object" ? undefined : v));
  probes.badGraph = check("bad graph", A.view(A.parseHash("#graph?graph=nope")));
  probes.badRepo = check("bad repo", A.view(A.parseHash("#graph?gmode=repo&rtask=nope")));
  probes.badCase = check("bad case", A.view(A.parseHash("#replay?case=nope")));
}
if (A.stamp) probes.stamps = [A.stamp("2026-11-27T21:30:00Z"), A.stamp("2026-12-31T15:05:00Z"), A.stamp("2026-11-27T12:12:33Z")];
const summary = { kind: info.kind, releases: info.releases.length, graphs: graphs.size, cases: cases.size, repos: repos.size, steps: info.steps };
A.init(null, {});
probes.empty = A.view(A.parseHash("#overview"));
A.init({ experiments: {}, synthetic: { runs: {} } }, {});
for (const tab of ["overview", "benchmark", "trace", "graph", "context", "replay", "audit"]) {
  check("no experiments " + tab, A.view(A.parseHash("#" + tab)));
}
A.init({ schema_version: "1.0", catalog: { releases: [], experiments: [] }, experiments: {}, synthetic: [], releases: {} }, {});
for (const tab of ["overview", "benchmark", "trace", "graph", "context", "replay", "audit"]) {
  check("empty site " + tab, A.view(A.parseHash("#" + tab)));
}
process.stdout.write(JSON.stringify({ views, ...summary, fails, probes }) + "\n");
