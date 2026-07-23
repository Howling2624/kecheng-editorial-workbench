from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable


FORMAT_NAME = "editorial-workbench-private-module"
FORMAT_VERSION = 1
MANIFEST_NAME = "private-module-manifest.json"
DEFAULT_BUNDLE_NAME = "editorial-private-config"
MAX_MEMBER_SIZE = 2 * 1024 * 1024 * 1024

PRIVATE_DIRECTORIES = (
    "modules/data_summary/稿件表数据/整合配置",
)

PRIVATE_FILES = (
    ".local/settings.json",
    ".local/publisher_accounts.json",
    "modules/ethics_review/config.json",
    "modules/ethics_review/api_key.txt",
    "modules/citation_review/config.json",
    "modules/citation_review/api_key.txt",
    "modules/data_summary/稿件表数据/数据同步工具/config.json",
)

PRIVATE_GLOBS = (
    "modules/data_summary/稿件表数据/数据同步工具/credentials*.json",
    "modules/data_summary/稿件表数据/数据同步工具/client_secret*.json",
    "modules/data_summary/稿件表数据/数据同步工具/token*.json",
    "modules/data_summary/稿件表数据/数据同步工具/token*.pickle",
)

EXCLUDED_PARTS = {
    "__pycache__",
    "exports",
    "import-backups",
}


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_root(value: str | None) -> Path:
    root = Path(value).resolve() if value else project_root_from_script()
    if not (root / "app.py").exists():
        raise ValueError(f"不是有效的工作台目录：{root}")
    return root


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def should_skip(path: Path, root: Path) -> bool:
    relative = path.resolve().relative_to(root.resolve())
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return True
    if path.suffix.lower() == ".kcbundle":
        return True
    return path.name.endswith((".tmp", ".bak"))


