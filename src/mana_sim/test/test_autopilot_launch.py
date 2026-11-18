"""The autopilot launch file finds the lattice planner's primitives in the package's share (#78)."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch_ros")
LAUNCH = Path(__file__).resolve().parents[1] / "launch" / "autopilot.launch.py"


def test_the_lattice_file_comes_from_the_package_share(monkeypatch):
    spec = importlib.util.spec_from_file_location("autopilot_launch", LAUNCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "get_package_share_directory", lambda name: f"/share/{name}")
    path = mod.lattice_file().replace("\\", "/")
    assert path.startswith("/share/nav2_smac_planner/")
    assert path.endswith("0.5m_turning_radius/diff/output.json")
