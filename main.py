from flask import Flask, jsonify
import lxml
import requests
from bs4 import BeautifulSoup
import re
import time
from flask import Response
import json
from googlesearch import search #pip install googlesearch-python
from flask import render_template

app = Flask(__name__)


@app.route('/players/<player_name>', methods=['GET'])
def get_player(player_name):
    query = f"{player_name} cricbuzz"
    profile_link = None
    try:
        results = search(query, num_results=5)
        for link in results:
            if "cricbuzz.com/profiles/" in link:
                profile_link = link
                print(f"Found profile: {profile_link}")
                break
                
        if not profile_link:
            return {"error": "No player profile found"}
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}
    
    # Get player profile page
    c = requests.get(profile_link).text
    cric = BeautifulSoup(c, "lxml")
    profile = cric.find("div", id="playerProfile")
    pc = profile.find("div", class_="cb-col cb-col-100 cb-bg-white")
    
    # Name, country and image
    name = pc.find("h1", class_="cb-font-40").text
    country = pc.find("h3", class_="cb-font-18 text-gray").text
    image_url = None
    images = pc.findAll('img')
    for image in images:
        image_url = image['src']
        break  # Just get the first image

    # Personal information and rankings
    personal = cric.find_all("div", class_="cb-col cb-col-60 cb-lst-itm-sm")
    role = personal[2].text.strip()
    
    icc = cric.find_all("div", class_="cb-col cb-col-25 cb-plyr-rank text-right")
    # Batting rankings
    tb = icc[0].text.strip()   # Test batting
    ob = icc[1].text.strip()   # ODI batting
    twb = icc[2].text.strip()  # T20 batting
    
    # Bowling rankings
    tbw = icc[3].text.strip()  # Test bowling
    obw = icc[4].text.strip()  # ODI bowling
    twbw = icc[5].text.strip() # T20 bowling

    # Summary of the stats
    summary = cric.find_all("div", class_="cb-plyr-tbl")
    batting = summary[0]
    bowling = summary[1]

    # Batting statistics
    bat_rows = batting.find("tbody").find_all("tr")
    batting_stats = {}
    for row in bat_rows:
        cols = row.find_all("td")
        format_name = cols[0].text.strip().lower()  # e.g., "Test", "ODI", "T20"
        batting_stats[format_name] = {
            "matches": cols[1].text.strip(),
            "runs": cols[3].text.strip(),
            "highest_score": cols[5].text.strip(),
            "average": cols[6].text.strip(),
            "strike_rate": cols[7].text.strip(),
            "hundreds": cols[12].text.strip(),
            "fifties": cols[11].text.strip(),
        }

    # Bowling statistics
    bowl_rows = bowling.find("tbody").find_all("tr")
    bowling_stats = {}
    for row in bowl_rows:
        cols = row.find_all("td")
        format_name = cols[0].text.strip().lower()  # e.g., "Test", "ODI", "T20"
        bowling_stats[format_name] = {
            "balls": cols[3].text.strip(),
            "runs": cols[4].text.strip(),
            "wickets": cols[5].text.strip(),
            "best_bowling_innings": cols[9].text.strip(),
            "economy": cols[7].text.strip(),
            "five_wickets": cols[11].text.strip(),
        }

    # Create player stats dictionary
    player_data = {
        "name": name,
        "country": country,
        "image": image_url,
        "role": role,
        "rankings": {
            "batting": {
                "test": tb,
                "odi": ob,
                "t20": twb
            },
            "bowling": {
                "test": tbw,
                "odi": obw,
                "t20": twbw
            }
        },
        "batting_stats": batting_stats,
        "bowling_stats": bowling_stats
    }

    return jsonify(player_data)


@app.route('/schedule')
def schedule():
    link = f"https://www.cricbuzz.com/cricket-schedule/upcoming-series/international"
    source = requests.get(link).text
    page = BeautifulSoup(source, "lxml")

    # Find all match containers
    match_containers = page.find_all("div", class_="cb-col-100 cb-col")

    matches = []

    # Iterate through each match container
    for container in match_containers:
        # Extract match details
        date = container.find("div", class_="cb-lv-grn-strip text-bold")
        match_info = container.find("div", class_="cb-col-100 cb-col")
        
        if date and match_info:
            match_date = date.text.strip()
            match_details = match_info.text.strip()
            matches.append(f"{match_date} - {match_details}")
    
    return jsonify(matches)


