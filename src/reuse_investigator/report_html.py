from __future__ import annotations

import html
import json
from typing import Any


_STATUS_LABELS = {
    "confirmed": "已核实唯一出处",
    "ambiguous": "证据不足 / 多候选",
    "rejected": "检索命中但核实被拒",
    "unexplained": "未能解释",
}

_STATUS_CLASS = {
    "confirmed": "ok",
    "ambiguous": "warn",
    "rejected": "bad",
    "unexplained": "mute",
}


def render_html_report(report: dict[str, Any]) -> str:
    """Render a single self-contained interactive HTML report.

    Frame evidence PPMs are served by the bundled viewer and referenced by
    relative artifact paths. Every finding links back to the exact probe frame
    and source frame numbers, and to the side-by-side original-frame artifact.
    """
    investigation_id = html.escape(str(report["investigation_id"]))
    probe = report["probe"]
    findings = report.get("findings", [])
    unexplained = report.get("unexplained", [])
    conditions = report.get("analysis_conditions", {})
    counts = report.get("status_counts", {})

    finding_cards = "\n".join(
        _finding_card(report["investigation_id"], finding)
        for finding in findings
    )
    unexplained_cards = "\n".join(
        _unexplained_card(interval) for interval in unexplained
    )
    source_options = "".join(
        f"<li><code>{html.escape(source['clip_id'])}</code> "
        f"({source['frame_count']} 帧, digest "
        f"<code>{html.escape(source['digest'][:12])}…</code>)</li>"
        for source in report.get("sources", [])
    )
    count_chips = "".join(
        f'<span class="chip {_STATUS_CLASS.get(status, "mute")}">'
        f"{_STATUS_LABELS.get(status, status)}: {counts[status]}</span>"
        for status in sorted(counts)
    )
    disposition_summary = _disposition_summary(
        report.get("frame_outcomes", [])
    )

    data_json = html.escape(json.dumps(report, ensure_ascii=False))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>画面复用调查 · {investigation_id}</title>
<style>
:root {{
  --bg:#14181d; --panel:#1d232b; --ink:#e7ecf2; --muted:#9aa7b4;
  --line:#2c3540; --ok:#3fb96b; --warn:#e0a93b; --bad:#e05d5d; --accent:#5aa0ff;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg);
  color:var(--ink); line-height:1.55; }}
header {{ padding:22px 28px; border-bottom:1px solid var(--line); background:var(--panel); }}
h1 {{ margin:0 0 6px; font-size:21px; }}
h2 {{ font-size:16px; margin:24px 0 10px; }}
main {{ padding:22px 28px 60px; max-width:1180px; }}
.meta {{ color:var(--muted); font-size:13px; }}
.chip {{ display:inline-block; padding:3px 10px; border-radius:999px;
  font-size:12px; margin:4px 6px 4px 0; border:1px solid var(--line); }}
.chip.ok {{ color:var(--ok); border-color:var(--ok); }}
.chip.warn {{ color:var(--warn); border-color:var(--warn); }}
.chip.bad {{ color:var(--bad); border-color:var(--bad); }}
.chip.mute {{ color:var(--muted); }}
.filters {{ margin:14px 0; }}
.filters button {{ background:var(--panel); color:var(--ink); border:1px solid var(--line);
  border-radius:6px; padding:6px 12px; margin-right:8px; cursor:pointer; }}
