"""Use the same Jupyter runtime versions in the demo images and server CI."""

import argparse
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

JUPYTER_PACKAGES = (
    "ipykernel",
    "jupyter-client",
    "jupyter-collaboration",
    "jupyter-collaboration-ui",
    "jupyter-core",
    "jupyter-docprovider",
    "jupyter-server",
    "jupyter-server-fileid",
    "jupyter-server-ydoc",
    "jupyter-ydoc",
    "jupyterlab",
    "jupyterlab-server",
    "nbformat",
    "pycrdt",
    "pycrdt-store",
    "pycrdt-websocket",
    "pyzmq",
    "tornado",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lockfile", type=Path)
    parser.add_argument("--check-installed", action="store_true")
    args = parser.parse_args()
    with args.lockfile.open("rb") as lockfile:
        packages = tomllib.load(lockfile)["package"]

    for name in JUPYTER_PACKAGES:
        pins = {package.get("version") for package in packages if package["name"] == name}
        if len(pins) != 1 or None in pins:
            parser.error(f"Expected one locked version for {name} in {args.lockfile}")
        locked = pins.pop()
        if args.check_installed:
            try:
                installed = version(name)
            except PackageNotFoundError:
                parser.error(f"{name} is missing. The lockfile selects {locked}.")
            if installed != locked:
                parser.error(f"{name} is {installed}. The lockfile selects {locked}.")
        else:
            print(f"{name}=={locked}")


if __name__ == "__main__":
    main()
