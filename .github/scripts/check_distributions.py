"""Check built distributions in clean environments outside the source tree."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
EXAMPLE = ROOT / "examples" / "orders_messy.csv"


def run(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def single(pattern: str) -> pathlib.Path:
    matches = sorted(DIST.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {pattern} in {DIST}, found {len(matches)}")
    return matches[0]


def check_contents(wheel: pathlib.Path, sdist: pathlib.Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        if "sift_lint/__init__.py" not in members or "sift_lint/cli.py" not in members:
            raise RuntimeError("Wheel is missing the Sift package")
        if any(name.startswith("sift/") for name in members):
            raise RuntimeError("Wheel contains the old import package")

    with tarfile.open(sdist, "r:gz") as archive:
        members = {name.split("/", 1)[-1] for name in archive.getnames()}
        for expected in ("sift_lint/__init__.py", "README.md", "LICENSE"):
            if expected not in members:
                raise RuntimeError(f"Source distribution is missing {expected}")


def smoke(artifact: pathlib.Path, workspace: pathlib.Path, label: str) -> None:
    env = workspace / label
    venv.EnvBuilder(with_pip=True).create(env)
    scripts = env / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    executable = scripts / ("sift-lint.exe" if os.name == "nt" else "sift-lint")

    run(str(python), "-m", "pip", "install", "--no-deps", str(artifact), cwd=workspace)
    run(
        str(python),
        "-c",
        (
            "from importlib.metadata import version; "
            "from pathlib import Path; "
            "import sift_lint, sys; "
            "assert sift_lint.__version__ == version('sift-lint'); "
            "assert not Path(sift_lint.__file__).resolve().is_relative_to(Path(sys.argv[1]))"
        ),
        str(ROOT),
        cwd=workspace,
    )
    run(str(executable), "--version", cwd=workspace)
    run(str(executable), "--help", cwd=workspace)
    run(
        str(executable),
        "check",
        str(EXAMPLE),
        "--no-config",
        "--fail-on",
        "none",
        cwd=workspace,
    )
    run(str(python), "-m", "pip", "check", cwd=workspace)


def main() -> None:
    wheel = single("*.whl")
    sdist = single("*.tar.gz")
    check_contents(wheel, sdist)
    run(sys.executable, "-m", "twine", "check", "--strict", str(wheel), str(sdist), cwd=ROOT)

    with tempfile.TemporaryDirectory(prefix="sift-package-") as directory:
        workspace = pathlib.Path(directory)
        smoke(wheel, workspace, "wheel")
        smoke(sdist, workspace, "sdist")


if __name__ == "__main__":
    main()
