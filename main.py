"""
Apple Podcasts Scraper — podcast discovery + full episode feeds.

Dependency-free (stdlib + Apify REST). No login, no API key, no browser.

Data paths:
  discovery: Apple iTunes Search/Lookup API (public):
             https://itunes.apple.com/search?term=&entity=podcast&country=
             https://itunes.apple.com/lookup?id=<collectionId>&entity=podcast
  episodes:  the podcast's own public RSS feed (feedUrl) — the standard,
             openly-syndicated episode format — parsed directly.
  fallback:  iTunes lookup entity=podcastEpisode when no feed is available.

Normalized, de-duplicated, with provenance on every row.
"""
from __future__ import annotations
import json, os, re, ssl, uuid, time, math
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

SCHEMA_VERSION = "1.0.0"
API = "https://api.apify.com/v2"
ITUNES = "https://itunes.apple.com"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Apple's iTunes API returns an EMPTY result set when a browser-style UA is sent;
# with a plain/minimal UA it responds normally. Use minimal headers for it.
UA_API = {"Accept": "application/json"}
_CTX = ssl.create_default_context()
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}


def log(*a):
    print(*a, flush=True)


def _u(path):
    return f"{API}{path}{'&' if '?' in path else '?'}" + urlencode({"token": os.environ.get("APIFY_TOKEN", "")})


def _text(url, timeout=45):
    with urlrequest.urlopen(urlrequest.Request(url, headers=UA), timeout=timeout, context=_CTX) as r:
        return r.read()


