#!/usr/bin/env python3
"""Run something inside another distribution, to build and try the packages there.

    python3 tools/distro.py debian:trixie-slim -- apt-get update
    make deb-test         install the Debian package in Debian and run the tests against it
    make rpm              build the RPM in Fedora, install it there and run the tests

No container engine and no root are needed: the distribution's official image is fetched from
Docker Hub as it is, unpacked under ~/.cache/mxoptions-build and entered through a user namespace
(unshare, with the sub-ids of /etc/subuid) and bubblewrap. The source tree is at /src inside.
What is installed there stays for the next run."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "mxoptions-build")
REGISTRY = "https://registry-1.docker.io/v2"
INDEX_TYPES = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"
IMAGE_TYPES = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
# Inside, the caller is root and the other users of the image exist too, which package managers need.
NAMESPACE = ["unshare", "--map-root-user", "--map-users=auto", "--map-groups=auto", "--"]


def _json(url, token=None, accept=None):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if accept:
        request.add_header("Accept", accept)
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def fetch(image):
    """Unpack ``image`` ("debian:trixie-slim") unless it is there already; returns its root directory."""
    name, _colon, tag = image.partition(":")
    root = os.path.join(CACHE, image.replace(":", "-").replace("/", "-"))
    if os.path.exists(os.path.join(root, ".unpacked")):
        return root
    repository = name if "/" in name else f"library/{name}"
    token = _json(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull")["token"]
    manifest = _json(f"{REGISTRY}/{repository}/manifests/{tag or 'latest'}", token, f"{INDEX_TYPES}, {IMAGE_TYPES}")
    if "manifests" in manifest:  # a list of images, one per architecture
        chosen = next(entry for entry in manifest["manifests"]
                      if entry.get("platform", {}).get("architecture") == "amd64" and entry["platform"].get("os") == "linux")
        manifest = _json(f"{REGISTRY}/{repository}/manifests/{chosen['digest']}", token, IMAGE_TYPES)
    subprocess.run(NAMESPACE + ["rm", "-rf", root], check=False)
    os.makedirs(root)
    for index, layer in enumerate(manifest["layers"]):
        archive = os.path.join(CACHE, f"{os.path.basename(root)}-layer{index}")
        print(f"fetching layer {index + 1} of {len(manifest['layers'])}: {layer['size'] / 1e6:.0f} MB", flush=True)
        subprocess.run(["curl", "-fL", "--retry", "5", "-C", "-", "-sS", "-H", f"Authorization: Bearer {token}", "-o", archive,
                        f"{REGISTRY}/{repository}/blobs/{layer['digest']}"], check=True)
        subprocess.run(NAMESPACE + ["tar", "-xf", archive, "-C", root, "--exclude=dev/*"], check=True)
        os.remove(archive)
    open(os.path.join(root, ".unpacked"), "w").close()
    return root


def run(image, command, check=True):
    """Run ``command`` (a list, or a string for sh) inside ``image``; the source tree is /src there."""
    root = fetch(image)
    if isinstance(command, str):
        command = ["sh", "-ec", command]
    inside = NAMESPACE + [
        "bwrap", "--bind", root, "/", "--proc", "/proc", "--dev", "/dev", "--perms", "1777", "--tmpfs", "/tmp", "--tmpfs", "/run",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf", "--bind", ROOT, "/src", "--chdir", "/src",
        "--clearenv", "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "HOME", "/root", "--setenv", "LANG", "C.UTF-8", "--setenv", "DEBIAN_FRONTEND", "noninteractive",
        "--setenv", "QT_QPA_PLATFORM", "offscreen", "--hostname", "mxoptions-build", "--unshare-uts", "--die-with-parent",
    ] + command
    return subprocess.run(inside, check=check).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("image", help='an image of Docker Hub, like "debian:trixie-slim" or "fedora:42"')
    parser.add_argument("command", nargs=argparse.REMAINDER, help="what to run inside (after --); a shell if nothing")
    args = parser.parse_args()
    for tool in ("unshare", "bwrap", "curl", "tar"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is needed and was not found")
    command = [part for part in args.command if part != "--"] or ["bash"]
    sys.exit(run(args.image, command, check=False))


if __name__ == "__main__":
    main()
