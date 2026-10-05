"""answer-engine-visibility: does a brand get named/cited when buyers ask AI?

Runs real buyer-intent prompts through live answer engines (Claude, Gemini,
Tavily cited search), detects whether the target brand is named/cited vs its
competitors, adds a technical crawlability score, and renders a one-page HTML
report.

Standard-library HTTP only. Reads API keys from C:\\Users\\rowle\\.env-keys.

Run:  python probe.py
Out:  <brand>_report_<YYYY-MM-DD>.html  (beside this file)

The scoring half (crawlability) is vendored verbatim from
ai-visibility-mcp/src/ai_visibility_mcp/server.py (stdlib only), minus the
FastMCP decorator so this file is standalone.
"""
import json, re, urllib.request, urllib.error, html, datetime, pathlib
from urllib.parse import urljoin, urlparse

KEYS_PATH = r"C:\Users\rowle\.env-keys"

# ----------------------------------------------------------------------------
# CONFIGS: one entry per brand. Pick at runtime:  python probe.py <slug>
# (defaults to "eve"). Adding a brand = add an entry here -> the public repo
# version will read this from a JSON/CLI instead.
# ----------------------------------------------------------------------------
CONFIGS = {
    "eve": {
        "brand": "Eve",
        "domain": "eve.legal",
        # word-boundary match for the brand as a legal product, avoids the
        # common English word "eve"
        "brand_regex": r"\beve\s+legal\b|eve\.legal|\beve\b(?=[^.]{0,60}(legal|law|plaintiff|litigation|attorney|intake|case))",
        "category": "AI tools for plaintiff personal injury law firms",
        "competitors": ["EvenUp", "Supio", "Harvey", "CoCounsel", "Casetext",
                        "Filevine", "Litify", "Clio", "Spellbook", "Parley",
                        "Briefpoint", "Darrow"],
        "prompts": [
            "What are the best AI tools for plaintiff personal injury law firms?",
            "What software automates case intake and evaluation for a personal injury law firm?",
            "Best AI legal assistant for drafting demand letters and medical summaries?",
            "What is the best AI case management platform for plaintiff attorneys?",
            "How can a personal injury firm use AI to increase attorney capacity?",
            "What AI platforms review cases for mass tort eligibility?",
            "Best legal AI for pre-litigation workflow automation?",
            "What is an AI workforce platform for law firms?",
        ],
    },
    "thrive": {
        "brand": "Thrive Smart Systems",
        "domain": "thrivesmartsystems.com",
        # "thrive" alone is far too common (Thrive Market, ThriveCart, etc.);
        # require the full brand, a product name, or thrive near an irrigation word
        "brand_regex": r"\bthrive\s+smart\b|thrivesmartsystems|\bthrive\s+evo\b|\bthrive\s+airlink\b|\bthrive\b(?=[^.]{0,50}(irrigation|wireless|valve|sprinkler|controller))",
        "category": "wireless irrigation systems that retrofit existing valves without trenching",
        "competitors": ["Rain Bird", "Hunter", "Hydrawise", "Toro", "Rachio",
                        "Weathermatic", "Netafim", "Baseline", "Calsense",
                        "HydroPoint", "Orbit", "K-Rain", "Irritrol"],
        "prompts": [
            "What is the best wireless irrigation system to retrofit existing valves without trenching?",
            "How can I make an existing sprinkler system wireless without running new control wire?",
            "Best wireless irrigation controller for commercial landscape sites?",
            "What companies make wireless valve control for irrigation?",
            "Best smart irrigation systems that work over long range without WiFi?",
            "Best way to retrofit an old irrigation system with smart wireless control?",
            "Wireless irrigation solutions for golf courses and large properties?",
            "What irrigation technology eliminates control wire between the controller and valves?",
        ],
    },
    "airops": {
        "brand": "AirOps",
        "domain": "airops.com",
        # "airops" is distinctive; match the word or the domain.
        "brand_regex": r"\bair\s?ops\b|airops\.com",
        "category": "AEO and AI search visibility tools that track and improve brand citations across ChatGPT, Perplexity, and Gemini",
        # Competitors verified appearing in live AI answers for this category
        # (Perplexity UI + Google AI Overview, 2026-10-05) plus AirOps's own
        # /compare page (Profound, Scrunch, Semrush, Ahrefs, Conductor).
        "competitors": ["Profound", "Scrunch", "Otterly", "Peec", "Semrush",
                        "Ahrefs", "SE Ranking", "Conductor", "Writesonic",
                        "Goodie", "Daydream", "Rankscale", "Nightwatch"],
        "prompts": [
            "What are the best AEO tools to track brand visibility in AI search?",
            "What are the best AI search visibility trackers in 2026?",
            "How do I track my brand's mentions and citations in ChatGPT and Perplexity?",
            "What tools monitor share of voice across AI answer engines?",
            "Best platform to improve my brand's citations in AI Overviews and ChatGPT?",
            "What are the best Profound alternatives for AI visibility tracking?",
            "What software helps create content that gets cited by AI search engines?",
            "Best enterprise generative engine optimization (GEO) platform?",
        ],
    },
}

