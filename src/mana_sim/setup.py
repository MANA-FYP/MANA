import os
from glob import glob

from setuptools import setup

package_name = "mana_sim"


def tree(source: str, target: str):
    """Every file under source, installed under share/<package>/<target>, folders kept."""
    out = {}
    for root, _, files in os.walk(source):
        if files:
            dest = os.path.normpath(
                os.path.join("share", package_name, target, os.path.relpath(root, source))
            )
            out[dest] = [os.path.join(root, f) for f in files]
    return list(out.items())


setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.py")),
        ("share/" + package_name + "/worlds", glob("worlds/*.sdf")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        *tree("models", "models"),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},  # tells colcon to run the tests with pytest
    zip_safe=True,
    maintainer="Komail Raza",
    maintainer_email="komail0@users.noreply.github.com",
    description="MANA simulation (SIM): the Gazebo test route and the wheel-and-track test host.",
    license="AGPL-3.0-only",
    entry_points={
        "console_scripts": [
            "host_node = mana_sim.host_node:main",
            "sensors_node = mana_sim.sensors_node:main",
            "fault_node = mana_sim.fault_node:main",
            "fault_script = mana_sim.fault_script:main",
            "driver_select = mana_sim.driver_select_node:main",
            "keyboard = mana_sim.keyboard_node:main",
        ]
    },
)
