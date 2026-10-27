from setuptools import setup

package_name = "mana_perception"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},  # tells colcon to run the tests with pytest
    zip_safe=True,
    maintainer="Muhammad Waqas",
    maintainer_email="waqasmalik0007@users.noreply.github.com",
    description="MANA perception (M1 Vision): ground from the 3D camera, and a scripted stub.",
    license="AGPL-3.0-only",
    entry_points={
        "console_scripts": [
            "stub_node = mana_perception.stub_node:main",
            "depth_node = mana_perception.depth_node:main",
        ]
    },
)