import sys
_SLUG = sys.argv[1] if len(sys.argv) > 1 else "eve"
if _SLUG not in CONFIGS:
    raise SystemExit("Unknown brand slug '" + _SLUG + "'. Options: " + ", ".join(CONFIGS))
CONFIG = CONFIGS[_SLUG]


def load_keys(path=KEYS_PATH):
    """Environment variables win; a local .env-keys file (KEY=value lines) is
    the fallback so the tool runs on any machine with exported keys."""
    import os
    keys = {}
    if pathlib.Path(path).exists():
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY"):
        if os.environ.get(k):
            keys[k] = os.environ[k]
    return keys


# ----------------------------------------------------------------------------
# Engines. Each returns (answer_text, cited_strings) where cited_strings is the
# list of URLs/titles the engine actually retrieved or cited. ALL THREE engines
# are GROUNDED (live web retrieval), so they measure what a buyer actually sees
# when they ask an AI assistant that searches the web - NOT base-model recall.
# (An earlier version called Claude/Gemini without search and under-reported
# every niche brand; see the 2026-10-05 reconciliation. Keep them grounded.)
# ----------------------------------------------------------------------------
def _post_json(url, body_obj, headers):
    body = json.dumps(body_obj).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def ask_claude(prompt, key, model="claude-sonnet-4-6"):
    # Claude WITH the server-side web_search tool = a grounded answer like a
    # buyer using Claude with search on. Loop on pause_turn (server tool cap).
    messages = [{"role": "user", "content": prompt}]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    text_parts, cited = [], []
    for _ in range(4):
        data = _post_json("https://api.anthropic.com/v1/messages", {
            "model": model, "max_tokens": 2000,
            "tools": [{"type": "web_search_20260209", "name": "web_search"}],
            "messages": messages,
        }, headers)
        for block in data.get("content", []):
            bt = block.get("type")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "web_search_tool_result":
                for res in (block.get("content") or []):
                    if isinstance(res, dict):
                        if res.get("url"):
                            cited.append(res["url"])
                        if res.get("title"):
                            cited.append(res["title"])
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": data.get("content", [])})
            continue
        break
    return " ".join(text_parts), cited


def ask_gemini(prompt, key, model="gemini-2.5-flash"):
    # Gemini WITH Google Search grounding.
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           + model + ":generateContent?key=" + key)
    data = _post_json(url, {"contents": [{"parts": [{"text": prompt}]}],
                            "tools": [{"google_search": {}}]},
                      {"content-type": "application/json"})
    text_parts, cited = [], []
    for c in data.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            if p.get("text"):
                text_parts.append(p["text"])
        gm = c.get("groundingMetadata", {}) or {}
        for chunk in gm.get("groundingChunks", []) or []:
            w = chunk.get("web", {}) or {}
            if w.get("uri"):
                cited.append(w["uri"])
            if w.get("title"):
                cited.append(w["title"])
    return " ".join(text_parts), cited


def ask_tavily(query, key):  # Tavily = AI search; synthesized answer + cited source URLs
    data = _post_json("https://api.tavily.com/search",
                      {"api_key": key, "query": query, "max_results": 8,
                       "include_answer": True},
                      {"content-type": "application/json"})
    return (data.get("answer", "") or ""), [res.get("url", "") for res in data.get("results", [])]


# ----------------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------------
BRAND_PAT = re.compile(CONFIG["brand_regex"], re.I)


def brand_hit(text, cited):
    if text and BRAND_PAT.search(text):
        return True
    dom = CONFIG["domain"].lower()
    for s in (cited or []):
        s = s or ""
        if dom in s.lower() or BRAND_PAT.search(s):
            return True
    return False


def comps_in(text, cited):
    found = {c for c in CONFIG["competitors"]
             if re.search(r"\b" + re.escape(c) + r"\b", text or "", re.I)}
    for s in (cited or []):
        s = s or ""
        for c in CONFIG["competitors"]:
            # match competitor name in a title, or its spaceless form in a URL/domain
            if re.search(r"\b" + re.escape(c) + r"\b", s, re.I) or \
               c.lower().replace(" ", "") in s.lower():
                found.add(c)
    return sorted(found)


