"""Verify the actual npm tarball, not only the source manifest."""
import json
import os
import subprocess
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = {"package/package.json", "package/opencode/index.js", "package/opencode/bridge.js",
            "package/hooks/boyscout.py", "package/skills/boyscout-rule/SKILL.md", "package/LICENSE"}


def main():
    with tempfile.TemporaryDirectory() as folder:
        result = subprocess.run(["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", folder],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        package = json.loads(result.stdout)[0]
        with tarfile.open(os.path.join(folder, package["filename"]), "r:gz") as archive:
            names = set(archive.getnames())
            assert REQUIRED <= names, "missing package files: %s" % (REQUIRED - names)
            assert not any("/tests/" in n or "/.git" in n or "/node_modules/" in n for n in names), names
            manifest = json.load(archive.extractfile("package/package.json"))
            assert manifest["exports"] == "./opencode/index.js"
            assert not manifest.get("dependencies"), "adapter must have no runtime npm dependencies"
    print("PASS actual npm package includes the adapter, Python engine and shared skill")


if __name__ == "__main__":
    main()
