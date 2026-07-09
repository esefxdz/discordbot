import wavelink
from collections import deque
from .constants import LOOP_OFF


class GuildState:
    def __init__(self):
        self.queue:    deque                    = deque()
        self.history:  list                     = []   # last 10 tracks
        self.loop:     int                      = LOOP_OFF
        self.autoplay: bool                     = False
        self.current:  wavelink.Playable | None = None
        self.mode:     str | None               = None

    def push_history(self, track: wavelink.Playable):
        self.history.insert(0, track)
        if len(self.history) > 10:
            self.history.pop()
