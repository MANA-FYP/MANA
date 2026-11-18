"""The autopilot's behaviour tree (#78, #187): nothing in it moves the chair by itself."""

import xml.etree.ElementTree as ET
from pathlib import Path

TREE = Path(__file__).resolve().parents[1] / "config" / "navigate_no_recovery.xml"


def test_the_tree_only_plans_checks_and_follows():
    used = {e.tag for e in ET.parse(TREE).iter()}
    assert used == {
        "root", "BehaviorTree", "PipelineSequence", "RateController", "Fallback",
        "ReactiveSequence", "Inverter", "GlobalUpdatedGoal", "IsPathValid",
        "ComputePathToPose", "FollowPath",
    }  # fmt: skip
    # no RecoveryNode, Spin, BackUp, Wait, DriveOnHeading or ClearCostmap
