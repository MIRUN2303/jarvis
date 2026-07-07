"""Final integration test for all new modules"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    print("=== Testing imports ===")
    from actions.youtube_video import youtube_video, _ytdlp_search_first, _open_in_brave
    from actions.music_player import music_player, play, search, now_playing
    from actions.vision_automation import vision_automation, find_text, describe_screen, _OCR_OK
    from actions.timer import timer_control, set_timer, list_timers, cancel_all_timers
    print("  All imports OK")

def test_ytdlp():
    print("\n=== Testing yt-dlp search ===")
    from actions.youtube_video import _ytdlp_search_first
    url = _ytdlp_search_first("test")
    assert url and "youtube.com" in url, f"Bad URL: {url}"
    print(f"  OK: {url}")

def test_timer():
    print("\n=== Testing timer ===")
    from actions.timer import set_timer, list_timers, cancel_all_timers
    cancel_all_timers()
    r = set_timer("3 seconds", "TestTimer")
    assert "3 seconds" in r, f"Bad response: {r}"
    assert "TestTimer" in r
    time.sleep(1)
    tl = list_timers()
    assert "TestTimer" in tl, f"Timer not in list: {tl}"
    cancel_all_timers()
    print("  Timer OK")

def test_vision_import():
    print("\n=== Testing vision_automation (no OCR yet) ===")
    from actions.vision_automation import vision_automation, _OCR_OK
    print(f"  OCR ready: {_OCR_OK}")
    # describe_screen may take long if OCR still downloading model, skip for now

def test_music():
    print("\n=== Testing music_player search ===")
    from actions.music_player import search, now_playing
    r = search("test song", 2)
    assert "I found" in r or "found" in r or "No results" in r
    print(f"  Search OK")
    np = now_playing()
    assert "Nothing" in np or "playing" in np
    print(f"  Now playing: {np}")

if __name__ == "__main__":
    test_imports()
    test_ytdlp()
    test_timer()
    test_vision_import()
    test_music()
    print("\n=== ALL TESTS PASSED ===")
