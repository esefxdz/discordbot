# Twitter/X RSS-to-Discord forwarding (feed: self-hosted RSSHub, see esefrss).
# One poller fetches the feed and fans new entries out to every webhook.
# Each webhook remembers which tweets it has already posted, so a reordered,
# partial or briefly broken feed never skips or reposts anything.
import os
import json
import time
import random
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit
import aiohttp
import feedparser

logger = logging.getLogger(__name__)

# The feed comes from RSSHub on yuuka using a real X account's cookie, so
# polls are jittered and much slower at night to avoid a robotic 24/7 pattern.
POLL_INTERVAL = (240, 480)          # daytime: random 4-8 minutes
NIGHT_POLL_INTERVAL = (3600, 7200)  # night: random 1-2 hours
NIGHT_HOURS = range(1, 6)           # 01:00-05:59 Istanbul; the account posts ~06:00-08:00
LOCAL_TZ = ZoneInfo('Europe/Istanbul')
REQUEST_TIMEOUT = 30                # seconds per HTTP call
SEEN_LIMIT = 500                    # remembered guids per webhook (the feed holds 20)
POST_GAP = 1.5                      # seconds between webhook posts
POST_ATTEMPTS = 3
ALERT_AFTER = 90 * 60               # feed broken this long -> DM the owner

EMBED_HOST = 'fxtwitter.com'        # proper Discord embeds; X never sees these


def embed_link(link: str) -> str:
    """Point a tweet link at fxtwitter so Discord shows a real embed."""
    parts = urlsplit(link.replace('#m', ''))
    if parts.netloc in ('x.com', 'twitter.com', 'www.twitter.com', 'nitter.net'):
        parts = parts._replace(netloc=EMBED_HOST)
    return urlunsplit(parts)


def _guid(entry) -> str:
    return entry.get('id') or entry.get('link', '')


def tweet_id(guid: str) -> int | None:
    """Status id from a guid. RSSHub uses the timeline entry's own id (a retweet's
    id, not the original's), so ids grow in the order the account posted them,
    unlike the feed order and pubDate (the original tweet's date)."""
    tail = guid.rstrip('/').rsplit('/', 1)[-1]
    return int(tail) if tail.isdigit() else None


