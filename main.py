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
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s


def _extract_score_and_overs(text: str):
    """
    Best-effort score extraction from a line.
    Returns: (score_text, overs_text)
    Examples:
      "SL 145/6 (20 ov)" -> ("SL 145/6", "20 ov")
      "ENG 88-3 (12.4 Ov)" -> ("ENG 88-3", "12.4 ov")
    """
    t = _clean_text(text)

    # overs patterns: (12.3 ov), 12 ov, 12.3 Ov
    overs = ""
    m_overs = re.search(r"\(?\s*(\d{1,2}(?:\.\d)?)\s*(ov|ovs|over|overs)\s*\)?", t, re.IGNORECASE)
    if m_overs:
        overs = f"{m_overs.group(1)} ov"

    # score patterns: 123/4, 123-4, 123 all out
    # try to capture with team short name around it too
    score = ""
    # common: "SL 145/6" or "SL 145-6"
    m_score = re.search(r"\b([A-Z]{2,6}\s*)?(\d{1,3}\s*(?:/|-)\s*\d{1,2})\b", t)
    if m_score:
        team = (m_score.group(1) or "").strip()
        scr = re.sub(r"\s+", "", m_score.group(2))  # remove spaces in 145 / 6
        score = f"{team} {scr}".strip()

    # if still empty, try "123 all out"
    if not score:
        m_allout = re.search(r"\b([A-Z]{2,6}\s*)?(\d{1,3})\s*(all\s*out)\b", t, re.IGNORECASE)
        if m_allout:
            team = (m_allout.group(1) or "").strip()
            score = f"{team} {m_allout.group(2)} all out".strip()

    return score, overs


def _split_live_blob_to_matches(text: str):
    """
    Takes long combined text from Cricbuzz and splits into match entries.
    Returns list of dicts: {match, score, overs, status}
    """
    t = _clean_text(text)

    # remove common UI junk words
    for jw in ["MATCHES", "ALL", "Preview", "PREVIEW"]:
        t = t.replace(jw, " ")

    t = _clean_text(t)

    # split using " - " separators (Cricbuzz uses these a lot)
    parts = [p.strip() for p in t.split(" - ") if p.strip()]

    results = []
    current_match = None

    for p in parts:
        # Detect "A vs B" or "A v B"
        if re.search(r"\b(vs|v)\b", p, re.IGNORECASE):
            current_match = _clean_text(p)
            continue

        if current_match:
            # This chunk is usually: score + overs + situation OR just situation
            chunk = _clean_text(p)

            score, overs = _extract_score_and_overs(chunk)

            # If chunk is only "Innings Break" or "Need 30 to win" etc, keep it as status
            status = chunk

            # If score exists, try to remove it from status to keep it clean
            if score:
                status = status.replace(score, "").strip()
            if overs:
                status = re.sub(r"\(?\s*" + re.escape(overs) + r"\s*\)?", "", status, flags=re.IGNORECASE).strip()

            status = _clean_text(status)

            results.append({
                "match": current_match,
                "score": score,
                "overs": overs,
                "status": status
            })
            current_match = None

    # remove duplicates and tiny noise
    cleaned = []
    seen = set()
    for item in results:
        m = _clean_text(item.get("match", ""))
        sc = _clean_text(item.get("score", ""))
        ov = _clean_text(item.get("overs", ""))
        st = _clean_text(item.get("status", ""))

        if not m:
            continue

        key = (m.lower(), sc.lower(), ov.lower(), st.lower())
        if key in seen:
            continue
        seen.add(key)

        cleaned.append({
            "match": m,
            "score": sc,
            "overs": ov,
            "status": st
        })

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
    # leaving as-is for now (you said we’ll work later)
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
            if 25 <= len(txt) <= 240:
                results.append(txt)

        uniq = []
        seen = set()
        for x in results:
            if x not in seen:
                seen.add(x)
                uniq.append(x)

        if not uniq:
            return jsonify({"error": "No schedule found", "title": title}), 502

        return jsonify(uniq[:80])

    except Exception as e:
        return jsonify({"error": "Internal error in /schedule", "details": str(e)}), 500


@app.route('/live')
def live_matches():
    """
    Returns:
    [
      {"match":"SL vs ENG","score":"SL 145/6","overs":"20 ov","status":"Innings Break"},
      ...
    ]
    """
    try:
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        status, html = _fetch_html(url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        # Grab text blocks that likely contain match + score info
        candidates = soup.find_all(["div", "a"], limit=6000)

        blobs = []
        for el in candidates:
            txt = _clean_text(el.get_text(" ", strip=True))
            if not txt:
                continue

            # keep medium blocks only
            if len(txt) > 420:
                continue

            # require match indicator + some match/score hint
            has_vs = (" vs " in txt.lower()) or (" v " in txt.lower())
            has_hint = any(k in txt.lower() for k in ["overs", "ov", "won by", "need", "target", "innings", "trail", "lead", "/"])
            if has_vs and has_hint:
                blobs.append(txt)

        # de-dup blobs
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
                "error": "No live data found (layout changed)",
                "status": status,
                "title": title
            }), 502

        # Parse blobs into structured matches
        all_items = []
        for blob in uniq_blobs[:10]:
            all_items.extend(_split_live_blob_to_matches(blob))

        # final de-dup
        final = []
        seen2 = set()
        for item in all_items:
            key = (item["match"].lower(), item.get("score","").lower(), item.get("overs","").lower(), item.get("status","").lower())
            if key in seen2:
                continue
            seen2.add(key)
            final.append(item)

        if not final:
            return jsonify({
                "error": "Parsed zero matches (layout changed)",
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
