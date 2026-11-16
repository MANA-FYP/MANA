"""Tests for the USB helper, with a fake adb. Nothing here touches a phone."""

from demo.usb import connect, parse_devices

LIST_OK = "List of devices attached\nR58M123ABC\tdevice\n\n"
LIST_WAITING = "List of devices attached\nR58M123ABC\tunauthorized\n\n"
LIST_NONE = "List of devices attached\n\n"
LIST_WIFI = "List of devices attached\n192.168.1.23:41235\tdevice\n\n"


def fake_adb(devices_text, reverse_code=0, connect_out="connected to 192.168.1.23:41235"):
    calls = []

    def run(adb, *args):
        calls.append(args)
        if args[0] == "devices":
            return 0, devices_text
        if args[0] == "pair":
            return 0, "Successfully paired to 192.168.1.23:37011"
        if args[0] == "connect":
            return 0, connect_out
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
    assert ("-s", "R58M123ABC", "reverse", "tcp:8000", "tcp:8000") in run.calls
    assert "http://localhost:8000/phone" in message and "by cable" in message


def test_an_unauthorised_phone_asks_for_the_usb_debugging_question():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_WAITING))
    assert not ok and "Allow USB debugging" in message


def test_no_phone_says_to_plug_it_in():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_NONE))
    assert not ok and "No phone found" in message


def test_a_failed_reverse_is_reported():
    ok, message = connect(8000, adb="adb", run=fake_adb(LIST_OK, reverse_code=1))
    assert not ok and "adb reverse failed" in message


def test_without_adb_the_message_says_both_ways_need_it(monkeypatch):
    monkeypatch.setattr("demo.usb.find_adb", lambda: None)
    ok, message = connect(8000, run=fake_adb(LIST_OK))
    assert not ok and "adb was not found" in message


def test_over_wifi_the_phone_is_paired_connected_and_tunnelled():
    run = fake_adb(LIST_WIFI)
    wifi, pair = "192.168.1.23:41235", ("192.168.1.23:37011", "482915")
    ok, message = connect(8000, adb="adb", run=run, wifi=wifi, pair=pair)
    assert ok and "over Wi-Fi" in message and "http://localhost:8000/phone" in message
    assert run.calls[:2] == [("pair", *pair), ("connect", wifi)]
    assert ("-s", wifi, "reverse", "tcp:8000", "tcp:8000") in run.calls


def test_a_phone_not_reached_over_wifi_says_why():
    run = fake_adb(LIST_NONE, connect_out="failed to connect to 192.168.1.23:41235")
    ok, message = connect(8000, adb="adb", run=run, wifi="192.168.1.23:41235")
    assert not ok and "could not reach the phone" in message
