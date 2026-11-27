"""Static web dashboard for GitHub Pages, built only from the versioned public store (public_data/).

No server, so it never sleeps; no network requests (CSP default-src 'none'); no model calls. The exporter
(``analysis.public_export``) rebuilds it after every export or release, so the page cannot drift from the store.

    python -m analysis.static_site build    # write docs/dashboard/index.html, docs/index.html, docs/.nojekyll
    python -m analysis.static_site check    # rebuild in memory and compare bytes (needs only public_data/)

GitHub Pages: Settings → Pages → Deploy from a branch → <branch> / docs. The dashboard is then served at
https://<owner>.github.io/<repository>/ (redirect) and /dashboard/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analysis import submission_bundle as sb

CHECKOUT = sb.CHECKOUT
STORE = Path("public_data")
PAGE = Path("docs/dashboard/index.html")
REDIRECT = Path("docs/index.html")
NOJEKYLL = Path("docs/.nojekyll")
FIGURE_SET = {"success-rate.svg": "success_rate", "tokens.svg": "tokens", "paired-effects.svg": "paired_effects",
              "task-matrix.svg": "task_matrix", "recovery-cases.svg": "recovery_cases"}
MANIFEST_FIELDS = ("exported_at", "status_updated_at", "data_sha256", "content_sha256", "source", "status_history")
REDIRECT_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta http-equiv="refresh" content="0; url=dashboard/">
<title>AAH 실험 결과 대시보드</title>
</head>
<body style="font-family: system-ui, sans-serif; margin: 2rem">
<p><a href="dashboard/">AAH 실험 결과 대시보드로 이동</a></p>
</body>
</html>
"""


class SiteError(RuntimeError):
    pass


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def site_payload(root: Path = CHECKOUT) -> tuple[dict, dict]:
    """(data, figures) for the page, read from the public store with integrity checks."""
    from analysis import submission_figures as sf

    store = root / STORE
    if not (store / "catalog.json").is_file():
        raise SiteError("public_data/catalog.json is missing; run python -m analysis.public_export export first")
    catalog = _json(store / "catalog.json")
    experiments, manifests, figures = {}, {}, {"_global": {"architecture.svg": sf.architecture()}}
    for entry in catalog["experiments"]:
        folder = store / entry["path"]
        manifest = _json(folder / "MANIFEST.json")
        body = (folder / "export.json").read_bytes()
        if sb.sha256(body) != manifest["files"]["export.json"]:
            raise SiteError(f"{entry['key']}: export.json does not match MANIFEST.json")
        export = json.loads(body.decode("utf-8"))
        experiments[entry["key"]] = export
        manifests[entry["key"]] = {k: manifest.get(k) for k in MANIFEST_FIELDS}
        if export.get("conditions") and export.get("contrasts"):
            figures[entry["key"]] = {name: getattr(sf, fn)(export) for name, fn in FIGURE_SET.items()}
    synthetic = [_json(store / s["path"] / "export.json") for s in catalog.get("synthetic", [])]
    releases = {r["release_id"]: _json(store / "releases" / f"{r['release_id']}.json") for r in catalog["releases"]}
    data = {"schema_version": sb.SCHEMA_VERSION, "kind": "site", "catalog": catalog, "experiments": experiments,
            "manifests": manifests, "synthetic": synthetic, "releases": releases}
    sb.assert_public(data)
    return data, figures


def render(root: Path = CHECKOUT) -> dict[Path, bytes]:
    data, figures = site_payload(root)
    data_bytes = sb.canonical(data)
    dashboard = root / "analysis/dashboard"
    page = ((dashboard / "template.html").read_text(encoding="utf-8")
            .replace("{{STYLE}}", (dashboard / "style.css").read_text(encoding="utf-8").strip())
            .replace("{{APP}}", (dashboard / "app.js").read_text(encoding="utf-8").strip())
            .replace("{{TITLE}}", "실험 결과 대시보드")
            .replace("{{SUBTITLE}}", "버전별 공개 데이터(public_data)에서 생성한 정적 페이지 · 서버 없음")
            .replace("{{BUNDLE_SHA256}}", sb.sha256(data_bytes))
            .replace("{{FIGURES}}", sb._embed(sb.canonical(figures)))
            .replace("{{DATA}}", sb._embed(data_bytes)))
    return {PAGE: page.replace("\r\n", "\n").encode("utf-8"), REDIRECT: REDIRECT_HTML.encode("utf-8"), NOJEKYLL: b""}


def build(root: Path = CHECKOUT) -> list[str]:
    outputs = render(root)
    for rel, data in outputs.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return [rel.as_posix() for rel in outputs]


def check(root: Path = CHECKOUT) -> list[str]:
    return [rel.as_posix() for rel, data in render(root).items()
            if not (root / rel).is_file() or (root / rel).read_bytes() != data]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("build", "check"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            written = build()
            print("STATIC_SITE_WRITTEN", " ".join(written))
            return 0
        differ = check()
        print("STATIC_SITE_REPRODUCED" if not differ else "STATIC_SITE_DIFFERS " + " ".join(differ))
        return 0 if not differ else 1
    except (SiteError, sb.BundleError, OSError, ValueError) as error:
        print("STATIC_SITE_BLOCKED", error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