# ----------------------------------------------------------------------------
# Vendored crawlability scorer (from ai-visibility-mcp server.py, stdlib only,
# FastMCP decorator removed; local var `html` renamed to avoid shadowing the
# html module used for escaping).
# ----------------------------------------------------------------------------
AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "anthropic-ai", "PerplexityBot", "Google-Extended", "CCBot",
               "Applebot-Extended"]
_UA = "answer-engine-visibility/0.1 (+https://github.com/draltaway/answer-engine-visibility)"
_TIMEOUT = 10
_MAX_BYTES = 1_000_000


def _normalize(url):
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read(_MAX_BYTES).decode("utf-8", "replace")
            return getattr(resp, "status", 200), body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return None, ""


def _parse_robots(body):
    groups, current = {}, []
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = (p.strip() for p in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            current = groups.setdefault(value, [])
        elif field == "disallow" and current is not None:
            current.append(value)
    return groups


def _crawler_allowed(groups, agent):
    for name in (agent, "*"):
        for token, disallows in groups.items():
            if token.lower() == name.lower() and any(d.strip() == "/" for d in disallows):
                return False
    return True


def check_ai_visibility(url):
    site = _normalize(url)
    checks = {}
    _, robots_body = _get(urljoin(site, "/robots.txt"))
    groups = _parse_robots(robots_body) if robots_body else {}
    crawler_access = {c: _crawler_allowed(groups, c) for c in AI_CRAWLERS}
    blocked = [c for c, ok in crawler_access.items() if not ok]
    checks["ai_crawlers_blocked"] = blocked
    checks["ai_crawlers_allowed"] = not blocked
    status, _ = _get(urljoin(site, "/llms.txt"))
    checks["llms_txt"] = status == 200
    status, _ = _get(urljoin(site, "/sitemap.xml"))
    checks["sitemap"] = status == 200
    status, page = _get(site)
    checks["homepage_reachable"] = status == 200
    lowered = page.lower()
    checks["structured_data"] = 'application/ld+json' in lowered
    checks["title"] = bool(re.search(r"<title[^>]*>\s*\S", page, re.I))
    checks["meta_description"] = bool(
        re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']\s*\S', page, re.I))
    checks["h1"] = bool(re.search(r"<h1[^>]*>\s*\S", page, re.I))
    weights = {"ai_crawlers_allowed": 30, "llms_txt": 20, "structured_data": 20,
               "sitemap": 10, "title": 10, "meta_description": 10}
    score = sum(pts for key, pts in weights.items() if checks.get(key))
    if score >= 80:
        verdict = "Strong: AI engines can find, read, and cite this site."
    elif score >= 50:
        verdict = "Partial: readable, but leaving discoverability on the table."
    else:
        verdict = "Weak: AI engines will struggle to find or cite this site."
    return {"url": site, "score": score, "summary": verdict, "checks": checks}


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def render_report(grid, presence, tech, comp_tally, engine_names):
    brand = CONFIG["brand"]
    today = datetime.date.today().isoformat()
    esc = html.escape

    # presence color
    pcol = "#a11" if presence < 34 else ("#8a6d00" if presence < 67 else "#08662a")

    # grid rows
    rows = []
    for r in grid:
        tds = []
        for cell in r["cells"]:
            mark = "YES" if cell["hit"] else "no"
            cls = "yes" if cell["hit"] else "no"
            tds.append('<td class="' + cls + '">' + mark + '</td>')
        rows.append('<tr><td class="q">' + esc(r["prompt"]) + '</td>' + ''.join(tds) + '</tr>')
    head = '<tr><th class="q">Buyer question</th>' + ''.join('<th>' + esc(e) + '</th>' for e in engine_names) + '</tr>'

    # competitor bar
    comp_sorted = sorted(comp_tally.items(), key=lambda kv: -kv[1])
    maxc = comp_sorted[0][1] if comp_sorted else 1
    comp_rows = []
    for name, n in comp_sorted:
        w = int(100 * n / maxc)
        comp_rows.append('<div class="crow"><span class="cname">' + esc(name) + '</span>'
                         '<span class="cbar" style="width:' + str(max(w, 6)) + '%"></span>'
                         '<span class="cn">' + str(n) + '</span></div>')
    comp_html = ''.join(comp_rows) if comp_rows else '<div class="muted">No competitors detected in the answers.</div>'

    # fixable gaps from technical checks
    c = tech["checks"]
    gaps = []
    if c.get("ai_crawlers_blocked"):
        gaps.append("Your robots.txt blocks AI crawlers (" + ", ".join(c["ai_crawlers_blocked"])
                    + "), so these engines cannot read the site at all.")
    if not c.get("llms_txt"):
        gaps.append("No /llms.txt: there is no plain-text map telling AI engines what " + brand + " is and does.")
    if not c.get("structured_data"):
        gaps.append("No JSON-LD structured data on the homepage: answer engines have no machine-readable facts to lift and cite.")
    if not c.get("sitemap"):
        gaps.append("No /sitemap.xml: crawlers may miss pages.")
    # When the site is technically healthy, the actionable content is WHICH
    # buyer questions the brand loses and who takes the citation there - not a
    # generic line. ALL engines here are grounded (they search the live web),
    # so a miss is a genuine citation gap, not a base-model-recall artifact.
    misses = []  # (prompt, [engines missed], [comps seen on those cells])
    for r in grid:
        missed_cells = [cell for cell in r["cells"] if not cell["hit"]]
        if missed_cells:
            mcomps = sorted({c for cell in missed_cells for c in cell["comps"]})
            misses.append((r["prompt"], [cell["engine"] for cell in missed_cells], mcomps))

    if tech["score"] >= 80:
        base = ["Your site is technically clean (crawlability " + str(tech["score"])
                + "/100), so nothing below is a website problem. These are the buyer "
                "questions where " + brand + " loses the citation:"]
        if presence == 0:
            base = ["Your site is technically clean (crawlability " + str(tech["score"])
                    + "/100), so this is NOT a crawling problem.",
                    "Every engine here searches the live web, and " + brand + " surfaced in "
                    "ZERO answers. For these buyer questions the answer engines pull "
                    "competitors, not " + brand + ".",
                    "The gap is third-party authority: earning citations in the round-ups, "
                    "review sites, and comparison pages the engines lift from, not changing "
                    "the website itself."]
        elif not misses:
            base = ["Your site is technically clean (crawlability " + str(tech["score"])
                    + "/100) AND " + brand + " was named in every answer probed. Keep the "
                    "citations that produced this; re-probe after major competitor moves."]
        else:
            for prompt, engs, mcomps in misses[:6]:
                line = '"' + prompt + '" - missed on ' + ", ".join(engs)
                line += ("; citation went to " + ", ".join(mcomps) if mcomps
                         else "; no competitor named either (unclaimed question)")
                base.append(line)
            base.append("Each line above is a specific, winnable citation: get " + brand
                        + " into the sources those engines pull for that question.")
        gaps = base
    else:
        gaps = gaps[:3] or ["Technical signals look healthy; the gap is answer-engine presence, not crawlability."]
    gaps_html = ''.join('<li>' + esc(g) + '</li>' for g in gaps)

    tcol = "#a11" if tech["score"] < 50 else ("#8a6d00" if tech["score"] < 80 else "#08662a")

    css = ("body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:#1a1a1a;max-width:860px;"
           "margin:0 auto;padding:28px;background:#f6f7f9;}h1{font-size:23px;color:#14213d;margin:0 0 2px;}"
           ".sub{color:#555;font-size:13px;margin-bottom:20px;}"
           ".scores{display:flex;gap:16px;margin-bottom:22px;}"
           ".scard{flex:1;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:16px 18px;}"
           ".big{font-size:40px;font-weight:800;line-height:1;}"
           ".slbl{font-size:12px;color:#555;margin-top:6px;}"
           "h2{font-size:15px;color:#14213d;margin:22px 0 8px;}"
           "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e5ea;border-radius:10px;overflow:hidden;font-size:13px;}"
           "th,td{padding:8px 10px;text-align:center;border-bottom:1px solid #eef0f3;}"
           "th{background:#14213d;color:#fff;font-weight:600;}td.q,th.q{text-align:left;}"
           "td.yes{background:#d9f5df;color:#08662a;font-weight:700;}td.no{background:#fdecec;color:#a11;}"
           ".crow{display:flex;align-items:center;gap:10px;margin:5px 0;}"
           ".cname{width:110px;font-size:13px;}.cbar{height:14px;background:#0a66c2;border-radius:3px;}"
           ".cn{font-size:12px;color:#555;}.muted{color:#777;font-size:13px;}"
           ".gaps{background:#fff8e6;border:1px solid #f0e2b8;border-radius:10px;padding:12px 16px 12px 30px;color:#6b5600;font-size:13px;}"
           ".note{font-size:12px;color:#888;margin-top:20px;}")

    doc = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>'
           + esc(brand) + ' Answer-Engine Visibility ' + today + '</title><style>' + css
           + '</style></head><body>'
           '<h1>Answer-Engine Visibility: ' + esc(brand) + '</h1>'
           '<div class="sub">' + today + ' &middot; When buyers ask AI assistants about "'
           + esc(CONFIG["category"]) + '", does ' + esc(brand) + ' get named? '
           'Probed live across ' + esc(", ".join(engine_names)) + '.</div>'
           '<div class="scores">'
           '<div class="scard"><div class="big" style="color:' + pcol + '">' + str(presence) + '%</div>'
           '<div class="slbl">ANSWER-ENGINE PRESENCE: share of buyer questions where ' + esc(brand)
           + ' was named or cited</div></div>'
           '<div class="scard"><div class="big" style="color:' + tcol + '">' + str(tech["score"]) + '</div>'
           '<div class="slbl">TECHNICAL CRAWLABILITY (0-100): ' + esc(tech["summary"]) + '</div></div>'
           '</div>'
           '<h2>The grid: who got named, question by question</h2>'
           '<table>' + head + ''.join(rows) + '</table>'
           '<h2>Who got cited instead</h2>' + comp_html +
           '<h2>' + ("Where the citations leak" if tech["score"] >= 80 else "Fixable gaps")
           + '</h2><ul class="gaps">' + gaps_html + '</ul>'
           '<div class="note">Built by Dallin Rowley. Every figure above is from a live probe run on '
           + today + '; nothing is estimated. The method and tooling are reusable: point it at any brand + category.</div>'
           '</body></html>')
    doc = (doc.replace(chr(8212), "-").replace(chr(8211), "-")
              .replace(chr(8217), "'").replace(chr(8220), '"').replace(chr(8221), '"'))

    out = pathlib.Path(__file__).resolve().parent / (CONFIG["domain"].split(".")[0] + "_report_" + today + ".html")
    out.write_text(doc, encoding="utf-8")
    print("wrote", out, "| em-dashes:", doc.count(chr(8212)))
    return out


