"""Generate a static HTML dashboard for the Clara assignment demo.

Usage:
    python scripts/build_dashboard.py

The script reads committed artifacts under outputs/accounts/ and writes
dashboard/index.html for use in the presentation video.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_DIR = ROOT / "outputs" / "accounts"
DASHBOARD_PATH = ROOT / "dashboard" / "index.html"


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _hours_summary(hours: dict) -> str:
    if not hours:
        return "Hours not captured"
    days = ", ".join(hours.get("days") or []) or "Days unconfirmed"
    start = hours.get("start") or "?"
    end = hours.get("end") or "?"
    return f"{days} | {start}-{end}"


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    seen = set()
    for piece in value.split(";"):
        cleaned = piece.strip()
        if not cleaned or not any(ch.isalnum() for ch in cleaned):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        parts.append(cleaned)
    return "; ".join(parts)


def _case_summary(case_dir: Path) -> dict:
    case_id = case_dir.name
    memo_v1 = _load_json(case_dir / "v1" / "account_memo.json") or {}
    memo_v2 = _load_json(case_dir / "v2" / "account_memo.json") or {}
    changelog = _load_json(case_dir / "changelog" / "changes.json") or {}
    processing_log = _load_json(case_dir / "processing_log.json") or []

    latest_memo = memo_v2 or memo_v1
    latest_source = "demo"
    if processing_log:
        latest_source = processing_log[-1].get("source", latest_source)

    summary = changelog.get("summary") or {}
    has_v2 = bool(memo_v2)
    return {
        "case_id": case_id,
        "company_name": latest_memo.get("company_name") or case_id,
        "industry": latest_memo.get("industry") or "Unknown",
        "status": "v2 ready" if has_v2 else "demo sample",
        "latest_version": "v2" if has_v2 else "v1",
        "source": latest_source,
        "timezone": ((latest_memo.get("business_hours") or {}).get("timezone")) or "Unconfirmed",
        "hours": _hours_summary(latest_memo.get("business_hours") or {}),
        "unresolved_unknowns": len(latest_memo.get("questions_or_unknowns") or []),
        "fields_changed": summary.get("fields_changed", 0),
        "conflicts": summary.get("conflicts_detected", 0),
        "notes": _clean_text(latest_memo.get("notes") or ""),
        "after_hours": latest_memo.get("after_hours_flow_summary") or "",
        "is_deliverable": has_v2,
    }


def _build_payload() -> dict:
    cases = []
    if ACCOUNTS_DIR.exists():
        for case_dir in sorted(path for path in ACCOUNTS_DIR.iterdir() if path.is_dir()):
            cases.append(_case_summary(case_dir))

    deliverable_cases = [case for case in cases if case["is_deliverable"]]
    return {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "summary": {
        "deliverable_cases": len(deliverable_cases),
        "ready_cases": len(deliverable_cases),
        "extra_cases": sum(1 for case in cases if not case["is_deliverable"]),
        "total_unknowns": sum(case["unresolved_unknowns"] for case in deliverable_cases),
        "total_conflicts": sum(case["conflicts"] for case in deliverable_cases),
      },
      "cases": cases,
    }


def _render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    generated = payload["generated_at"]
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Clara AI Demo Dashboard</title>
  <style>
    :root {{
      --ink: #162028;
      --muted: #5d6b74;
      --line: rgba(16, 34, 44, 0.12);
      --paper: rgba(255, 252, 246, 0.82);
      --teal: #0e7c86;
      --gold: #d9a441;
      --ok: #2b7a4b;
      --warn: #a85d1a;
      --shadow: 0 18px 50px rgba(22, 32, 40, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Bahnschrift, "Aptos", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(14, 124, 134, 0.15), transparent 36%),
        radial-gradient(circle at top right, rgba(203, 90, 47, 0.16), transparent 34%),
        linear-gradient(160deg, #f5efe4 0%, #f7f8f4 48%, #eef4f1 100%);
      min-height: 100vh;
    }}
    .shell {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .hero, .panel, .case-card, .metric {{
      background: var(--paper);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }}
    .hero {{
      border-radius: 28px;
      overflow: hidden;
      position: relative;
      padding: 28px;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -40px -40px auto;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: linear-gradient(135deg, rgba(14,124,134,0.15), rgba(217,164,65,0.22));
      filter: blur(8px);
    }}
    .eyebrow {{
      letter-spacing: 0.16em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--teal);
      margin-bottom: 10px;
      font-weight: 700;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(32px, 5vw, 58px);
      line-height: 0.98;
      max-width: 760px;
    }}
    .hero-copy {{
      max-width: 720px;
      color: var(--muted);
      font-size: 16px;
      margin: 14px 0 0;
      line-height: 1.5;
    }}
    .stamp {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      margin-top: 18px;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(14, 124, 134, 0.1);
      color: var(--teal);
      font-weight: 700;
      font-size: 13px;
    }}
    .grid {{ display: grid; gap: 16px; }}
    .metrics {{
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-top: 20px;
    }}
    .metric {{ border-radius: 22px; padding: 18px 20px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }}
    .metric-value {{ font-size: 34px; font-weight: 700; margin-top: 8px; }}
    .layout {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-top: 18px;
    }}
    .panel {{ border-radius: 22px; padding: 22px; }}
    .panel h2 {{ margin: 0 0 14px; font-size: 18px; }}
    .legend-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 0;
      border-top: 1px solid var(--line);
    }}
    .legend-row:first-of-type {{ border-top: 0; padding-top: 0; }}
    .legend-title {{ font-weight: 700; }}
    .legend-copy {{ color: var(--muted); font-size: 14px; max-width: 420px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 700;
      background: rgba(22, 32, 40, 0.06);
      color: var(--ink);
    }}
    .pill.ok {{ background: rgba(43, 122, 75, 0.12); color: var(--ok); }}
    .pill.warn {{ background: rgba(217, 164, 65, 0.18); color: var(--warn); }}
    .pill.demo {{ background: rgba(14, 124, 134, 0.12); color: var(--teal); }}
    .cases {{
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      margin-top: 18px;
    }}
    .case-card {{
      border-radius: 22px;
      padding: 18px;
      transform: translateY(16px);
      opacity: 0;
      animation: rise 560ms ease forwards;
    }}
    .case-card:nth-child(2) {{ animation-delay: 80ms; }}
    .case-card:nth-child(3) {{ animation-delay: 140ms; }}
    .case-card:nth-child(4) {{ animation-delay: 200ms; }}
    .case-card:nth-child(5) {{ animation-delay: 260ms; }}
    .case-card:nth-child(6) {{ animation-delay: 320ms; }}
    .case-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    .case-id {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }}
    .case-name {{ margin: 6px 0 0; font-size: 22px; line-height: 1.05; }}
    .case-meta {{ display: grid; gap: 10px; margin-top: 18px; }}
    .case-meta div {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 14px;
    }}
    .case-meta dt {{ color: var(--muted); }}
    .case-meta dd {{ margin: 0; text-align: right; font-weight: 600; }}
    .note {{
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      min-height: 72px;
    }}
    .spotlight {{ margin-top: 18px; }}
    .spotlight-item {{ padding: 12px 0; border-top: 1px solid var(--line); }}
    .spotlight-item:first-of-type {{ border-top: 0; padding-top: 0; }}
    .spotlight-item h3 {{ margin: 0 0 4px; font-size: 16px; }}
    .spotlight-item p {{ margin: 0; color: var(--muted); line-height: 1.5; font-size: 14px; }}
    @keyframes rise {{
      from {{ transform: translateY(16px); opacity: 0; }}
      to {{ transform: translateY(0); opacity: 1; }}
    }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .shell {{ width: min(100% - 20px, 1180px); padding-top: 20px; }}
      .hero {{ padding: 22px; border-radius: 24px; }}
    }}
  </style>
</head>
<body>
  <main class=\"shell\">
    <section class=\"hero\">
      <div class=\"eyebrow\">Clara Assignment Demo</div>
      <h1>Versioned voice-agent outputs, onboarding deltas, and readiness in one screen.</h1>
      <p class=\"hero-copy\">
        This dashboard is generated from the committed pipeline artifacts under outputs/accounts.
        It is designed for the video walkthrough, so the repo can show demo intake, onboarding
        updates, and v2 readiness without any hosted dependency.
      </p>
      <div class=\"stamp\">Generated {generated}</div>
      <div class=\"grid metrics\" id=\"metrics\"></div>
    </section>

    <section class=\"layout\">
      <article class=\"panel\">
        <h2>Pipeline Readiness</h2>
        <div id=\"readiness\"></div>
      </article>
      <article class=\"panel\">
        <h2>Video Walkthrough Beats</h2>
        <div class=\"spotlight\" id=\"spotlight\"></div>
      </article>
    </section>

    <section class=\"grid cases\" id=\"cases\"></section>
  </main>

  <script>
    const data = {data_json};

    const metrics = [
      ["Deliverable Pairs", data.summary.deliverable_cases],
      ["V2 Ready", data.summary.ready_cases],
      ["Extra Samples", data.summary.extra_cases],
      ["Open Unknowns", data.summary.total_unknowns],
      ["Flagged Conflicts", data.summary.total_conflicts],
    ];

    const readinessItems = [
      {{
        title: "Transcript + Form Coverage",
        copy: (
          "The n8n export checks for transcript-based onboarding first and falls "
          "back to onboarding forms when a transcript is not present."
        ),
        pill: `${{data.summary.ready_cases}} cases ready`,
        kind: "ok"
      }},
      {{
        title: "Deliverable Integrity",
        copy: (
          "Placeholder schema text is stripped before merge, keeping committed "
          "v2 memos and Retell drafts import-ready."
        ),
        pill: "Placeholder guardrails",
        kind: "demo"
      }},
      {{
        title: "Version Hygiene",
        copy: "Superseded v1 emergency unknowns are retired when onboarding confirms a new emergency taxonomy.",
        pill: `${{data.summary.total_unknowns}} unknowns left`,
        kind: data.summary.total_unknowns ? "warn" : "ok"
      }},
    ];

    const metricRoot = document.getElementById("metrics");
    metrics.forEach(([label, value]) => {{
      const el = document.createElement("div");
      el.className = "metric";
      el.innerHTML = `
        <div class=\"metric-label\">${{label}}</div>
        <div class=\"metric-value\">${{value}}</div>
      `;
      metricRoot.appendChild(el);
    }});

    const readinessRoot = document.getElementById("readiness");
    readinessItems.forEach((item) => {{
      const row = document.createElement("div");
      row.className = "legend-row";
      row.innerHTML = `
        <div>
          <div class=\"legend-title\">${{item.title}}</div>
          <div class=\"legend-copy\">${{item.copy}}</div>
        </div>
        <span class=\"pill ${{item.kind}}\">${{item.pill}}</span>
      `;
      readinessRoot.appendChild(row);
    }});

    const spotlightCases = [...data.cases]
      .sort(
        (left, right) =>
          (right.fields_changed + right.conflicts) -
          (left.fields_changed + left.conflicts)
      )
      .slice(0, 3);
    const spotlightRoot = document.getElementById("spotlight");
    spotlightCases.forEach((item) => {{
      const node = document.createElement("div");
      node.className = "spotlight-item";
      node.innerHTML = `
        <h3>${{item.company_name}}</h3>
        <p>
          ${{item.case_id}} moved to ${{item.latest_version.toUpperCase()}} via
          ${{item.source}} with ${{item.fields_changed}} tracked field changes and
          ${{item.conflicts}} flagged conflicts.
        </p>
      `;
      spotlightRoot.appendChild(node);
    }});

    const casesRoot = document.getElementById("cases");
    data.cases.forEach((item) => {{
      const card = document.createElement("article");
      const statusClass = item.latest_version === "v2" ? "ok" : "warn";
      const sourceLabel = item.source === "onboarding_form"
        ? "form"
        : item.source === "onboarding_call"
          ? "call"
          : "demo";
      card.className = "case-card";
      card.innerHTML = `
        <div class=\"case-top\">
          <div>
            <div class=\"case-id\">${{item.case_id}}</div>
            <h2 class=\"case-name\">${{item.company_name}}</h2>
          </div>
          <span class=\"pill ${{statusClass}}\">${{item.status}}</span>
        </div>
        <div class=\"case-meta\">
          <div><dt>Industry</dt><dd>${{item.industry}}</dd></div>
          <div><dt>Source</dt><dd><span class=\"pill demo\">${{sourceLabel}}</span></dd></div>
          <div><dt>Timezone</dt><dd>${{item.timezone}}</dd></div>
          <div><dt>Hours</dt><dd>${{item.hours}}</dd></div>
          <div><dt>Unknowns</dt><dd>${{item.unresolved_unknowns}}</dd></div>
          <div><dt>Field Changes</dt><dd>${{item.fields_changed}}</dd></div>
          <div><dt>Conflicts</dt><dd>${{item.conflicts}}</dd></div>
        </div>
        <div class=\"note\">
          ${{item.after_hours || item.notes || "No additional notes captured."}}
        </div>
      `;
      casesRoot.appendChild(card);
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    payload = _build_payload()
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(_render_html(payload), encoding="utf-8")
    print(f"Dashboard written to {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