def _json(url, timeout=45):
    with urlrequest.urlopen(urlrequest.Request(url, headers=UA_API), timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def get_input():
    store = os.environ.get("ACTOR_DEFAULT_KEY_VALUE_STORE_ID")
    if store and os.environ.get("APIFY_TOKEN"):
        try:
            return json.loads(_text(_u(f"/key-value-stores/{store}/records/{os.environ.get('ACTOR_INPUT_KEY','INPUT')}")).decode("utf-8", "ignore"))
        except Exception as e:
            log(f"input fetch failed: {e}")
    if os.path.exists("input.json"):
        return json.load(open("input.json"))
    return {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sanitize(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


def push_items(items):
    items = [_sanitize(r) for r in items]
    token = os.environ.get("APIFY_TOKEN"); ds = os.environ.get("ACTOR_DEFAULT_DATASET_ID")
    if not (token and ds):
        with open("dataset_local.jsonl", "a") as f:
            for it in items:
                f.write(json.dumps(it) + "\n")
        return len(items)
    n = 0
    for i in range(0, len(items), 300):
        chunk = items[i:i + 300]
        req = urlrequest.Request(_u(f"/datasets/{ds}/items"), data=json.dumps(chunk).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
        urlrequest.urlopen(req, timeout=120, context=_CTX).read()
        n += len(chunk)
    return n


def charge(event_name, count):
    token = os.environ.get("APIFY_TOKEN"); rid = os.environ.get("ACTOR_RUN_ID")
    if not (token and rid) or count <= 0:
        return
    body = json.dumps({"eventName": event_name, "count": int(count)}).encode()
    req = urlrequest.Request(_u(f"/actor-runs/{rid}/charge"), data=body, method="POST",
                             headers={"Content-Type": "application/json", "idempotency-key": uuid.uuid4().hex})
    try:
        urlrequest.urlopen(req, timeout=30, context=_CTX).read()
    except HTTPError as e:
        try: log(f"charge {event_name} failed: {e.code} {e.read().decode()[:160]}")
        except Exception: log(f"charge {event_name} failed: {e.code}")
    except Exception as e:
        log(f"charge {event_name} failed: {e}")


# ---------------------------------------------------------------- helpers ----

def _strip_html(s):
    if not s:
        return None
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    import html as _h
    s = _h.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None


def _dur_seconds(v):
    if not v:
        return None
    v = str(v).strip()
    try:
        if ":" in v:
            parts = [int(p) for p in v.split(":") if p.strip() != ""]
            sec = 0
            for p in parts:
                sec = sec * 60 + p
            return sec
        return int(float(v))
    except Exception:
        return None


def _iso_from_rfc822(v):
    if not v:
        return None
    try:
        return parsedate_to_datetime(v).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------- records ----

def podcast_from_itunes(r, source_input):
    genres = r.get("genres") or []
    return {
        "record_type": "podcast",
        "collection_id": r.get("collectionId"),
        "name": r.get("collectionName"),
        "artist": r.get("artistName"),
        "feed_url": r.get("feedUrl"),
        "artwork_url": (r.get("artworkUrl600") or r.get("artworkUrl100")),
        "genres": genres or None,
        "primary_genre": r.get("primaryGenreName"),
        "country": r.get("country"),
        "language": r.get("language") or (r.get("languageCodesISO2A") or [None])[0],
        "track_count": r.get("trackCount"),
        "release_date": r.get("releaseDate"),
        "itunes_url": r.get("collectionViewUrl"),
        "content_advisory_rating": r.get("contentAdvisoryRating"),
        "is_explicit": (r.get("collectionExplicitness") == "explicit"),
        "source_type": "search",
        "source_input": source_input,
        "schema_version": SCHEMA_VERSION,
        "fetched_at": _now(),
    }


def podcast_from_feed(ch, source_input):
    def t(tag, ns=None):
        el = ch.find(tag, ns) if ns else ch.find(tag)
        return el.text.strip() if (el is not None and el.text) else None

    title = t("title")
    desc = _strip_html(t("content:encoded", NS) or t("description"))
    img = None
    ie = ch.find("itunes:image", NS)
    if ie is not None:
        img = ie.get("href")
    if not img:
        i2 = ch.find("image/url")
        if i2 is not None:
            img = i2.text
    cats = [c.get("text") for c in ch.findall("itunes:category", NS) if c.get("text")]
    owner = ch.find("itunes:owner", NS)
    owner_name = owner_email = None
    if owner is not None:
        n = owner.find("itunes:name", NS); e = owner.find("itunes:email", NS)
        owner_name = n.text if (n is not None) else None
        owner_email = e.text if (e is not None) else None
    return {
        "record_type": "podcast",
        "collection_id": None,
        "name": title,
        "artist": t("itunes:author", NS),
        "feed_url": None,
        "artwork_url": img,
        "genres": cats or None,
        "primary_genre": cats[0] if cats else None,
        "country": None,
        "language": t("language"),
        "track_count": None,
        "release_date": None,
        "itunes_url": None,
        "content_advisory_rating": None,
        "is_explicit": (t("itunes:explicit", NS) or "").lower() in ("yes", "true", "explicit"),
        "description": desc,
        "author": t("itunes:author", NS),
        "owner_name": owner_name,
        "owner_email": owner_email,
        "copyright": t("copyright"),
        "link": t("link"),
        "last_build_date": _iso_from_rfc822(t("lastBuildDate")),
        "source_type": "feed",
        "source_input": source_input,
        "schema_version": SCHEMA_VERSION,
        "fetched_at": _now(),
    }


def parse_feed(raw, podcast_meta, feed_url, source_input, max_eps):
    """Return (podcast_record_or_None, [episode_records])."""
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        log(f"feed parse error {feed_url}: {e}")
        return None, []
    ch = root.find("channel")
    if ch is None:
        return None, []
    prec = podcast_from_feed(ch, source_input)
    prec["feed_url"] = feed_url
    prec["name"] = prec["name"] or (podcast_meta or {}).get("name")
    prec["collection_id"] = (podcast_meta or {}).get("collection_id")
    cid = prec["collection_id"]
    pname = prec["name"] or (podcast_meta or {}).get("name")
    eps = []
    for it in ch.findall("item"):
        def t(tag, ns=None):
            el = it.find(tag, ns) if ns else it.find(tag)
            return el.text.strip() if (el is not None and el.text) else None

        enc = it.find("enclosure")
        audio_url = enc.get("url") if enc is not None else None
        audio_len = None
        if enc is not None and (enc.get("length") or "").isdigit():
            audio_len = int(enc.get("length"))
        img = None
        iie = it.find("itunes:image", NS)
        if iie is not None:
            img = iie.get("href")
        desc_html = t("content:encoded", NS) or t("description")
        pub = t("pubDate")
        season = t("itunes:season", NS)
        epno = t("itunes:episode", NS)
        eps.append({
            "record_type": "episode",
            "podcast_collection_id": cid,
            "podcast_name": pname,
            "feed_url": feed_url,
            "episode_guid": t("guid"),
            "title": t("title"),
            "description": _strip_html(desc_html),
            "description_html": desc_html,
            "pub_date": _iso_from_rfc822(pub),
            "pub_date_raw": pub,
            "duration_seconds": _dur_seconds(t("itunes:duration", NS)),
            "audio_url": audio_url,
            "audio_length": audio_len,
            "audio_type": enc.get("type") if enc is not None else None,
            "episode_link": t("link"),
            "image_url": img,
            "season": int(season) if (season or "").isdigit() else None,
            "episode_number": int(epno) if (epno or "").isdigit() else None,
            "episode_type": t("itunes:episodeType", NS),
            "is_explicit": (t("itunes:explicit", NS) or "").lower() in ("yes", "true", "explicit"),
            "source_type": "feed",
            "source_input": source_input,
            "schema_version": SCHEMA_VERSION,
            "fetched_at": _now(),
        })
        if len(eps) >= max_eps:
            break
    return prec, eps


def episodes_from_itunes(collection_id, source_input, max_eps):
    d = _json(f"{ITUNES}/lookup?" + urlencode({"id": collection_id, "entity": "podcastEpisode", "limit": min(200, max_eps)}))
    out = []
    for r in d.get("results", []):
        if r.get("kind") != "podcast-episode":
            continue
        out.append({
            "record_type": "episode",
            "podcast_collection_id": collection_id,
            "podcast_name": r.get("collectionName"),
            "feed_url": None,
            "episode_guid": str(r.get("trackId")) if r.get("trackId") is not None else None,
            "title": r.get("trackName"),
            "description": r.get("description"),
            "description_html": None,
            "pub_date": r.get("releaseDate"),
            "pub_date_raw": None,
            "duration_seconds": int(r["trackTimeMillis"] / 1000) if r.get("trackTimeMillis") else None,
            "audio_url": r.get("episodeUrl"),
            "audio_length": None,
            "audio_type": None,
            "episode_link": r.get("trackViewUrl"),
            "image_url": r.get("artworkUrl600") or r.get("artworkUrl160"),
            "season": r.get("seasonNumber"),
            "episode_number": r.get("episodeNumber"),
            "episode_type": None,
            "is_explicit": (r.get("trackExplicitness") == "explicit"),
            "source_type": "itunes",
            "source_input": source_input,
            "schema_version": SCHEMA_VERSION,
            "fetched_at": _now(),
        })
        if len(out) >= max_eps:
            break
    return out


def norm_feed_url(v):
    v = str(v).strip()
    return v if v.startswith("http") else None


def norm_id(v):
    v = str(v).strip()
    if not v:
        return None
    m = re.search(r"id(\d+)", v)
    if m:
        return int(m.group(1))
    return int(v) if v.isdigit() else None


# ------------------------------------------------------------------ main -----

def main():
    inp = get_input() or {}
    country = (inp.get("country") or "US").upper()
    include_eps = bool(inp.get("includeEpisodes"))
    max_eps = int(inp.get("maxEpisodesPerPodcast") or 100)
    search_limit = int(inp.get("maxPodcastsPerSearch") or 50)
    max_items = int(inp.get("maxItems") or 100000)

    queries = inp.get("searchQueries") or []
    if isinstance(queries, str):
        queries = [queries]
    ids = inp.get("podcastIds") or []
    if isinstance(ids, str):
        ids = [ids]
    feeds = inp.get("feedUrls") or []
    if isinstance(feeds, str):
        feeds = [feeds]

    if not queries and not ids and not feeds:
        queries = ["technology"]

    charge("actor-start", 1)
    rows = []; errors = []; seen_pod = set(); seen_ep = set()

    def emit_podcast(p):
        cid = p.get("collection_id")
        feed = p.get("feed_url")
        keys = []
        if cid:
            keys.append(f"cid:{cid}")
        if feed:
            keys.append(f"feed:{feed}")
        if not keys and p.get("name"):
            keys.append(f"name:{p['name'].strip().lower()}")
        if any(k in seen_pod for k in keys):
            # merge: keep the first row but fill in a missing feed_url / collection_id
            if keys and rows:
                for r in rows:
                    if r.get("record_type") == "podcast":
                        if cid and r.get("collection_id") == cid:
                            r["feed_url"] = r.get("feed_url") or feed
                            return False
                        if feed and r.get("feed_url") == feed:
                            r["collection_id"] = r.get("collection_id") or cid
                            return False
            return False
        seen_pod.update(keys)
        rows.append(p)
        return True

    # 1) discovery via iTunes search
    podcast_targets = []  # (meta dict with collection_id/name/feed_url)
    for q in queries:
        q = str(q).strip()
        if not q:
            continue
        try:
            d = _json(f"{ITUNES}/search?" + urlencode({"term": q, "entity": "podcast", "limit": search_limit, "country": country}))
            for r in d.get("results", []):
                meta = {"collection_id": r.get("collectionId"), "name": r.get("collectionName"), "feed_url": r.get("feedUrl")}
                podcast_targets.append(meta)
                emit_podcast(podcast_from_itunes(r, q))
        except (HTTPError, URLError, ValueError) as e:
            errors.append(f"search '{q}': {str(e)[:60]}")
        time.sleep(0.5)

    # 2) explicit ids
    for v in ids:
        cid = norm_id(v)
        if cid:
            podcast_targets.append({"collection_id": cid, "name": None, "feed_url": None})

    # 3) explicit feeds
    for v in feeds:
        f = norm_feed_url(v)
        if f:
            podcast_targets.append({"collection_id": None, "name": None, "feed_url": f})

    # resolve iTunes metadata for bare ids (name + feed_url)
    resolved = {}
    for meta in podcast_targets:
        cid = meta.get("collection_id")
        if cid and not meta.get("feed_url"):
            d = _json(f"{ITUNES}/lookup?" + urlencode({"id": cid, "entity": "podcast"}))
            for r in d.get("results", []):
                if r.get("kind") == "podcast":
                    meta["feed_url"] = r.get("feedUrl")
                    meta["name"] = meta.get("name") or r.get("collectionName")
                    emit_podcast(podcast_from_itunes(r, str(cid)))
                    break
            time.sleep(0.4)

    # 4) episodes
    if include_eps:
        for meta in podcast_targets:
            feed = meta.get("feed_url")
            got = []
            if feed:
                try:
                    raw = _text(feed)
                    p, got = parse_feed(raw, meta, feed, feed, max_eps)
                    if p:
                        emit_podcast(p)
                except (HTTPError, URLError, ValueError) as e:
                    errors.append(f"feed {str(feed)[:40]}: {str(e)[:50]}")
            if not got and meta.get("collection_id"):
                try:
                    got = episodes_from_itunes(meta["collection_id"], str(meta["collection_id"]), max_eps)
                except (HTTPError, URLError, ValueError) as e:
                    errors.append(f"itunes eps {meta.get('collection_id')}: {str(e)[:50]}")
            for e in got:
                k = e.get("episode_guid") or (e.get("podcast_collection_id"), e.get("title"))
                if k in seen_ep:
                    continue
                seen_ep.add(k)
                rows.append(e)
            time.sleep(0.4)

    rows = rows[:max_items]
    n_pod = sum(1 for r in rows if r["record_type"] == "podcast")
    n_ep = len(rows) - n_pod
    pushed = push_items(rows)
    charge("podcast", n_pod)
    charge("episode", n_ep)
    log(f"FINISHED podcasts={n_pod} episodes={n_ep} pushed={pushed} errors={len(errors)}")
    if errors:
        log("Errors: " + "; ".join(errors[:10]))


if __name__ == "__main__":
    main()
