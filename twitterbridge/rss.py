# Twitter/X RSS-to-Discord forwarding via Nitter RSS feeds.
# Polls RSS endpoints on a timer, auto-rotates through fallback URLs
# when an instance returns empty or fails, and posts new entries
# to a Discord webhook.
import os
import asyncio
import logging
import aiohttp
import feedparser

logger = logging.getLogger(__name__)

POLL_INTERVAL = 300          # 5 minutes
REQUEST_TIMEOUT = 30         # seconds per HTTP call
MAX_EMPTY_STRIKES = 3        # consecutive empty polls before rotating URL


class TwitterRSSForwarder:
    """Polls a Twitter RSS feed and forwards new items to a Discord webhook.

    Reads TWITTER_RSS_URL and optional TWITTER_RSS_FALLBACKS from the
    environment.  Falls back through URLs when the current one fails or
    returns empty entries.
    """

    def __init__(self, webhook_url: str, guid_file: str = 'data/last_tweet.txt'):
        primary = os.getenv('TWITTER_RSS_URL', '')
        fallbacks_raw = os.getenv('TWITTER_RSS_FALLBACKS', '')

        urls = [primary] if primary else []
        if fallbacks_raw:
            urls.extend(u.strip() for u in fallbacks_raw.split(',') if u.strip())

        if not urls:
            raise ValueError('TWITTER_RSS_URL is not set')

        self.rss_urls = urls
        self.webhook_url = webhook_url
        self.guid_file = guid_file

        self.last_guid: str | None = None
        self._running = False
        self._session: aiohttp.ClientSession | None = None

        self._current_idx = 0
        self._empty_strikes = 0

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self._running = True
        self._load_last_guid()

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
        )

        logger.info('twitter rss forwarder started — %d URLs, current: %s',
                    len(self.rss_urls), self.rss_urls[self._current_idx])

        while self._running:
            try:
                await self._poll_cycle()
            except asyncio.CancelledError:
                logger.info('twitter rss forwarder cancelled during poll')
                break
            except Exception:
                logger.exception('unhandled error in twitter rss poll loop — will retry next cycle')

            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                self._running = False
                raise

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
        start_idx = self._current_idx
        errors: list[str] = []

        for offset in range(len(self.rss_urls)):
            idx = (start_idx + offset) % len(self.rss_urls)
            url = self.rss_urls[idx]

            try:
                entries = await self._fetch_feed(url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f'{url}: {exc}')
                logger.warning('twitter rss url failed: %s — %s', url, exc)
                continue

            if not entries:
                logger.debug('twitter rss url returned empty: %s', url)
                self._empty_strikes += 1
                if self._empty_strikes >= MAX_EMPTY_STRIKES:
                    logger.warning('twitter rss %d consecutive empty polls — rotating away from %s',
                                   self._empty_strikes, url)
                    self._rotate()
                else:
                    logger.debug('twitter rss empty strike %d/%d on %s',
                                 self._empty_strikes, MAX_EMPTY_STRIKES, url)
                return

            self._empty_strikes = 0
            if idx != self._current_idx:
                logger.info('twitter rss switched to %s', url)
                self._current_idx = idx

            await self._process_entries(entries)
            return

        logger.error('twitter rss all %d URLs failed: %s', len(self.rss_urls), '; '.join(errors))
        self._rotate()

    async def _fetch_feed(self, url: str) -> list[dict] | None:
        if self._session is None:
            raise RuntimeError('start() must be called before fetching')

        async with self._session.get(url) as resp:
            resp.raise_for_status()
            text = await resp.text()

        feed = feedparser.parse(text)

        if feed.bozo and not feed.entries:
            logger.warning('twitter rss parse error from %s: %s', url, feed.bozo_exception)
            return None

        return feed.entries

    # ------------------------------------------------------------------
    # Entry processing
    # ------------------------------------------------------------------

    async def _process_entries(self, entries: list[dict]):
        if self.last_guid is None:
            self.last_guid = entries[0].get('id', '')
            self._save_last_guid()
            logger.info('twitter rss first run — recorded guid: %s', self.last_guid)
            return

        # Find the index of the last known GUID in the current feed.
        last_idx = None
        for i, entry in enumerate(entries):
            if entry.get('id', '') == self.last_guid:
                last_idx = i
                break

        if last_idx is None:
            # Stored GUID not in this feed — nitter returned a stale/partial
            # snapshot.  Update to the newest GUID *without* posting, so we
            # don't spam the channel with duplicates when the full feed returns.
            logger.warning(
                'stored guid %s not found in feed (%d entries) — '
                'updating to %s without posting',
                self.last_guid, len(entries), entries[0].get('id', '?'),
            )
            self.last_guid = entries[0].get('id', '')
            self._save_last_guid()
            return

        new_entries = entries[:last_idx]  # everything newer than last known
        if not new_entries:
            return

        for entry in reversed(new_entries):
            await self._post_to_discord(entry)

        self.last_guid = new_entries[0].get('id', '')
        self._save_last_guid()

    # ------------------------------------------------------------------
    # Discord webhook
    # ------------------------------------------------------------------

    async def _post_to_discord(self, entry):
        link = entry.get('link', '')
        if 'nitter.net' in link:
            link = link.replace('nitter.net', 'twitter.com').replace('#m', '')

        payload = {
            'username': '@CalabiyauLeaks',
            'content': link,
        }

        async with self._session.post(self.webhook_url, json=payload) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                logger.warning('twitter webhook post failed: %s %s', resp.status, body[:200])
            else:
                logger.info('twitter rss posted: %s', link)

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    def _rotate(self):
        self._current_idx = (self._current_idx + 1) % len(self.rss_urls)
        self._empty_strikes = 0
        logger.info('twitter rss rotated to URL %d/%d: %s',
                    self._current_idx + 1, len(self.rss_urls), self.rss_urls[self._current_idx])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_last_guid(self):
        if os.path.exists(self.guid_file):
            with open(self.guid_file, 'r') as f:
                self.last_guid = f.read().strip() or None

    def _save_last_guid(self):
        os.makedirs('data', exist_ok=True)
        with open(self.guid_file, 'w') as f:
            f.write(self.last_guid or '')