def iter_private_files(root: Path) -> list[Path]:
    found: set[Path] = set()

    for relative in PRIVATE_DIRECTORIES:
        base = root / relative
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not should_skip(path, root):
                found.add(path.resolve())

    for relative in PRIVATE_FILES:
        path = root / relative
        if path.is_file() and not should_skip(path, root):
            found.add(path.resolve())

    for pattern in PRIVATE_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and not should_skip(path, root):
                found.add(path.resolve())

    return sorted(found, key=lambda path: relative_posix(path, root))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_path(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or not member.parts:
        raise ValueError(f"模块中包含不安全路径：{name}")
    if member.parts[0] == MANIFEST_NAME:
        raise ValueError(f"模块成员路径无效：{name}")
    return member


def build_manifest(root: Path, files: Iterable[Path]) -> dict:
    entries = []
    for path in files:
        entries.append(
            {
                "path": relative_posix(path, root),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }


def default_export_path(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / ".local" / "exports" / f"{DEFAULT_BUNDLE_NAME}-{stamp}.kcbundle"


def export_bundle(root: Path, output: Path | None) -> Path:
    files = iter_private_files(root)
    if not files:
        raise ValueError("没有找到可导出的私有配置。请先配置工作台或 Google Drive 数据源。")

    destination = (output or default_export_path(root)).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root, files)

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.comment = (
                "CONFIDENTIAL: contains private editorial credentials and mappings. "
                "Do not upload to source control."
            ).encode("ascii")
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for path in files:
                archive.write(path, relative_posix(path, root))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    total = sum(item["size"] for item in manifest["files"])
    print(f"已导出：{destination}")
    print(f"文件数：{len(files)}，配置大小：{total / 1024:.1f} KB")
    print("注意：该模块包含登录凭据和内部映射，请仅通过可信介质传输，不要上传到 GitHub。")
    return destination


def load_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise ValueError("不是有效的私有配置模块：缺少清单。") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("私有配置模块清单损坏。") from exc
    if manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
        raise ValueError("私有配置模块格式或版本不受支持。")
    if not isinstance(manifest.get("files"), list):
        raise ValueError("私有配置模块清单不完整。")
    return manifest


def validate_archive(archive: zipfile.ZipFile, manifest: dict) -> list[dict]:
    expected = {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError("私有配置模块清单包含无效条目。")
        member = safe_member_path(str(item.get("path", "")))
        size = int(item.get("size", -1))
        digest = str(item.get("sha256", ""))
        if size < 0 or size > MAX_MEMBER_SIZE or len(digest) != 64:
            raise ValueError(f"文件清单无效：{member}")
        expected[member.as_posix()] = {
            "path": member.as_posix(),
            "size": size,
            "sha256": digest,
        }

    actual = {
        info.filename
        for info in archive.infolist()
        if not info.is_dir() and info.filename != MANIFEST_NAME
    }
    if actual != set(expected):
        raise ValueError("私有配置模块中的文件与清单不一致。")
    return [expected[name] for name in sorted(expected)]


def choose_bundle() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            title="选择科诚工作台私有配置模块",
            filetypes=[("工作台私有模块", "*.kcbundle"), ("所有文件", "*.*")],
        )
        root.destroy()
        return Path(selected).resolve() if selected else None
    except Exception:
        return None


def backup_existing(root: Path, paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / ".local" / "import-backups" / f"pre-import-{stamp}.zip"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in existing:
            archive.write(path, relative_posix(path, root))
    return backup


def import_bundle(root: Path, source: Path, assume_yes: bool, dry_run: bool) -> None:
    source = source.resolve()
    if not source.is_file():
        raise ValueError(f"找不到私有配置模块：{source}")

    with zipfile.ZipFile(source, "r") as archive:
        manifest = load_manifest(archive)
        entries = validate_archive(archive, manifest)
        targets = [root / PurePosixPath(item["path"]) for item in entries]
        existing = [path for path in targets if path.exists()]

        print(f"模块：{source.name}")
        print(f"文件数：{len(entries)}，将覆盖现有文件：{len(existing)}")
        if dry_run:
            print("预检通过，未写入文件。")
            return

        if existing and not assume_yes:
            answer = input("继续导入并自动备份现有文件？[y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("已取消。")
                return

        backup = backup_existing(root, existing)
        staging = root / ".local" / f"import-staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            for item in entries:
                member = item["path"]
                destination = staging / PurePosixPath(member)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(member, "r") as source_stream, destination.open("wb") as target_stream:
                    while True:
                        chunk = source_stream.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        target_stream.write(chunk)
                if destination.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"文件校验失败：{member}")

            for item in entries:
                relative = PurePosixPath(item["path"])
                staged = staging / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    print("私有配置模块已导入。请运行“同步并重建稿件数据.bat”获取最新数据。")
    if backup:
        print(f"原文件备份：{backup}")


def inspect_bundle(source: Path) -> None:
    with zipfile.ZipFile(source.resolve(), "r") as archive:
        manifest = load_manifest(archive)
        entries = validate_archive(archive, manifest)
    total = sum(item["size"] for item in entries)
    print("模块校验通过。")
    print(f"格式版本：{manifest['version']}")
    print(f"创建时间：{manifest['created_at']}")
    print(f"文件数：{len(entries)}，配置大小：{total / 1024:.1f} KB")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="编辑工作台私有配置模块导入导出工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="导出当前电脑的私有配置模块")
    export_parser.add_argument("--project-root", help="工作台目录，默认自动识别")
    export_parser.add_argument("--output", type=Path, help="输出 .kcbundle 路径")

    import_parser = subparsers.add_parser("import", help="导入私有配置模块")
    import_parser.add_argument("bundle", nargs="?", type=Path, help=".kcbundle 文件；Windows 下留空会弹出选择框")
    import_parser.add_argument("--project-root", help="工作台目录，默认自动识别")
    import_parser.add_argument("--yes", action="store_true", help="无需确认即可覆盖，覆盖前仍会自动备份")
    import_parser.add_argument("--dry-run", action="store_true", help="只校验，不写入")

    inspect_parser = subparsers.add_parser("inspect", help="校验私有配置模块")
    inspect_parser.add_argument("bundle", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "export":
            root = normalize_root(args.project_root)
            export_bundle(root, args.output)
        elif args.command == "import":
            root = normalize_root(args.project_root)
            bundle = args.bundle or choose_bundle()
            if bundle is None:
                raise ValueError("未选择私有配置模块。")
            import_bundle(root, bundle, args.yes, args.dry_run)
        elif args.command == "inspect":
            inspect_bundle(args.bundle)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
