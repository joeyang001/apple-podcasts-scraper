# Apple Podcasts Scraper — Shows, Episodes, Audio & Metadata

Scrape **Apple Podcasts**: discover shows by keyword, then pull their full **episode feeds** — titles, descriptions, publish dates, durations, audio URLs, season/episode numbers, artwork — without logging in and without an API key.

Episodes come from each podcast's own **public RSS feed** (with an iTunes API fallback), so the data is rich and up to date. Every row is normalized, de-duplicated and carries provenance.

## What you get

- **Podcast discovery** — search by keyword and get shows with artwork, genres, country, episode counts and the feed URL.
- **Full episode feeds** — every episode with title, description (plain + HTML), publish date, duration in seconds, direct audio URL + byte length + MIME type, episode link, image, season and episode number.
- **Direct feeds too** — pass RSS feed URLs straight in if you already know them.

Rows are tagged by `record_type` (`podcast` or `episode`) in one dataset.

## Output fields

**Podcasts (`record_type: "podcast"`)**
| Field | Description |
|---|---|
| `collection_id` / `name` / `artist` | Identity |
| `feed_url` / `itunes_url` | Sources |
| `artwork_url` | Cover art |
| `genres` / `primary_genre` | Categories |
| `country` / `language` | Localization |
| `track_count` / `release_date` | Size / launch |
| `content_advisory_rating` / `is_explicit` | Rating |
| `description` / `author` / `owner_name` / `owner_email` / `copyright` / `link` / `last_build_date` | Feed metadata |

**Episodes (`record_type: "episode"`)**
| Field | Description |
|---|---|
| `podcast_collection_id` / `podcast_name` / `feed_url` | Which show |
| `episode_guid` / `title` | Identity |
| `description` / `description_html` | Show notes |
| `pub_date` / `pub_date_raw` | ISO-8601 UTC + original |
| `duration_seconds` | Parsed to seconds |
| `audio_url` / `audio_length` / `audio_type` | Enclosure |
| `episode_link` / `image_url` | Links |
| `season` / `episode_number` / `episode_type` / `is_explicit` | Episode metadata |

## Use cases

- **Podcast analytics & charts** — episode cadence, duration, catalog growth.
- **Ad-tech & sponsorship research** — identify shows by genre and output.
- **Transcript / AI pipelines** — pair episode audio URLs with metadata.
- **Media monitoring** — track when shows publish and what they cover.
- **Market maps** — build a database of shows in any niche.

## Input

```json
{
  "searchQueries": ["true crime", "startups"],
  "includeEpisodes": true,
  "maxEpisodesPerPodcast": 200
}
```

```json
{ "podcastIds": [1434243584], "includeEpisodes": true }
{ "feedUrls": ["https://lexfridman.com/feed/podcast/"], "maxEpisodesPerPodcast": 50 }
```

## Pricing

**Pay per result.** You are charged only for rows delivered to your dataset (podcasts and episodes), plus a small per-run fee. No subscription, no minimum.

## Notes

- Episodes are read from each show's public RSS feed — the standard, openly-syndicated podcast format; the iTunes Search/Lookup API is used for discovery and as a fallback.
- De-duplicated across queries, IDs and feed URLs.
- Intended for research and analytics. Podcast audio and text remain the property of their publishers — respect their terms and copyright.