class TwitterRSSForwarder:
    """Polls the Twitter RSS feed and forwards new items to Discord webhooks.

    Reads TWITTER_RSS_URL and optional comma-separated TWITTER_RSS_FALLBACKS.
    `webhooks` maps a name (used in the state file) to a webhook URL.
    `alert` is an optional coroutine taking a message, called when the feed
    has been broken for ALERT_AFTER and again when it recovers.
    """

    def __init__(self, webhooks: dict[str, str], state_file: str = 'data/twitter_seen.json', alert=None):
        primary = os.getenv('TWITTER_RSS_URL', '')
        fallbacks_raw = os.getenv('TWITTER_RSS_FALLBACKS', '')

        urls = [primary] if primary else []
        if fallbacks_raw:
            urls.extend(u.strip() for u in fallbacks_raw.split(',') if u.strip())

        if not urls:
            raise ValueError('TWITTER_RSS_URL is not set')

        self.rss_urls = urls
        self.webhooks = {name: url for name, url in webhooks.items() if url}
        self.state_file = state_file
        self.alert = alert

        self.seen: dict[str, list[str]] = {}
        self._running = False
        self._session: aiohttp.ClientSession | None = None

        self._bad_since: float | None = None
        self._alerted = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self._running = True
        self._load_state()

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self._session = aiohttp.ClientSession(timeout=timeout)

        logger.info('twitter rss forwarder started — %s -> %s',
                    self.rss_urls[0], ', '.join(self.webhooks) or 'no webhooks')

        while self._running:
            try:
                await self._poll_cycle()
            except asyncio.CancelledError:
                logger.info('twitter rss forwarder cancelled during poll')
                break
            except Exception:
                logger.exception('unhandled error in twitter rss poll loop — will retry next cycle')

            try:
                await asyncio.sleep(self._next_delay())
            except asyncio.CancelledError:
                self._running = False
                raise

    @staticmethod
    def _next_delay() -> float:
        night = datetime.now(LOCAL_TZ).hour in NIGHT_HOURS
        return random.uniform(*(NIGHT_POLL_INTERVAL if night else POLL_INTERVAL))

    def stop(self):
        self._running = False
        logger.info('twitter rss forwarder stop requested')

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None
            logger.debug('twitter rss http session closed')

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    async def _poll_cycle(self):
        entries, problem = await self._fetch_any()
        if not entries:
            await self._note_bad(problem or 'feed returned no items')
            return
        await self._note_good()

        for name, webhook in self.webhooks.items():
            await self._forward(name, webhook, entries)

    async def _fetch_any(self) -> tuple[list[dict], str | None]:
        problems = []
        for url in self.rss_urls:
            try:
                async with self._session.get(url) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                problems.append(f'{url}: {exc}')
                continue

            feed = feedparser.parse(text)
            if feed.entries:
                return feed.entries, None
            problems.append(f'{url}: no items' + (f' ({feed.bozo_exception})' if feed.bozo else ''))
        return [], '; '.join(problems)

    async def _forward(self, name: str, webhook: str, entries: list[dict]):
        ids = [_guid(e) for e in entries]
        seen = self.seen.get(name)

        if seen is None:
            # first run for this webhook: remember the current feed, post nothing
            self.seen[name] = ids[::-1]
            self._save_state()
            logger.info('twitter rss [%s] first run — remembered %d tweets', name, len(ids))
            return

        seen_set = set(seen)
        known = [g for g in ids if g in seen_set]
        if not known:
            # nothing in the feed is familiar (long outage or a feed change):
            # re-baseline instead of flooding the channel with the whole page
            logger.warning('twitter rss [%s] no known tweets in feed — re-baselining without posting', name)
            seen.extend(ids[::-1])
            del seen[:-SEEN_LIMIT]
            self._save_state()
            return

        # Unseen entries newer than the oldest known one in this feed are new, wherever
        # the feed puts them. Unseen entries older than every known one are old tweets
        # that slid in when something was deleted, so they are only remembered.
        known_ids = [i for i in map(tweet_id, known) if i is not None]
        oldest_known = min(known_ids) if known_ids else None
        new, slid_in = [], []
        for e in entries:
            g = _guid(e)
            if g in seen_set:
                continue
            tid = tweet_id(g)
            if tid is not None and oldest_known is not None and tid < oldest_known:
                slid_in.append(g)
            else:
                new.append(e)
        if slid_in:
            seen.extend(slid_in)
            del seen[:-SEEN_LIMIT]
            self._save_state()

        new.sort(key=lambda e: tweet_id(_guid(e)) or 0)
        for entry in new:  # oldest first
            if not await self._post(name, webhook, entry):
                break  # leave the rest unseen; retried next poll
            seen.append(_guid(entry))
            del seen[:-SEEN_LIMIT]
            self._save_state()
            await asyncio.sleep(POST_GAP)

    # ------------------------------------------------------------------
    # Discord webhook
    # ------------------------------------------------------------------

    async def _post(self, name: str, webhook: str, entry) -> bool:
        link = embed_link(entry.get('link', ''))
        payload = {'username': '@CalabiyauLeaks', 'content': link}

        for _ in range(POST_ATTEMPTS):
            try:
                async with self._session.post(webhook, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info('twitter rss [%s] posted: %s', name, link)
                        return True
                    if resp.status == 429:
                        data = await resp.json(content_type=None)
                        wait = float(data.get('retry_after', 2))
                        logger.info('twitter rss [%s] rate limited, waiting %.1fs', name, wait)
                        await asyncio.sleep(min(wait, 60))
                        continue
                    body = await resp.text()
                    logger.warning('twitter rss [%s] webhook post failed: %s %s', name, resp.status, body[:200])
                    if resp.status < 500:
                        return False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('twitter rss [%s] webhook post error: %s', name, type(exc).__name__)
            await asyncio.sleep(2)
        return False

    # ------------------------------------------------------------------
    # Outage alerts
    # ------------------------------------------------------------------

    async def _note_bad(self, problem: str):
        now = time.monotonic()
        if self._bad_since is None:
            self._bad_since = now
        logger.warning('twitter rss feed problem: %s', problem)

        broken_for = now - self._bad_since
        if not self._alerted and broken_for >= ALERT_AFTER:
            self._alerted = True
            await self._send_alert(
                f'⚠️ esefrss: the @CalabiyauLeaks feed has been broken for {broken_for / 60:.0f} min '
                f'({problem[:300]}).\nIf X locked @esefos2: log in on arona, pass the check, then put a fresh '
                f'auth_token in ~/esefrss/.env and `docker compose up -d --force-recreate` (see esefrss README).'
            )

    async def _note_good(self):
        if self._alerted:
            await self._send_alert('✅ esefrss: the @CalabiyauLeaks feed is working again.')
        self._bad_since = None
        self._alerted = False

    async def _send_alert(self, text: str):
        if self.alert is None:
            return
        try:
            await self.alert(text)
        except Exception:
            logger.exception('twitter rss alert failed')

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                self.seen = {k: list(v) for k, v in json.load(f).items()}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file) or '.', exist_ok=True)
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.seen, f)
        os.replace(tmp, self.state_file)