@app.route('/live')
def live_matches():
    link = "https://www.cricbuzz.com/cricket-match/live-scores"
    source = requests.get(link).text
    page = BeautifulSoup(source, "lxml")

    # Container that holds all matches
    page = page.find("div", class_="cb-col cb-col-100 cb-bg-white")
    matches = page.find_all("a", class_="cb-lv-scrs-well")  # anchor has match link + score

    live_matches = []

    for m in matches:
        # extract matchId from href
        href = m.get("href", "")
        match_id = None
        if href.startswith("/live-cricket-scores/"):
            match_id = href.split("/")[2]  # second part is ID

        # get team names
        teams = m.find_all("div", class_="cb-ovr-flo cb-hmscg-tm-nm")

        if len(teams) == 2:
            team1 = teams[0].get_text(strip=True)
            team2 = teams[1].get_text(strip=True)
            summary = f"{team1} vs {team2}"
            live_matches.append({
                "matchId": match_id,
                "liveMatchSummary": summary
            })

    return jsonify(live_matches)


@app.route('/match/<match_id>')
def match_details(match_id):
    link = f"https://www.cricbuzz.com/live-cricket-scores/{match_id}"
    source = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}).text
    page = BeautifulSoup(source, "lxml")

    result = {}

    # ---- Teams ----
    batting_team = page.find("h2", class_="cb-font-20 text-bold inline-block")
    # ---- Match title ----
    title_tag = page.find("h1", class_="cb-nav-hdr")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)
    

    # ---- Current score ----
    if batting_team:
        result["currentScore"] = batting_team.get_text(strip=True)

    # ---- Current run rate ----
    crr_tag = page.find("span", string=lambda t: t and "CRR:" in t)
    if crr_tag and crr_tag.find_next("span"):
        result["currentRunRate"] = crr_tag.find_next("span").get_text(strip=True)

    # ---- Match status ----
    status_tag = page.find("div", class_="cb-text-stumps")
    if status_tag:
        result["status"] = status_tag.get_text(strip=True)

    # ---- Batters ----
    batters = []
    inf_blocks = page.select("div.cb-min-inf")
    if len(inf_blocks) > 0:
        batter_rows = inf_blocks[0].select("div.cb-min-itm-rw")[:2]
        for row in batter_rows:
            cols = row.find_all("div")
            if len(cols) >= 6:
                batters.append({
                    "name": cols[0].get_text(" ", strip=True),
                    "runs": cols[1].get_text(strip=True),
                    "balls": cols[2].get_text(strip=True),
                    "fours": cols[3].get_text(strip=True),
                    "sixes": cols[4].get_text(strip=True),
                    "strikeRate": cols[5].get_text(strip=True)
                })
    result["batters"] = batters

    # ---- Bowlers ----
    bowlers = []
    if len(inf_blocks) > 1:
        bowler_rows = inf_blocks[1].select("div.cb-min-itm-rw")[:2]
        for row in bowler_rows:
            cols = row.find_all("div")
            if len(cols) >= 6:
                bowlers.append({
                    "name": cols[0].get_text(" ", strip=True),
                    "overs": cols[1].get_text(strip=True),
                    "maidens": cols[2].get_text(strip=True),
                    "runs": cols[3].get_text(strip=True),
                    "wickets": cols[4].get_text(strip=True),
                    "economy": cols[5].get_text(strip=True)
                })
    result["bowlers"] = bowlers

    # ---- Key Stats ----
    key_stats = {}
    key_block = page.select_one("div.cb-col.cb-col-33.cb-key-st-lst")
    if key_block:
        for row in key_block.select("div.cb-min-itm-rw"):
            label = row.find("span", class_="text-bold")
            value = row.find_all("span")[-1]
            if label and value:
                text = label.get_text(strip=True)
                if text.startswith("Partnership"):
                    key_stats["partnership"] = value.get_text(strip=True)
                elif text.startswith("Last Wkt"):
                    key_stats["lastWicket"] = value.get_text(strip=True)
                elif text.startswith("Ovs Left"):
                    key_stats["oversLeft"] = value.get_text(strip=True)
                elif text.startswith("Last 10 overs"):
                    key_stats["last10Overs"] = value.get_text(strip=True)
                elif text.startswith("Toss"):
                    key_stats["toss"] = value.get_text(strip=True)
    result["keyStats"] = key_stats

    # ---- Recent balls ----
    recent_tag = page.find("div", class_="cb-min-rcnt")
    if recent_tag:
        result["recent"] = recent_tag.get_text(strip=True).replace("Recent:", "").strip()

    return jsonify(result)

@app.route('/')
def website():
    return render_template('index.html')

if __name__ =="__main__":
    app.run(debug=True)






