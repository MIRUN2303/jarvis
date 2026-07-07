"""Test all modules import and basic functions work"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

def test_weather():
    from actions.weather import get_weather
    r = get_weather("london")
    assert "Weather" in r or "°C" in r
    print(f"Weather OK: {r[:80]}...")

def test_translator():
    from actions.translator import translate, detect_language
    r = translate("hello", "turkish")
    assert "merhaba" in r.lower() or "Translation" in r
    print(f"Translate OK: {r}")
    d = detect_language("bonjour")
    assert "French" in d or "confidence" in d
    print(f"Detect OK: {d}")

def test_smart_lists():
    from actions.smart_lists import add_item, show_list, list_lists, delete_list
    add_item("testlist", "test item 1")
    add_item("testlist", "test item 2")
    s = show_list("testlist")
    assert "test item 1" in s
    print(f"Lists OK: {s[:80]}...")
    delete_list("testlist")

def test_briefing():
    from actions.briefing import quick_briefing
    r = quick_briefing("london")
    assert "briefing" in r.lower()
    print(f"Briefing OK")

def test_imports():
    from actions.weather import weather
    from actions.briefing import briefing
    from actions.smart_lists import smart_lists
    from actions.translator import translator
    print("All imports OK")

if __name__ == "__main__":
    test_imports()
    test_weather()
    test_translator()
    test_smart_lists()
    test_briefing()
    print("\n=== ALL MODULE TESTS PASSED ===")
