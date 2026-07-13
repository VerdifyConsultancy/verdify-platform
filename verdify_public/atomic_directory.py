"""Descriptor-bound directory promotion and candidate recovery helpers.

The kernel rename API still names the source directory; an open directory
descriptor alone cannot make ``renameat2`` operate on that inode.  Promotion
therefore has two explicit parts:

* the scanner keeps the exact candidate descriptor open through promotion;
* the parent and candidate must be owned by the current uid and not writable by
  group/other, while callers serialize writers with the publisher lock.

Within that ownership boundary every lookup is relative to the held parent,
and the candidate name/inode plus its no-link tree are revalidated immediately
before the exchange.  A post-scan pathname replacement is rejected before the
live name is touched.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
STALE_CANDIDATE_MAX = 4096
TREE_ENTRY_MAX = 500_000

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class PromotionResult:
    exchanged: bool
    old_live_removed: bool


def root_identity_from_stat(metadata: os.stat_result) -> str:
    material = f"public-output-root-v1:{metadata.st_dev}:{metadata.st_ino}".encode()
    return hashlib.sha256(material).hexdigest()


def root_identity(file_descriptor: int) -> str:
    return root_identity_from_stat(os.fstat(file_descriptor))


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _metadata_state(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_name(name: str, label: str) -> str:
    if name in {"", ".", ".."} or "/" in name or "\x00" in name:
        raise ValueError(f"invalid {label} name")
    return name


def _open_directory_path(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if ".." in path.parts:
        raise ValueError("directory traversal is forbidden")
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_owned_private_directory(
    descriptor: int,
    *,
    label: str,
    expected_path: Path | None = None,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise ValueError(f"{label} must be a current-user-owned directory without group/other write")
    if expected_path is not None:
        absolute = Path(os.path.abspath(expected_path))
        path_metadata = os.stat(absolute, follow_symlinks=False)
        if stat.S_ISLNK(path_metadata.st_mode) or not _same_inode(metadata, path_metadata):
            raise ValueError(f"{label} path changed")
        if Path(os.path.realpath(absolute)) != absolute:
            raise ValueError(f"{label} path is not canonical")
    return metadata


def _assert_named_directory(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    *,
    label: str,
) -> None:
    metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or not _same_inode(metadata, expected):
        raise ValueError(f"{label} path changed")


def tree_inventory(root_descriptor: int, expected_device: int) -> dict[str, tuple[int, ...]]:
    root_metadata = os.fstat(root_descriptor)
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_dev != expected_device
        or root_metadata.st_uid != os.getuid()
    ):
        raise ValueError("candidate root is unsafe")
    inventory = {".": _metadata_state(root_metadata)}
    pending = [(os.dup(root_descriptor), "")]
    entries_seen = 0
    expected_uid = os.getuid()
    try:
        while pending:
            descriptor, relative_directory = pending.pop()
            try:
                with os.scandir(descriptor) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > TREE_ENTRY_MAX or entry.name in {"", ".", ".."} or "/" in entry.name:
                        raise ValueError("candidate tree exceeds its entry bound")
                    metadata = entry.stat(follow_symlinks=False)
                    if metadata.st_dev != expected_device or metadata.st_uid != expected_uid:
                        raise ValueError("candidate tree ownership or device changed")
                    if stat.S_ISLNK(metadata.st_mode):
                        raise ValueError("candidate tree contains a symlink")
                    relative_path = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                    if stat.S_ISDIR(metadata.st_mode):
                        child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                        opened = os.fstat(child)
                        if not _same_inode(metadata, opened):
                            os.close(child)
                            raise ValueError("candidate directory changed during inventory")
                        inventory[relative_path] = _metadata_state(opened)
                        pending.append((child, relative_path))
                    elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise ValueError("candidate tree contains a special or hardlinked file")
                    else:
                        inventory[relative_path] = _metadata_state(metadata)
            finally:
                os.close(descriptor)
        return inventory
    finally:
        for descriptor, _relative_path in pending:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _tree_is_safe_fd(root_descriptor: int, expected_device: int) -> bool:
    try:
        tree_inventory(root_descriptor, expected_device)
    except (OSError, ValueError):
        return False
    return True


def _remove_tree_contents_fd(root_descriptor: int, expected_device: int) -> None:
    with os.scandir(root_descriptor) as iterator:
        entries = list(iterator)
    if len(entries) > TREE_ENTRY_MAX:
        raise ValueError("candidate tree exceeds removal limit")
    for entry in entries:
        metadata = entry.stat(follow_symlinks=False)
        if metadata.st_dev != expected_device or metadata.st_uid != os.getuid() or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("candidate changed during descriptor cleanup")
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=root_descriptor)
            try:
                if not _same_inode(metadata, os.fstat(child)):
                    raise ValueError("candidate directory changed during cleanup")
                _remove_tree_contents_fd(child, expected_device)
            finally:
                os.close(child)
            current = os.stat(entry.name, dir_fd=root_descriptor, follow_symlinks=False)
            if not _same_inode(metadata, current):
                raise ValueError("candidate directory changed before removal")
            os.rmdir(entry.name, dir_fd=root_descriptor)
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            current = os.stat(entry.name, dir_fd=root_descriptor, follow_symlinks=False)
            if not _same_inode(metadata, current) or current.st_nlink != 1:
                raise ValueError("candidate file changed before removal")
            os.unlink(entry.name, dir_fd=root_descriptor)
        else:
            raise ValueError("candidate contains an unsafe cleanup entry")


def _renameat2(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    flags: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError("atomic directory rename is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_parent,
        os.fsencode(source_name),
        destination_parent,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def promote_open_directory(
    parent_descriptor: int,
    staged_name: str,
    staged_descriptor: int,
    live_name: str,
    *,
    expected_identity: str,
    expected_parent_path: Path,
    expected_inventory: dict[str, tuple[int, ...]] | None = None,
    before_exchange: Callable[[], None] | None = None,
    after_exchange: Callable[[], None] | None = None,
) -> PromotionResult:
    """Promote the exact descriptor scanned by the caller within a private parent."""
    _safe_name(staged_name, "staged")
    _safe_name(live_name, "live")
    if staged_name == live_name:
        raise ValueError("promotion directories must be distinct")
    parent_metadata = _assert_owned_private_directory(
        parent_descriptor,
        label="promotion parent",
        expected_path=expected_parent_path,
    )
    staged_metadata = _assert_owned_private_directory(staged_descriptor, label="promotion candidate")
    if (
        staged_metadata.st_dev != parent_metadata.st_dev
        or root_identity_from_stat(staged_metadata) != expected_identity
    ):
        raise ValueError("promotion candidate does not match the scanned inode")
    _assert_named_directory(parent_descriptor, staged_name, staged_metadata, label="promotion candidate")
    bound_inventory = expected_inventory or tree_inventory(staged_descriptor, staged_metadata.st_dev)
    if tree_inventory(staged_descriptor, staged_metadata.st_dev) != bound_inventory:
        raise ValueError("promotion candidate does not match the scanned tree")

    # Cooperating publishers all take this descriptor lock in addition to the
    # outer publish/build locks.  The parent ownership/mode check above excludes
    # other uid and group/world writers from the namespace.
    fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
    live_descriptor: int | None = None
    exchanged = False
    try:
        try:
            live_descriptor = os.open(live_name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        except FileNotFoundError:
            live_descriptor = None
        if live_descriptor is not None:
            live_metadata = os.fstat(live_descriptor)
            if not stat.S_ISDIR(live_metadata.st_mode) or live_metadata.st_dev != staged_metadata.st_dev:
                raise ValueError("live directory is unsafe or on another filesystem")
            _assert_named_directory(parent_descriptor, live_name, live_metadata, label="live directory")

        if before_exchange is not None:
            before_exchange()

        # This is the last possible validation point before the named kernel
        # operation.  It catches deterministic post-scan swaps and hardlinks;
        # the private-parent + lock boundary excludes concurrent namespace
        # writers after this point.
        _assert_owned_private_directory(
            parent_descriptor,
            label="promotion parent",
            expected_path=expected_parent_path,
        )
        current_staged = os.fstat(staged_descriptor)
        if (
            not _same_inode(current_staged, staged_metadata)
            or root_identity_from_stat(current_staged) != expected_identity
        ):
            raise ValueError("scanned candidate descriptor changed")
        _assert_named_directory(parent_descriptor, staged_name, staged_metadata, label="promotion candidate")
        if tree_inventory(staged_descriptor, staged_metadata.st_dev) != bound_inventory:
            raise ValueError("promotion candidate changed after scan")
        if live_descriptor is None:
            _renameat2(parent_descriptor, staged_name, parent_descriptor, live_name, RENAME_NOREPLACE)
        else:
            _assert_named_directory(parent_descriptor, live_name, os.fstat(live_descriptor), label="live directory")
            _renameat2(parent_descriptor, staged_name, parent_descriptor, live_name, RENAME_EXCHANGE)
            exchanged = True

        _assert_named_directory(parent_descriptor, live_name, staged_metadata, label="promoted live directory")
        if after_exchange is not None:
            after_exchange()

        old_live_removed = live_descriptor is None
        if live_descriptor is not None:
            old_metadata = os.fstat(live_descriptor)
            _assert_named_directory(parent_descriptor, staged_name, old_metadata, label="retired live directory")
            try:
                _remove_tree_contents_fd(live_descriptor, old_metadata.st_dev)
                _assert_named_directory(parent_descriptor, staged_name, old_metadata, label="retired live directory")
                os.rmdir(staged_name, dir_fd=parent_descriptor)
                old_live_removed = True
            except (OSError, ValueError):
                # A SIGKILL or cleanup race may leave the old live tree under
                # the candidate name. Age/identity-gated startup recovery owns it.
                old_live_removed = False
        os.fsync(parent_descriptor)
        return PromotionResult(exchanged=exchanged, old_live_removed=old_live_removed)
    finally:
        if live_descriptor is not None:
            os.close(live_descriptor)
        fcntl.flock(parent_descriptor, fcntl.LOCK_UN)


def promote(
    staged: Path,
    live: Path,
    *,
    expected_identity: str | None = None,
    before_exchange: Callable[[], None] | None = None,
    after_exchange: Callable[[], None] | None = None,
) -> PromotionResult:
    staged = Path(os.path.abspath(staged))
    live = Path(os.path.abspath(live))
    if staged == live or staged.parent != live.parent:
        raise ValueError("promotion directories must be distinct siblings")
    parent_descriptor = _open_directory_path(staged.parent)
    staged_descriptor: int | None = None
    try:
        staged_descriptor = os.open(staged.name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        identity = expected_identity or root_identity(staged_descriptor)
        return promote_open_directory(
            parent_descriptor,
            staged.name,
            staged_descriptor,
            live.name,
            expected_identity=identity,
            expected_parent_path=staged.parent,
            before_exchange=before_exchange,
            after_exchange=after_exchange,
        )
    finally:
        if staged_descriptor is not None:
            os.close(staged_descriptor)
        os.close(parent_descriptor)


def discard_open_directory(
    parent_descriptor: int,
    candidate_name: str,
    candidate_descriptor: int,
    *,
    expected_identity: str,
    expected_parent_path: Path,
) -> bool:
    """Remove only the still-named inode held by the caller; never a replacement."""
    _safe_name(candidate_name, "candidate")
    _assert_owned_private_directory(
        parent_descriptor,
        label="candidate parent",
        expected_path=expected_parent_path,
    )
    metadata = _assert_owned_private_directory(candidate_descriptor, label="candidate")
    if root_identity_from_stat(metadata) != expected_identity:
        raise ValueError("candidate does not match expected identity")
    try:
        _assert_named_directory(parent_descriptor, candidate_name, metadata, label="candidate")
    except (FileNotFoundError, ValueError):
        return False
    if not _tree_is_safe_fd(candidate_descriptor, metadata.st_dev):
        return False
    _remove_tree_contents_fd(candidate_descriptor, metadata.st_dev)
    _assert_named_directory(parent_descriptor, candidate_name, metadata, label="candidate")
    os.rmdir(candidate_name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)
    return True


def discard_candidate(candidate: Path) -> bool:
    candidate = Path(os.path.abspath(candidate))
    parent_descriptor = _open_directory_path(candidate.parent)
    candidate_descriptor: int | None = None
    try:
        _assert_owned_private_directory(parent_descriptor, label="candidate parent", expected_path=candidate.parent)
        try:
            candidate_descriptor = os.open(candidate.name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        except FileNotFoundError:
            return False
        return discard_open_directory(
            parent_descriptor,
            candidate.name,
            candidate_descriptor,
            expected_identity=root_identity(candidate_descriptor),
            expected_parent_path=candidate.parent,
        )
    finally:
        if candidate_descriptor is not None:
            os.close(candidate_descriptor)
        os.close(parent_descriptor)


def cleanup_stale_candidates(live: Path, *, min_age_seconds: int) -> int:
    live = Path(os.path.abspath(live))
    parent_descriptor = _open_directory_path(live.parent)
    pattern = re.compile(rf"^\.{re.escape(live.name)}\.candidate\.[A-Za-z0-9]{{8}}$")
    now = time.time()
    removed = 0
    seen = 0
    try:
        parent_metadata = _assert_owned_private_directory(
            parent_descriptor,
            label="candidate parent",
            expected_path=live.parent,
        )
        with os.scandir(parent_descriptor) as iterator:
            names = sorted(entry.name for entry in iterator if pattern.fullmatch(entry.name))
        for name in names:
            seen += 1
            if seen > STALE_CANDIDATE_MAX:
                raise ValueError("too many stale candidate directories")
            try:
                metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_dev != parent_metadata.st_dev
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
                or now - metadata.st_mtime < min_age_seconds
            ):
                continue
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
            try:
                if not _same_inode(metadata, os.fstat(descriptor)) or not _tree_is_safe_fd(descriptor, metadata.st_dev):
                    continue
                _remove_tree_contents_fd(descriptor, metadata.st_dev)
                _assert_named_directory(parent_descriptor, name, metadata, label="stale candidate")
                os.rmdir(name, dir_fd=parent_descriptor)
                removed += 1
            finally:
                os.close(descriptor)
        if removed:
            os.fsync(parent_descriptor)
        return removed
    finally:
        os.close(parent_descriptor)
