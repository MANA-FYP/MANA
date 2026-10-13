"""Checks on the mana_msgs message files that need no ROS install.

Every module report must start with the same two fields, so the decision core can line reports
up by time and weigh them by trust. Every message file must also be registered for the build.
"""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "mana_msgs"
OUTPUTS = {"DriveRequest", "UserMessage"}  # from the decision core, so no trust field


def fields(msg):
    """Field lines of a .msg file, without comments, blank lines or constants."""
    lines = (line.split("#", 1)[0].strip() for line in msg.read_text().splitlines())
    return [" ".join(line.split()) for line in lines if line and "=" not in line]


def test_every_message_is_registered_in_cmake():
    on_disk = {p.name for p in (PKG / "msg").glob("*.msg")}
    registered = set(re.findall(r'"msg/(\w+\.msg)"', (PKG / "CMakeLists.txt").read_text()))
    assert on_disk and on_disk == registered


def test_module_reports_start_with_header_and_trust():
    for msg in (PKG / "msg").glob("*.msg"):
        expected = ["std_msgs/Header header"]
        if msg.stem not in OUTPUTS:
            expected.append("float32 trust")
        assert fields(msg)[: len(expected)] == expected, msg.name
