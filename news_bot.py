
import os
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = Path("news_history.json")
MANUAL_FILE = Path("manual_news.json")

REDDIT_URL = (
    "https://www.reddit.com/r/WarlineOfficial/new.json"
    "?limit=20"
)
APPLE_API = (
    "https://itunes.apple.com/lookup"
    "?id=6742809194&country=us"
)

HEADERS = {
    "User-Agent": (
        "WarlineAllianceNewsBot/1.0 "
        "(public announcement monitor)"
    )
}

# Notification options:
# "none", "everyone", or "role"
NOTIFY = "everyone"
ROLE_ID = "YOUR_ROLE_ID"

# Set to True to post existing news on the first run.
POST_EXISTING_NEWS = False

KEYWORDS = (
    "update", "patch", "event", "maintenance",
    "server", "release", "announcement",
    "new feature", "new content", "version",
    "battle", "operation", "commander",
    "warline", "sniper strike"
)


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def notification(mode):
    if mode == "everyone":
        return "@everyone", {"parse": ["everyone"]}

    if mode == "role" and ROLE_ID.isdigit():
        return (
            f"<@&{ROLE_ID}>",
            {"parse": [], "roles": [ROLE_ID]}
        )

    return "", {"parse": []}


def send_news(item):
    mode = item.get("notify", NOTIFY)
    mention, allowed = notification(mode)

    # Always mention everyone
    mention = "@everyone"
    allowed = {"parse": ["everyone"]}

    category = item.get("category", "Game News")
    title = str(item["title"])[:250]
    description = str(
        item.get("description", "")
    )[:3500]

    embed = {
        "title": f"📰 {title}",
        "description": description or "New announcement!",
        "color": 3447003,
        "fields": [{
            "name": "📌 Category",
            "value": str(category)[:100],
            "inline": True
        }],
        "footer": {
            "text": "⚔️ Warline Alliance • News Bot"
        },
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat()
    }

    url = item.get("url", "")
    if url.startswith("https://"):
        embed["url"] = url

    payload = {
        "username": "Warline News Bot",
        "content": mention,
        "allowed_mentions": allowed,
        "embeds": [embed]
    }

    response = requests.post(
        WEBHOOK,
        json=payload,
        timeout=25
    )
    response.raise_for_status()

    print("Posted:", title)
    time.sleep(1)


def get_reddit_news():
    response = requests.get(
        REDDIT_URL,
        headers=HEADERS,
        timeout=20
    )
    response.raise_for_status()

    posts = response.json()["data"]["children"]
    results = []

    for entry in posts:
        post = entry["data"]

        if post.get("stickied"):
            continue

        title = post.get("title", "")
        body = post.get("selftext", "")
        combined = (title + " " + body).lower()

        if not any(k in combined for k in KEYWORDS):
            continue

        results.append({
            "id": "reddit:" + post["id"],
            "title": title,
            "description": body[:1500] or (
                "A new Warline community post "
                "is available. Open the source "
                "to read the announcement."
            ),
            "category": "Community / Game News",
            "url": (
                "https://www.reddit.com"
                + post["permalink"]
            ),
            "created": post.get("created_utc", 0)
        })

    return results


def get_apple_news():
    response = requests.get(
        APPLE_API,
        headers=HEADERS,
        timeout=20
    )
    response.raise_for_status()

    results = response.json().get("results", [])
    if not results:
        raise ValueError("App Store returned no game")

    app = results[0]
    version = app.get("version", "unknown")
    notes = app.get(
        "releaseNotes",
        "A new game version is available."
    )

    return [{
        "id": "ios:" + version,
        "title": f"Warline iOS Update – v{version}",
        "description": notes[:3000],
        "category": "Game Update",
        "url": app.get(
            "trackViewUrl",
            "https://apps.apple.com/us/app/"
            "warline-sniper-strike/id6742809194"
        ),
        "created": 0
    }]


def get_manual_news():
    data = load_json(
        MANUAL_FILE, {"announcements": []}
    )

    results = []

    for entry in data.get("announcements", []):
        if not entry.get("enabled", True):
            continue

        title = entry.get("title", "").strip()
        if not title:
            continue

        unique_id = entry.get("id")
        if not unique_id:
            unique_id = hashlib.sha256(
                json.dumps(
                    entry, sort_keys=True
                ).encode()
            ).hexdigest()[:16]

        results.append({
            "id": "manual:" + str(unique_id),
            "title": title,
            "description": entry.get("description", ""),
            "category": entry.get(
                "category", "Alliance Announcement"
            ),
            "url": entry.get("url", ""),
            "notify": entry.get("notify", NOTIFY),
            "created": 0
        })

    return results


def main():
    state = load_json(STATE_FILE, {
        "initialized": False,
        "posted": []
    })

    posted = set(state.get("posted", []))
    first_run = not state.get("initialized", False)

    automatic = []
    successful_sources = 0

    for name, function in [
        ("Reddit", get_reddit_news),
        ("App Store", get_apple_news)
    ]:
        try:
            items = function()
            automatic.extend(items)
            successful_sources += 1
            print(name, "items:", len(items))
        except Exception as error:
            print(name, "check failed:", error)

    
    # Only include automatic news from the past 7 days
    seven_days_ago = (
        datetime.now(timezone.utc)
        - timedelta(days=7)
    ).timestamp()

    automatic = [
        item for item in automatic
        if item.get("created", 0) >= seven_days_ago
    ]

    manual = get_manual_news()

    # First run: remember existing automatic news
    # without flooding the Discord channel.
    if first_run:
        if not POST_EXISTING_NEWS:
            for item in automatic:
                posted.add(item["id"])

        # Only initialize when a source was checked.
        if successful_sources:
            state["initialized"] = True

    # Manual posts are always eligible.
    # Older automatic posts come first.
    items = (
        sorted(
            automatic,
            key=lambda x: x.get("created", 0)
        )
        + manual
    )

    for item in items:
        if item["id"] in posted:
            continue

        send_news(item)
        posted.add(item["id"])

        # Save progress after each successful post.
        state["posted"] = sorted(posted)
        save_state(state)

    state["posted"] = sorted(posted)
    save_state(state)
    print("News check completed.")


if __name__ == "__main__":
    main()
