from flask import Flask, jsonify, Response, render_template
import requests
from bs4 import BeautifulSoup

# Try to import googlesearch, but don't crash the whole server if it isn't available
try:
    from googlesearch import search  # pip install googlesearch-python
except Exception:
    search = None

app = Flask(__name__)

# Browser-like headers (reduces chance of Cricbuzz blocking server requests)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}


@app.route('/players/<player_name>', methods=['GET'])
def get_player(player_name):
    # If googlesearch isn't installed / fails on server, return a clear error instead of crashing
    if search is None:
        return jsonify({"error": "Player search feature is not available (googlesearch not installed on server)."}), 501

    query = f"{player_name} cricbuzz"
    profile_link = None

    try:
        results = search(query, num_results=5)
        for link in results:
            if "cricbuzz.com/profiles/" in link:
                profile_link = link
                break

        if not profile_link:
            return jsonify({"error": "No player profile found"}), 404
    except Exception as e:
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

    try:
        r = requests.get(profile_link, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz player page", "status": r.status_code}), 502

        cric = BeautifulSoup(r.text, "html.parser")

        profile = cric.find("div", id="playerProfile")
        if not profile:
            return jsonify({"error": "Cricbuzz layout changed or profile not found"}), 502

        pc = profile.find("div", class_="cb-col cb-col-100 cb-bg-white")
        if not pc:
            return jsonify({"error": "Cricbuzz layout changed (profile container missing)"}), 502

        # Name, country, image
        name_el = pc.find("h1", class_="cb-font-40")
        country_el = pc.find("h3", class_="cb-font-18 text-gray")
        name = name_el.get_text(strip=True) if name_el else player_name
        country = country_el.get_text(strip=True) if country_el else ""

        image_url = None
        img = pc.find("img")
        if img and img.get("src"):
            image_url = img["src"]

        # Role
        personal = cric.find_all("div", class_="cb-col cb-col-60 cb-lst-itm-sm")
        role = personal[2].get_text(" ", strip=True) if len(personal) > 2 else ""

        # Rankings
        icc = cric.find_all("div", class_="cb-col cb-col-25 cb-plyr-rank text-right")

        def safe_rank(i):
            return icc[i].get_text(strip=True) if len(icc) > i else ""

        tb = safe_rank(0)
        ob = safe_rank(1)
        twb = safe_rank(2)
        tbw = safe_rank(3)
        obw = safe_rank(4)
        twbw = safe_rank(5)

        # Stats tables
        summary = cric.find_all("div", class_="cb-plyr-tbl")
        if len(summary) < 2:
            return jsonify({"error": "Cricbuzz layout changed (stats tables missing)"}), 502

        batting = summary[0]
        bowling = summary[1]

        # Batting stats
        batting_stats = {}
        bat_tbody = batting.find("tbody")
        if bat_tbody:
            for row in bat_tbody.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 13:
                    continue
                fmt = cols[0].get_text(strip=True).lower()
                batting_stats[fmt] = {
                    "matches": cols[1].get_text(strip=True),
                    "runs": cols[3].get_text(strip=True),
                    "highest_score": cols[5].get_text(strip=True),
                    "average": cols[6].get_text(strip=True),
                    "strike_rate": cols[7].get_text(strip=True),
                    "hundreds": cols[12].get_text(strip=True),
                    "fifties": cols[11].get_text(strip=True),
                }

        # Bowling stats
        bowling_stats = {}
        bowl_tbody = bowling.find("tbody")
        if bowl_tbody:
            for row in bowl_tbody.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 12:
                    continue
                fmt = cols[0].get_text(strip=True).lower()
                bowling_stats[fmt] = {
                    "balls": cols[3].get_text(strip=True),
                    "runs": cols[4].get_text(strip=True),
                    "wickets": cols[5].get_text(strip=True),
                    "best_bowling_innings": cols[9].get_text(strip=True),
                    "economy": cols[7].get_text(strip=True),
                    "five_wickets": cols[11].get_text(strip=True),
                }

        player_data = {
            "name": name,
            "country": country,
            "image": image_url,
            "role": role,
            "rankings": {
                "batting": {"test": tb, "odi": ob, "t20": twb},
                "bowling": {"test": tbw, "odi": obw, "t20": twbw},
            },
            "batting_stats": batting_stats,
            "bowling_stats": bowling_stats,
            "profile_url": profile_link,
        }

        return jsonify(player_data)

    except Exception as e:
        return jsonify({"error": "Internal error in /players", "details": str(e)}), 500


@app.route('/schedule')
def schedule():
    """
    Fetch upcoming international series schedule from Cricbuzz.
    This version won't crash; it returns useful debug info if blocked.
    """
    try:
        link = "https://www.cricbuzz.com/cricket-schedule/upcoming-series/international"
        r = requests.get(link, headers=HEADERS, timeout=20)

        if r.status_code != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": r.status_code}), 502

        html = r.text
        page = BeautifulSoup(html, "html.parser")
        title = page.title.get_text(strip=True) if page.title else ""

        # If response is too small, it's usually a block / error page
        if len(html) < 2000:
            return jsonify({
                "error": "Response too small (likely blocked)",
                "status": r.status_code,
                "title": title,
                "sample": html[:300]
            }), 502

        match_containers = page.find_all("div", class_="cb-col-100 cb-col")
        matches = []

        for container in match_containers:
            date = container.find("div", class_="cb-lv-grn-strip text-bold")
            match_info = container.find("div", class_="cb-col-100 cb-col")
            if date and match_info:
                matches.append(
                    f"{date.get_text(' ', strip=True)} - {match_info.get_text(' ', strip=True)}"
                )

        if not matches:
            return jsonify({
                "error": "No schedule found (blocked or layout changed)",
                "status": r.status_code,
                "title": title
            }), 502

        return jsonify(matches)

    except Exception as e:
        return jsonify({"error": "Internal error in /schedule", "details": str(e)}), 500


@app.route('/live')
def live_matches():
    """
    Fetch live matches from Cricbuzz.
    This version is flexible and returns debug info instead of crashing.
    """
    try:
        link = "https://www.cricbuzz.com/cricket-match/live-scores"
        r = requests.get(link, headers=HEADERS, timeout=20)

        if r.status_code != 200:
            return jsonify({"error": "Failed to fetch Cricbuzz", "status": r.status_code}), 502

        html = r.text
        page = BeautifulSoup(html, "html.parser")
        title = page.title.get_text(strip=True) if page.title else ""

        # If response is too small, it's usually a block / error page
        if len(html) < 2000:
            return jsonify({
                "error": "Response too small (likely blocked)",
                "status": r.status_code,
                "title": title,
                "sample": html[:300]
            }), 502

        # Try multiple containers (Cricbuzz changes often)
        candidates = []

        root1 = page.find("div", class_="cb-col cb-col-100 cb-bg-white")
        if root1:
            candidates.append(root1)

        root2 = page.find("div", id="cb-body")
        if root2:
            candidates.append(root2)

        root3 = page.find("body")
        if root3:
            candidates.append(root3)

        live_matches_list = []
        for root in candidates:
            blocks = root.find_all("div", class_=lambda c: c and "cb-lv-scrs-col" in c)
            for b in blocks:
                txt = b.get_text(" ", strip=True)
                if txt and txt not in live_matches_list:
                    live_matches_list.append(txt)

        if not live_matches_list:
            return jsonify({
                "error": "No matches found (blocked or layout changed)",
                "status": r.status_code,
                "title": title
            }), 502

        return jsonify(live_matches_list)

    except Exception as e:
        return jsonify({"error": "Internal error in /live", "details": str(e)}), 500


@app.route('/')
def website():
    return render_template('index.html')


if __name__ == "__main__":
    # Local dev only; Railway uses gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
