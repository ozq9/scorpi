"""Scorpi Radio from chat: play a mood, genre or station, and control whatever is playing (the radio or another program)."""

from .. import music
from . import Result, ToolError, tool


def _describe(snap):
    radio, system = snap["radio"], snap["system"]
    if radio["playing"] and radio["station"]:
        song = " – ".join(x for x in (radio["artist"], radio["title"]) if x)
        return "Scorpi Radio is playing %s%s." % (radio["station"]["name"], (": " + song) if song else "")
    if system:
        verb = "is playing" if system["status"] == "Playing" else "has paused"
        return "%s %s %s%s." % (system["app"] or "A program", verb, system["title"], (" by " + system["artist"]) if system["artist"] else "")
    return "Nothing is playing."


@tool(
    "play_music",
    "Scorpi Radio and media playback. play: start internet radio for a mood (focus, chill, lofi, jazz, classical, electronic, "
    "rock, hiphop, pop, news), a genre or a station name — without a query it resumes. pause / next / previous control the "
    "radio, or Spotify/YouTube/any player when the radio is off. now_playing says what plays. favorite saves the station.",
    {
        "action": "string[play,pause,next,previous,now_playing,favorite]: What to do",
        "query?": "string: Mood, genre or station name for play, e.g. 'focus', 'jazz', 'BBC Radio 6'",
    },
    group="media",
    risk="write",
)
def play_music(ctx, action, query=""):
    snap = music.snapshot()
    radio, system = snap["radio"], snap["system"]
    radio_on = radio["playing"] and radio["station"]
    # the device the message came from; push-to-talk on the desktop, routines and agents: the PC's app window
    speaker = music.device_for(ctx.conv_id) or "host"

    if action == "now_playing":
        return Result(_describe(snap))

    if action == "play":
        if query:
            try:
                stations, mood = music.stations_for(query)
            except Exception as exc:
                raise ToolError("The radio directory did not answer: %s" % exc)
            if not stations:
                raise ToolError("No playable stations found for '%s'. Try a genre like jazz, lofi or rock." % query)
            music.play(stations[0], speaker=speaker, mood=mood, queue=stations)
            others = ", ".join(s["name"] for s in stations[1:4])
            return Result("Playing %s on Scorpi Radio.%s" % (stations[0]["name"], (" Next up if they skip: " + others) if others else ""))
        # a paused player (Spotify…) is what "resume" most likely means; otherwise the last station
        if system and system["status"] == "Paused" and not radio_on:
            music.system_control("play")
            return Result("Resumed %s in %s." % (system["title"], system["app"] or "the player"))
        if radio["station"]:
            music.play(speaker=speaker)
            return Result("Resumed %s on Scorpi Radio." % radio["station"]["name"])
        raise ToolError("Nothing to resume. Say what to play, e.g. a mood or genre.")

    if action == "pause":
        if radio_on:
            music.pause()
            return Result("Paused Scorpi Radio.")
        if system and system["status"] == "Playing":
            music.system_control("pause")
            return Result("Paused %s." % (system["app"] or "the player"))
        return Result("Nothing is playing.")

    if action in ("next", "previous"):
        if radio_on:
            music.skip(1 if action == "next" else -1)
            now = music.snapshot()["radio"]["station"]
            return Result("Switched to %s." % now["name"])
        if system:
            music.system_control(action)
            return Result("Skipped to the %s track in %s." % ("next" if action == "next" else "previous", system["app"] or "the player"))
        return Result("Nothing is playing.")

    if action == "favorite":
        if not radio["station"]:
            raise ToolError("No station to save yet.")
        music.set_favorite(radio["station"], True)
        return Result("Saved %s to the radio favourites." % radio["station"]["name"])

    raise ToolError("Unknown action %s" % action)
