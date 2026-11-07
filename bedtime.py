"""Bedtime: at a time you choose, the pets tell you to go to bed and sleep until morning.

If your computer is asleep or the app isn't running at wake-up time, they wake up the
next time it checks (within a second of the computer waking).
"""
import datetime as dt
import time

REMIND_EVERY = 30 * 60      # "still up?" reminder interval while you keep using the computer
RECENTLY_ACTIVE = 10 * 60   # you count as "still up" if you switched apps within this long


def parse_time(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.time(h, m)


def is_night(now: dt.datetime, bed: dt.time, wake: dt.time) -> bool:
    t = now.time()
    if bed == wake:
        return False
    if bed < wake:                  # e.g. bed 1:00 AM, wake 9:00 AM
        return bed <= t < wake
    return t >= bed or t < wake     # e.g. bed 11:30 PM, wake 8:00 AM (crosses midnight)


def friendly(t: dt.time):
    return dt.datetime.combine(dt.date.today(), t).strftime("%I:%M %p").lstrip("0")


class Bedtime:
    def __init__(self, pets, tracker, coach, bed="23:30", wake="08:00", enabled=False,
                 clock=dt.datetime.now, monotonic=time.time):
        self.pets = pets
        self.tracker = tracker
        self.coach = coach
        self.clock = clock
        self.monotonic = monotonic
        self.bed, self.wake = parse_time(bed), parse_time(wake)
        self.enabled = enabled
        self.asleep = False          # pets are in bedtime mode
        self.sleep_at = None         # when to lie down after saying goodnight
        self.last_reminder = 0.0

    def set_times(self, bed, wake):
        self.bed, self.wake = parse_time(bed), parse_time(wake)

    def describe(self):
        return f"{friendly(self.bed)} to {friendly(self.wake)}"

    def check(self):
        now = self.monotonic()
        night = self.enabled and is_night(self.clock(), self.bed, self.wake)
        # A focus session late at night is your call: bedtime waits until it ends.
        bedtime_now = night and not self.coach.active

        if bedtime_now and not self.asleep:
            self._go_to_bed(now)
        elif not bedtime_now and self.asleep:
            self._wake_up(good_morning=self.enabled and not night)
        elif self.asleep:
            if self.sleep_at and now >= self.sleep_at:
                self.sleep_at = None
                for pet in self._visible():
                    pet.nap(10_000)
            user_active = now - self.tracker.since < RECENTLY_ACTIVE
            if user_active and now - self.last_reminder > REMIND_EVERY:
                self.last_reminder = now
                pet = self._first()
                if pet:
                    pet.wake()
                    pet.say("still up? 🌙\ngo to bed!", 5)
                self.sleep_at = now + 5

    def _go_to_bed(self, now):
        self.asleep = True
        self.last_reminder = now
        for pet in self.pets:
            pet.bedtime = True
        pet = self._first()
        if pet:
            pet.say("it's bedtime! 🌙\ntime to sleep?", 6)
        self.sleep_at = now + 6

    def _wake_up(self, good_morning):
        self.asleep = False
        self.sleep_at = None
        for pet in self.pets:
            pet.bedtime = False
            pet.wake()
        if good_morning:
            for i, pet in enumerate(self._visible()):
                pet.playful()
                if i == 0:
                    pet.say("good morning! ☀️", 5)

    def _visible(self):
        return [p for p in self.pets if p.isVisible() and p.away <= 0]

    def _first(self):
        v = self._visible()
        return v[0] if v else None