.filters button.active {{ border-color:var(--accent); color:var(--accent); }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; margin:14px 0; }}
.card.ok {{ border-left:4px solid var(--ok); }}
.card.warn {{ border-left:4px solid var(--warn); }}
.card.bad {{ border-left:4px solid var(--bad); }}
.card.mute {{ border-left:4px solid var(--muted); }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; }}
.kv {{ font-size:13px; color:var(--muted); }}
.kv b {{ color:var(--ink); }}
.frame-link {{ color:var(--accent); text-decoration:none; font-family:monospace; }}
.frame-link:hover {{ text-decoration:underline; }}
.evidence {{ margin-top:10px; }}
.evidence img {{ max-width:100%; border:1px solid var(--line); border-radius:8px;
  image-rendering:pixelated; background:#000; }}
details {{ margin-top:8px; }}
summary {{ cursor:pointer; color:var(--accent); font-size:13px; }}
table {{ border-collapse:collapse; width:100%; font-size:12.5px; margin-top:8px; }}
th,td {{ border:1px solid var(--line); padding:5px 8px; text-align:left; }}
th {{ color:var(--muted); font-weight:600; }}
code {{ color:#cfe0ff; }}
.hidden {{ display:none; }}
.note {{ font-size:12.5px; color:var(--muted); background:#171c22;
  border:1px dashed var(--line); border-radius:8px; padding:10px 12px; }}
</style>
</head>
<body>
<header>
  <h1>画面复用调查 · <code>{investigation_id}</code></h1>
  <div class="meta">
    待查片 <code>{html.escape(probe["clip_id"])}</code> ·
    {probe["width"]}×{probe["height"]} · {probe["frame_count"]} 帧 ·
    digest <code>{html.escape(probe["digest"][:16])}…</code>
  </div>
  <div style="margin-top:8px">{count_chips}</div>
</header>
<main>
  <section>
    <h2>候选原片库</h2>
    <ul class="meta">{source_options}</ul>
    <div class="note">
      检索命中（二值指纹汉明距离）只作为<strong>候选</strong>，不等于核实来源。
      确认结论必须同时通过归一化互相关、色彩不变量、连续时序链与唯一性余量；
      静止/低纹理画面或多原片近似时保留为候选，无法解释的范围单独列出。
    </div>
    <h2>分析条件</h2>
    <pre class="meta">{html.escape(json.dumps(conditions, ensure_ascii=False, indent=2))}</pre>
    <h2>逐帧处置统计</h2>
    {disposition_summary}
  </section>

  <section>
    <h2>片段对应结论</h2>
    <div class="filters" id="filters">
      <button data-filter="all" class="active">全部</button>
      <button data-filter="confirmed">已核实</button>
      <button data-filter="ambiguous">证据不足</button>
      <button data-filter="rejected">被拒命中</button>
      <button data-filter="unexplained">未能解释</button>
    </div>
    <div id="findings">{finding_cards}</div>
    <h2>未能解释的范围</h2>
    <div id="unexplained">{unexplained_cards or '<p class="meta">无</p>'}</div>
  </section>
</main>
<script type="application/json" id="report-data">{data_json}</script>
<script>
const buttons = document.querySelectorAll('#filters button');
buttons.forEach(btn => btn.addEventListener('click', () => {{
  buttons.forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const filter = btn.dataset.filter;
  document.querySelectorAll('.card[data-status]').forEach(card => {{
    const show = filter === 'all' || card.dataset.status === filter;
    card.classList.toggle('hidden', !show);
  }});
}}));
</script>
</body>
</html>
"""


def _disposition_summary(frame_outcomes: list[dict[str, Any]]) -> str:
    labels = {
        "verified": "通过核实",
        "rejected_candidate": "检索命中但核实被拒",
        "no_candidate": "无检索候选",
        "repeated_held_frame": "静止/重复帧（不可唯一定位）",
    }
    counts: dict[str, int] = {}
    for item in frame_outcomes:
        disposition = item.get("disposition", "verified")
        counts[disposition] = counts.get(disposition, 0) + 1
    rows = "".join(
        f"<tr><td>{html.escape(labels.get(key, key))}</td><td>{counts[key]}</td></tr>"
        for key in sorted(counts)
    )
    return (
        "<table><tr><th>逐帧处置</th><th>帧数</th></tr>"
        f"{rows}</table>"
    )


def _finding_card(investigation_id: str, finding: dict[str, Any]) -> str:
    status = finding["status"]
    css = _STATUS_CLASS.get(status, "mute")
    label = _STATUS_LABELS.get(status, status)
    probe_range = finding["probe_range"]
    source_range = finding["source_range"]
    evidence = finding.get("evidence") or {}
    artifact = evidence.get("artifact", "")
    alternatives = finding.get("alternatives", [])

    alt_rows = ""
    if alternatives:
        alt_rows = "".join(
            "<tr><td><code>{cid}</code></td><td>{ncc}</td><td>{frames}</td></tr>".format(
                cid=html.escape(str(alt["source_clip_id"])),
                ncc=alt["mean_ncc"],
                frames=alt["matching_frames"],
            )
            for alt in alternatives
        )
    alt_block = (
        "<details><summary>保留的其他候选（未达唯一判定）</summary>"
        "<table><tr><th>候选原片</th><th>平均 NCC</th><th>命中帧数</th></tr>"
        f"{alt_rows}</table></details>"
        if alternatives
        else '<div class="kv">无竞争候选</div>'
    )

    evidence_block = ""
    if artifact:
        evidence_url = f"/v1/investigations/{html.escape(investigation_id)}/artifact?name={html.escape(artifact.split('/')[-1])}"
        evidence_block = f"""
        <div class="evidence">
          <a class="frame-link" href="{evidence_url}" target="_blank">
            打开双方真实原帧对照 (PPM) ↗
          </a><br>
          <img src="{evidence_url}" alt="frame pair evidence" loading="lazy">
        </div>"""

    return f"""
    <article class="card {css}" data-status="{html.escape(status)}">
      <div class="grid">
        <div>
          <strong>{html.escape(label)}</strong>
          <div class="kv" style="margin-top:6px">
            待查片帧区间 <b>[{probe_range[0]}, {probe_range[1]})</b><br>
            原片 <code>{html.escape(finding['source_clip_id'])}</code>
            帧区间 <b>[{source_range[0]}, {source_range[1]})</b><br>
            时序方向 <b>{html.escape(finding['direction'])}</b><br>
            平均 NCC <b>{finding['mean_ncc']}</b> ·
            色彩距离 <b>{finding['mean_color_distance']}</b> ·
            核实帧数 <b>{finding['verified_frames']}</b> ·
            低纹理帧 <b>{finding['flat_frames']}</b>
          </div>
          <div class="kv" style="margin-top:8px">
            回到原帧：待查片 <b>#{probe_range[0]}…{probe_range[1] - 1}</b>
            ↔ 原片 <code>{html.escape(finding['source_clip_id'])}</code>
            <b>#{source_range[0]}…{max(source_range[0], source_range[1] - 1)}</b>
          </div>
        </div>
        <div>
          {alt_block}
          {evidence_block}
        </div>
      </div>
    </article>
    """


def _unexplained_card(interval: dict[str, Any]) -> str:
    probe_range = interval["probe_range"]
    return f"""
    <article class="card mute" data-status="unexplained">
      <strong>{_STATUS_LABELS['unexplained']}</strong>
      <div class="kv" style="margin-top:6px">
        待查片帧区间 <b>[{probe_range[0]}, {probe_range[1]})</b><br>
        原因：{html.escape(interval['reason'])}
      </div>
    </article>
    """
