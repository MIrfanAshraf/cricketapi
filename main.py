from flask import Flask, jsonify, render_template
import requests
from bs4 import BeautifulSoup
import re

# Try to import googlesearch, but don't crash the whole server if it isn't available
try:
    from googlesearch import search  # pip install googlesearch-python
except Exception:
    search = None

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}


def _fetch_html(url: str):
    r = requests.get(url, headers=HEADERS, timeout=25)
    return r.status_code, r.text


def _page_title(soup: BeautifulSoup) -> str:
    return soup.title.get_text(strip=True) if soup.title else ""


def _clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_live_blob_to_matches(text: str):
    """
    Takes long combined text from Cricbuzz and tries to split into match entries.
    Returns list of dicts: {match, status, extra}
    """
    t = _clean_text(text)

    # Remove common junk words that appear on Cricbuzz UI
    junk_words = ["MATCHES", "ALL", "Preview", "PREVIEW"]
    for jw in junk_words:
        t = t.replace(jw, " ")

    # Make sure separators are consistent
    t = _clean_text(t)

    # Many entries look like: "TEAM vs TEAM - status ..."
    # Split by occurrences of " - " but we will re-attach properly
    parts = [p.strip() for p in t.split(" - ") if p.strip()]

    results = []
    current_match = None

    for p in parts:
        # Detect match-like token "A vs B" or "A v B"
        # keep it flexible, allow U19, XI, etc.
        if re.search(r"\b(vs|v)\b", p, re.IGNORECASE):
            # If p looks like a match title, store it
            current_match = p
            continue

        # If we already have a match title, this part is status/details
        if current_match:
            status = p
            # Sometimes next part is extra, we will store later if available
            results.append({
                "match": current_match,
                "status": status
            })
            current_match = None
        else:
            # If we have a status without match title, store as unknown
            results.append({
                "match": "",
                "status": p
            })

    # Final cleanup: remove duplicates and super-short garbage
    cleaned = []
    seen = set()
    for item in results:
        m = _clean_text(item.get("match", ""))
        s = _clean_text(item.get("status", ""))

        # remove empty/noise lines
        if len(m) < 3 and len(s) < 10:
            continue

        key = (m.lower(), s.lower())
        if key in seen:
            continue
        seen.add(key)

        cleaned.append({"match": m, "status": s})

    return cleaned


@app.route('/players/<player_name>', methods=['GET'])
def get_player(player_name):
    if search is None:
        return jsonify({"error": "Player search is not available (googlesearch not installed)."}), 501

    query = f"{player_name} cricbuzz"
    profile_link = None

    try:
        results = search(query, num_results=8)
        for link in results:
            if "cricbuzz.com/profiles/" in link:
                profile_link = link
                break
        if not profile_link:
            return jsonify({"error": "No player profile found"}), 404
    except Exception as e:
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

    try:
        status, html = _fetch_html(profile_link)
        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz profile page", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")

        profile = soup.find("div", id="playerProfile")
        if not profile:
            return jsonify({"error": "Cricbuzz layout changed (playerProfile not found)"}), 502

        pc = profile.find("div", class_="cb-col cb-col-100 cb-bg-white") or profile
        name_el = pc.find("h1")
        country_el = pc.find("h3")

        name = name_el.get_text(strip=True) if name_el else player_name
        country = country_el.get_text(strip=True) if country_el else ""

        image_url = None
        img = pc.find("img")
        if img and img.get("src"):
            image_url = img["src"]

        return jsonify({
            "name": name,
            "country": country,
            "image": image_url,
            "profile_url": profile_link
        })

    except Exception as e:
        return jsonify({"error": "Internal error in /players", "details": str(e)}), 500


@app.route('/schedule')
def schedule():
    try:
        url = "https://www.cricbuzz.com/cricket-schedule/upcoming-series/international"
        status, html = _fetch_html(url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        blocks = soup.find_all("div", class_=lambda c: c and ("cb-col-100" in c))
        results = []

        for b in blocks:
            txt = _clean_text(b.get_text(" ", strip=True))
            if not txt:
                continue
            if len(txt) > 240:
                continue
            if len(txt) < 25:
                continue
            results.append(txt)

        uniq = []
        seen = set()
        for x in results:
            if x not in seen:
                seen.add(x)
                uniq.append(x)

        uniq = uniq[:80]

        if not uniq:
            return jsonify({
                "error": "No schedule found (layout changed)",
                "status": status,
                "title": title
            }), 502

        return jsonify(uniq)

    except Exception as e:
        return jsonify({"error": "Internal error in /schedule", "details": str(e)}), 500


@app.route('/live')
def live_matches():
    """
    Returns structured JSON:
    [
      {"match":"SL vs ENG","status":"SL opt to bat"},
      {"match":"ZIMU19 vs PAKU19","status":"Need 36 to win"}
    ]
    """
    try:
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        status, html = _fetch_html(url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        # Grab lots of small text blocks and keep only likely ones
        candidates = soup.find_all(["div", "a"], limit=5000)

        blobs = []
        for el in candidates:
            txt = _clean_text(el.get_text(" ", strip=True))
            if not txt:
                continue
            if len(txt) > 320:
                continue

            has_vs = (" vs " in txt.lower()) or (" v " in txt.lower())
            has_hint = any(k in txt.lower() for k in ["overs", "ov", "won by", "need", "target", "innings", "trail", "lead"])
            if has_vs and has_hint:
                blobs.append(txt)

        # Deduplicate blobs
        uniq_blobs = []
        seen = set()
        for b in blobs:
            k = b.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq_blobs.append(b)

        if not uniq_blobs:
            return jsonify({
                "error": "No matches found (Cricbuzz HTML changed).",
                "status": status,
                "title": title
            }), 502

        # Take the best (first) blob and split it into match objects
        # If there are multiple blobs, we merge them
        all_items = []
        for blob in uniq_blobs[:8]:
            all_items.extend(_split_live_blob_to_matches(blob))

        # Final de-dup again
        final = []
        seen2 = set()
        for item in all_items:
            key = (item["match"].lower(), item["status"].lower())
            if key in seen2:
                continue
            seen2.add(key)
            final.append(item)

        # If still empty, return debug
        if not final:
            return jsonify({
                "error": "Parsed zero structured matches.",
                "status": status,
                "title": title,
                "sample": uniq_blobs[0][:250]
            }), 502

        return jsonify(final[:30])

    except Exception as e:
        return jsonify({"error": "Internal error in /live", "details": str(e)}), 500


@app.route('/')
def website():
    return render_template('index.html')


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
