"""Tests for the USB helper, with a fake adb. Nothing here touches a phone."""

from demo.usb import connect, parse_devices

LIST_OK = "List of devices attached\nR58M123ABC\tdevice\n\n"
LIST_WAITING = "List of devices attached\nR58M123ABC\tunauthorized\n\n"
LIST_NONE = "List of devices attached\n\n"


def fake_adb(devices_text, reverse_code=0):
    calls = []

    def run(adb, *args):
        calls.append(args)
        if args[0] == "devices":
            return 0, devices_text
        return reverse_code, "" if reverse_code == 0 else "error: closed"

    run.calls = calls
    return run


def test_devices_are_split_into_ready_and_not_ready():
    assert parse_devices(LIST_OK) == (["R58M123ABC"], [])
    assert parse_devices(LIST_WAITING) == ([], ["R58M123ABC"])
    assert parse_devices(LIST_NONE) == ([], [])


def test_a_ready_phone_gets_the_reverse_tunnel_and_the_localhost_address():
    run = fake_adb(LIST_OK)
    ok, message = connect(8000, adb="adb", run=run)
    assert ok
    assert ("reverse", "tcp:8000", "tcp:8000") in run.calls
    assert "http://localhost:8000/phone" in message


def test_an_unauthorised_phone_asks_for_the_usb_debugging_question():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_WAITING))
    assert not ok and "Allow USB debugging" in message


def test_no_phone_says_to_plug_it_in():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_NONE))
    assert not ok and "No phone found" in message


def test_a_failed_reverse_is_reported():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_OK, reverse_code=1))
    assert not ok and "adb reverse failed" in message


def test_without_adb_the_message_points_to_the_wifi_way(monkeypatch):
    monkeypatch.setattr("demo.usb.find_adb", lambda: None)
    ok, message = connect(8000, run=fake_adb(LIST_OK))
    assert not ok and "adb was not found" in message
