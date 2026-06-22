# this is where twitter/x rss-to-discord forwarding lives
import os
import asyncio
import aiohttp
import feedparser

POLL_INTERVAL = 43200  # 12 hours
GUID_FILE = 'data/last_tweet.txt'

class TwitterRSSForwarder:
    def __init__(self, rss_url, webhook_url):
        self.rss_url = rss_url
        self.webhook_url = webhook_url
        self.last_guid = None
        self._running = False

    async def start(self):
        self._running = True
        self._load_last_guid()
        print(f'✅ twitter rss forwarder started — polling {self.rss_url}')

        while self._running:
            try:
                await self._poll()
            except Exception as e:
                print(f'twitter rss poll failed: {e}')

            await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        self._running = False
        print('twitter rss forwarder stopped')

    def _load_last_guid(self):
        if os.path.exists(GUID_FILE):
            with open(GUID_FILE, 'r') as f:
                self.last_guid = f.read().strip() or None

    def _save_last_guid(self):
        os.makedirs('data', exist_ok=True)
        with open(GUID_FILE, 'w') as f:
            f.write(self.last_guid or '')

    async def _poll(self):
        feed = feedparser.parse(self.rss_url)

        if feed.bozo and not feed.entries:
            print(f'twitter rss feed error: {feed.bozo_exception}')
            return

        if not feed.entries:
            return

        # first run — just record the latest guid, don't post anything
        if self.last_guid is None:
            self.last_guid = feed.entries[0].get('id', '')
            self._save_last_guid()
            print(f'twitter rss first run — recorded guid: {self.last_guid}')
            return

        # collect new entries (entries are newest-first in rss)
        new_entries = []
        for entry in feed.entries:
            guid = entry.get('id', '')
            if guid == self.last_guid:
                break
            new_entries.append(entry)

        if not new_entries:
            return

        # post oldest first so discord order matches timeline
        for entry in reversed(new_entries):
            await self._post_to_discord(entry)

        # update last seen guid to the newest
        self.last_guid = new_entries[0].get('id', '')
        self._save_last_guid()

    async def _post_to_discord(self, entry):
        title = entry.get('title', '')
        link = entry.get('link', '')

        # nitter links -> real twitter links
        if 'nitter.net' in link:
            link = link.replace('nitter.net', 'twitter.com').replace('#m', '')

        content = f'{title}\n{link}'.strip()

        payload = {
            'username': '@CalabiyauLeaks',
            'content': content[:2000],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook_url, json=payload) as resp:
                if resp.status not in (200, 204):
                    print(f'twitter webhook post failed: {resp.status} {await resp.text()}')
                else:
                    print(f'twitter rss posted: {title[:60]}...')
