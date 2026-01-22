from flask import Flask, jsonify, render_template
import requests
from bs4 import BeautifulSoup

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

        # Role
        personal = soup.find_all("div", class_="cb-col cb-col-60 cb-lst-itm-sm")
        role = personal[2].get_text(" ", strip=True) if len(personal) > 2 else ""

        # Rankings (safe)
        icc = soup.find_all("div", class_="cb-col cb-col-25 cb-plyr-rank text-right")

        def safe_rank(i):
            return icc[i].get_text(strip=True) if len(icc) > i else ""

        player_data = {
            "name": name,
            "country": country,
            "image": image_url,
            "role": role,
            "rankings": {
                "batting": {"test": safe_rank(0), "odi": safe_rank(1), "t20": safe_rank(2)},
                "bowling": {"test": safe_rank(3), "odi": safe_rank(4), "t20": safe_rank(5)},
            },
            "profile_url": profile_link,
        }

        return jsonify(player_data)

    except Exception as e:
        return jsonify({"error": "Internal error in /players", "details": str(e)}), 500


@app.route('/schedule')
def schedule():
    """
    Upcoming international series schedule.
    Returns either a list OR a clear debug error (never crashes).
    """
    try:
        url = "https://www.cricbuzz.com/cricket-schedule/upcoming-series/international"
        status, html = _fetch_html(url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        # A bit of structure: schedule text blocks usually live in these areas
        blocks = soup.find_all("div", class_=lambda c: c and ("cb-col-100" in c))
        results = []

        # Extract lines that look like schedules (simple but effective)
        for b in blocks:
            txt = b.get_text(" ", strip=True)
            if not txt:
                continue
            # filter obvious junk
            if "Cricbuzz" in txt and len(txt) < 30:
                continue
            # Keep medium-length lines (schedule entries are often like this)
            if 25 <= len(txt) <= 220:
                results.append(txt)

        # remove duplicates
        uniq = []
        seen = set()
        for x in results:
            if x not in seen:
                seen.add(x)
                uniq.append(x)

        # Keep only first 80 items (safe)
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
    Live matches.
    Cricbuzz changes CSS often, so we extract match cards by multiple strategies.
    """
    try:
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        status, html = _fetch_html(url)

        if status != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": status}), 502

        soup = BeautifulSoup(html, "html.parser")
        title = _page_title(soup)

        # Strategy 1: Try common match-card containers (Cricbuzz changes frequently)
        # We look for divs that contain "vs" and "Overs" or "won by"/"need" etc.
        candidates = soup.find_all(["div", "a"], limit=5000)

        matches = []
        for el in candidates:
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue

            # Very simple pattern checks for cricket score cards
            has_vs = (" vs " in txt.lower()) or (" v " in txt.lower())
            has_score_hint = any(k in txt.lower() for k in ["overs", "ov", "run", "target", "won by", "need", "trail", "lead", "innings"])
            # ignore massive blocks
            if len(txt) > 320:
                continue

            if has_vs and has_score_hint:
                matches.append(txt)

        # Clean duplicates & keep quality
        cleaned = []
        seen = set()
        for m in matches:
            # skip navigation text
            if "live scores" in m.lower() and len(m) < 40:
                continue
            if m not in seen:
                seen.add(m)
                cleaned.append(m)

        # Strategy 2: If still empty, try links that look like match URLs
        if not cleaned:
            links = soup.find_all("a", href=True)
            for a in links:
                href = a["href"]
                if "/cricket-match/" in href or "/live-cricket-score/" in href:
                    txt = a.get_text(" ", strip=True)
                    if txt and len(txt) < 220:
                        cleaned.append(txt)

            # de-dup again
            uniq = []
            seen2 = set()
            for x in cleaned:
                if x not in seen2:
                    seen2.add(x)
                    uniq.append(x)
            cleaned = uniq

        # Final result
        if not cleaned:
            return jsonify({
                "error": "No matches found (Cricbuzz HTML changed).",
                "status": status,
                "title": title
            }), 502

        # limit output
        return jsonify(cleaned[:50])

    except Exception as e:
        return jsonify({"error": "Internal error in /live", "details": str(e)}), 500


@app.route('/')
def website():
    return render_template('index.html')


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