def run():
    keys = load_keys()
    engines = []
    if keys.get("ANTHROPIC_API_KEY"):
        engines.append(("Claude (web search)", lambda p: ask_claude(p, keys["ANTHROPIC_API_KEY"])))
    if keys.get("GEMINI_API_KEY"):
        engines.append(("Gemini (grounded)", lambda p: ask_gemini(p, keys["GEMINI_API_KEY"])))
    if keys.get("TAVILY_API_KEY"):
        engines.append(("Tavily (cited search)", lambda p: ask_tavily(p, keys["TAVILY_API_KEY"])))
    if not engines:
        raise SystemExit("No usable API keys found in " + KEYS_PATH)

    grid, hits, cells, comp_tally = [], 0, 0, {}
    for p in CONFIG["prompts"]:
        row = {"prompt": p, "cells": []}
        for ename, fn in engines:
            try:
                text, cited = fn(p)
                hit = brand_hit(text, cited)
                comps = comps_in(text, cited)
            except Exception as e:
                text, cited, hit, comps = ("ERROR: " + str(e)), [], False, []
            for cc in comps:
                comp_tally[cc] = comp_tally.get(cc, 0) + 1
            cells += 1
            hits += 1 if hit else 0
            row["cells"].append({"engine": ename, "hit": hit, "comps": comps,
                                 "excerpt": (text or "")[:2000],
                                 "cited": (cited or [])[:30]})
            print(("  HIT " if hit else "  --- ") + ename + " | " + p[:48]
                  + (" | comps: " + ",".join(comps) if comps else ""))
        grid.append(row)

    presence = round(100 * hits / cells) if cells else 0
    print("PRESENCE:", presence, "% (", hits, "/", cells, ")")
    tech = check_ai_visibility(CONFIG["domain"])
    print("TECHNICAL:", tech["score"], tech["summary"])

    # audit dump: full text + cited strings per cell, plus a loose brand-match
    # flag so false negatives in the strict regex are visible on review
    audit = []
    for r in grid:
        for cell in r["cells"]:
            hay = (cell.get("excerpt") or "") + " " + " ".join(cell.get("cited") or [])
            loose = bool(BRAND_PAT.search(hay)) or (CONFIG["domain"].lower() in hay.lower())
            audit.append({"prompt": r["prompt"], "engine": cell["engine"],
                          "hit": cell["hit"], "loose_brand_match": loose,
                          "comps": cell["comps"], "excerpt": cell.get("excerpt", ""),
                          "cited": cell.get("cited", [])})
    ap = pathlib.Path(__file__).resolve().parent / (CONFIG["domain"].split(".")[0] + "_raw_audit.json")
    ap.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print("audit ->", ap)

    render_report(grid, presence, tech, comp_tally, [e[0] for e in engines])


if __name__ == "__main__":
    run()
