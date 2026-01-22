from flask import Flask, jsonify, render_template
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

try:
    from googlesearch import search
except Exception:
    search = None

app = Flask(__name__)

BASE = "https://www.cricbuzz.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

def _fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=25)
    return r.status_code, r.text

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _title(soup: BeautifulSoup) -> str:
    return soup.title.get_text(strip=True) if soup.title else ""

def _extract_score_from_match_page(soup: BeautifulSoup):
    """
    Cricbuzz match page usually has score blocks; we extract best-effort:
    - score lines containing / or "all out"
    - status line like "Innings Break" or "Need 26 to win"
    """
    text_blocks = soup.find_all(["div", "span", "p"], limit=6000)

    score_lines = []
    status_line = ""

    for el in text_blocks:
        t = _clean(el.get_text(" ", strip=True))
        if not t:
            continue
        if len(t) > 200:
            continue

        # score hints
        if re.search(r"\b\d{1,3}\s*(/|-)\s*\d{1,2}\b", t) or re.search(r"\ball\s*out\b", t, re.IGNORECASE):
            # avoid junk like ads
            if any(x in t.lower() for x in ["cookie", "privacy", "subscribe", "sign in"]):
                continue
            score_lines.append(t)

        # status hints
        if any(x in t.lower() for x in ["need", "won by", "innings break", "stumps", "tea", "lunch", "rain", "target", "trail", "lead"]):
            if len(t) >= 8 and len(t) <= 120:
                status_line = t

    # dedup & choose best score line
    uniq_scores = []
    seen = set()
    for s in score_lines:
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq_scores.append(s)

    # pick the shortest useful one (usually clean scoreboard line)
    uniq_scores.sort(key=len)
    best_score = uniq_scores[0] if uniq_scores else ""

    return best_score, status_line

def _find_live_match_links(live_html: str):
    soup = BeautifulSoup(live_html, "html.parser")

    links = soup.find_all("a", href=True)
    match_links = []

    for a in links:
        href = a["href"]
        # Live match links commonly include these patterns
        if "/live-cricket-score/" in href or "/cricket-match/" in href:
            full = urljoin(BASE, href)
            match_links.append(full)

    # deduplicate and limit
    uniq = []
    seen = set()
    for l in match_links:
        if l in seen:
            continue
        seen.add(l)
        uniq.append(l)

    return uniq[:10]  # limit for speed

@app.route("/live")
def live():
    try:
        live_url = "https://www.cricbuzz.com/cricket-match/live-scores"
        status, html = _fetch(live_url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz live page", "status": status}), 502

        # Find match links
        match_links = _find_live_match_links(html)
        if not match_links:
            soup = BeautifulSoup(html, "html.parser")
            return jsonify({
                "error": "No match links found (layout changed)",
                "status": status,
                "title": _title(soup)
            }), 502

        results = []

        for link in match_links:
            try:
                s2, h2 = _fetch(link)
                if s2 != 200:
                    continue

                soup2 = BeautifulSoup(h2, "html.parser")
                page_title = _title(soup2)

                # Match name guess from title
                match_name = page_title.replace("Cricbuzz.com", "").strip(" -|")

                score, status_line = _extract_score_from_match_page(soup2)

                # Only include if it looks like a real match
                if "vs" in match_name.lower() or " v " in match_name.lower():
                    results.append({
                        "match": match_name,
                        "score": score,
                        "overs": "",   # score often already includes overs; keeping field for future
                        "status": status_line,
                        "url": link
                    })
            except Exception:
                continue

        if not results:
            return jsonify({"error": "Could not parse live matches right now"}), 502

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": "Internal error in /live", "details": str(e)}), 500

@app.route("/")
def home():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
