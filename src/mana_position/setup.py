from setuptools import setup

package_name = "mana_position"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/localisation.yaml"]),
        ("share/" + package_name + "/launch", ["launch/localisation.launch.py"]),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},  # tells colcon to run the tests with pytest
    zip_safe=True,
    maintainer="Mushahid Hussain",
    maintainer_email="184832252+mushi0541@users.noreply.github.com",
    description="MANA position (M2): GPS, the lidar map or odometry as the position source.",
    license="AGPL-3.0-only",
    entry_points={"console_scripts": ["position_node = mana_position.position_node:main"]},
)
