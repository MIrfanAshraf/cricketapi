from flask import Flask, jsonify, render_template, request
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

# Optional googlesearch import
try:
    from googlesearch import search
except Exception:
    search = None

app = Flask(__name__)

CRICBUZZ_BASE = "https://www.cricbuzz.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=25)
    return r.status_code, r.text

def _page_title(soup: BeautifulSoup) -> str:
    return soup.title.get_text(strip=True) if soup.title else ""

def _extract_score_overs_from_text(text: str):
    """
    Best-effort: extract score and overs from a text blob.
    Examples:
      "SL 145/6 (20 ov)" -> score "SL 145/6", overs "20 ov"
    """
    t = _clean(text)

    overs = ""
    m_overs = re.search(r"(\d{1,2}(?:\.\d)?)\s*(ov|ovs|over|overs)\b", t, re.IGNORECASE)
    if m_overs:
        overs = f"{m_overs.group(1)} ov"

    score = ""
    # 145/6 or 145-6
    m_score = re.search(r"\b([A-Z]{2,6}\s*)?(\d{1,3}\s*(?:/|-)\s*\d{1,2})\b", t)
    if m_score:
        team = (m_score.group(1) or "").strip()
        scr = re.sub(r"\s+", "", m_score.group(2))
        score = f"{team} {scr}".strip()

    # 210 all out
    if not score:
        m_allout = re.search(r"\b([A-Z]{2,6}\s*)?(\d{1,3})\s*(all\s*out)\b", t, re.IGNORECASE)
        if m_allout:
            team = (m_allout.group(1) or "").strip()
            score = f"{team} {m_allout.group(2)} all out".strip()

    return score, overs

def _split_live_blob_to_matches(blob: str):
    """
    Your proven working approach: split combined string into matches.
    Returns list of dicts: {match, status, score, overs}
    """
    t = _clean(blob)

    # remove UI junk words
    for jw in ["MATCHES", "ALL", "Preview", "PREVIEW"]:
        t = t.replace(jw, " ")

    t = _clean(t)
    parts = [p.strip() for p in t.split(" - ") if p.strip()]

    results = []
    current_match = None

    for p in parts:
        if re.search(r"\b(vs|v)\b", p, re.IGNORECASE):
            current_match = _clean(p)
            continue

        if current_match:
            chunk = _clean(p)

            # try extract score/overs from same chunk if present
            score, overs = _extract_score_overs_from_text(chunk)

            status = chunk
            if score:
                status = status.replace(score, "").strip()
            if overs:
                status = re.sub(r"\(?\s*" + re.escape(overs) + r"\s*\)?", "", status, flags=re.IGNORECASE).strip()
            status = _clean(status)

            results.append({
                "match": current_match,
                "score": score,
                "overs": overs,
                "status": status
            })
            current_match = None

    # dedup
    final = []
    seen = set()
    for r in results:
        key = (r["match"].lower(), r["score"].lower(), r["overs"].lower(), r["status"].lower())
        if key in seen:
            continue
        seen.add(key)
        final.append(r)

    return final

def _find_live_match_links(html: str):
    """
    Find possible match links from live page.
    Keep it broad to survive Cricbuzz layout changes.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)

    found = []
    for a in links:
        href = a["href"].strip()

        # Common patterns Cricbuzz uses for match pages
        if "/live-cricket-score/" in href or "/cricket-match/" in href or "/live-cricket-scorecard/" in href:
            full = urljoin(CRICBUZZ_BASE, href)
            found.append(full)

    # dedup + limit
    uniq = []
    seen = set()
    for u in found:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)

    return uniq[:8]  # speed limit

def _extract_from_match_page(url: str):
    """
    Best-effort extraction from match page:
    - match title (from h1 if exists, else <title>)
    - score (try to find text containing / and ov)
    - status (need/won/innings break etc)
    """
    s, h = _fetch(url)
    if s != 200:
        return None

    soup = BeautifulSoup(h, "html.parser")

    # match title
    match_title = ""
    h1 = soup.find("h1")
    if h1:
        match_title = _clean(h1.get_text(" ", strip=True))
    if not match_title:
        match_title = _clean(_page_title(soup).replace("Cricbuzz.com", "").strip(" -|"))

    # scan many small blocks
    blocks = soup.find_all(["div", "span", "p"], limit=7000)

    best_score = ""
    best_overs = ""
    best_status = ""

    for el in blocks:
        txt = _clean(el.get_text(" ", strip=True))
        if not txt or len(txt) > 180:
            continue

        # score candidate
        sc, ov = _extract_score_overs_from_text(txt)
        if sc and (ov or "/" in sc or "-" in sc):
            # choose the shortest good score line
            candidate = f"{sc} ({ov})".strip() if ov else sc
            if not best_score or len(candidate) < len(best_score):
                best_score = sc
                best_overs = ov

        # status candidate
        if any(k in txt.lower() for k in ["need", "won by", "innings break", "stumps", "tea", "lunch", "rain", "target", "trail", "lead"]):
            if 8 <= len(txt) <= 120:
                best_status = txt

    return {
        "match": match_title,
        "score": best_score,
        "overs": best_overs,
        "status": best_status,
        "url": url
    }

@app.route("/live")
def live():
    """
    Stable output:
    - Always returns match+status (from live page)
    - Tries to enrich score/overs using match pages (optional)
    Query param:
      /live?detail=1  -> try to enrich (slower)
    """
    try:
        detail = request.args.get("detail", "0").strip() == "1"

        live_url = f"{CRICBUZZ_BASE}/cricket-match/live-scores"
        status, html = _fetch(live_url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz live page", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        # Collect blobs from live page (your working method)
        candidates = soup.find_all(["div", "a"], limit=6000)

        blobs = []
        for el in candidates:
            txt = _clean(el.get_text(" ", strip=True))
            if not txt or len(txt) > 420:
                continue

            has_vs = (" vs " in txt.lower()) or (" v " in txt.lower())
            has_hint = any(k in txt.lower() for k in ["overs", "ov", "won by", "need", "target", "innings", "trail", "lead", "/"])
            if has_vs and has_hint:
                blobs.append(txt)

        # dedup blobs
        uniq_blobs = []
        seen = set()
        for b in blobs:
            k = b.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq_blobs.append(b)

        if not uniq_blobs:
            return jsonify({"error": "No live text blocks found (layout changed)", "title": title}), 502

        # Parse blobs into basic list
        basic_items = []
        for blob in uniq_blobs[:8]:
            basic_items.extend(_split_live_blob_to_matches(blob))

        # final dedup
        basic_final = []
        seen2 = set()
        for it in basic_items:
            key = (it["match"].lower(), it["status"].lower())
            if key in seen2:
                continue
            seen2.add(key)
            basic_final.append(it)

        if not basic_final:
            return jsonify({"error": "Could not parse basic live matches", "title": title}), 502

        # If detail requested: enrich scores using match links
        if detail:
            links = _find_live_match_links(html)
            enriched = []
            for link in links:
                data = _extract_from_match_page(link)
                if data and data.get("match"):
                    enriched.append(data)

            # If enrichment works, return it; otherwise fallback to basic
            if enriched:
                return jsonify(enriched)

        return jsonify(basic_final)

    except Exception as e:
        return jsonify({"error": "Internal error in /live", "details": str(e)}), 500

@app.route("/")
def home():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
