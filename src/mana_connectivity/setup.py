from setuptools import setup

package_name = "mana_connectivity"

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
    maintainer="Mushahid Hussain",
    maintainer_email="184832252+mushi0541@users.noreply.github.com",
    description="MANA connectivity (M3): Wi-Fi or Bluetooth for alerts, with none lost.",
    license="AGPL-3.0-only",
    entry_points={"console_scripts": ["link_node = mana_connectivity.link_node:main"]},
)
