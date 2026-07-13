#!/usr/bin/env python3
"""Safely acquire the shared Lab cache lock, then exec the guarded command."""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import sys
from pathlib import Path

LOCK_DIRECTORY = "locks"
LOCK_FILENAME = "publish-wrapper.lock"
LOCK_FAILURE = "Lab cache lock initialization failed"


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_root(path: Path, *, create: bool, normalize_mode: bool) -> int:
    if ".." in path.parts:
        raise ValueError
    absolute = Path(os.path.abspath(path))
    components = absolute.parts[1:]
    if not components:
        raise ValueError
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(absolute.anchor, flags)
    root_descriptor: int | None = None
    try:
        for part in components[:-1]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        final_component = components[-1]
        try:
            root_descriptor = os.open(final_component, flags, dir_fd=descriptor)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(final_component, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            root_descriptor = os.open(final_component, flags, dir_fd=descriptor)
        metadata = os.fstat(root_descriptor)
        current = os.stat(final_component, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or not _same_inode(metadata, current) or metadata.st_uid != os.getuid():
            raise ValueError
        if normalize_mode:
            os.fchmod(root_descriptor, 0o700)
            metadata = os.fstat(root_descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError
        os.close(descriptor)
        return root_descriptor
    except BaseException:
        if root_descriptor is not None:
            os.close(root_descriptor)
        os.close(descriptor)
        raise


def _open_lock_directory(root_descriptor: int, expected_device: int, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(LOCK_DIRECTORY, 0o700, dir_fd=root_descriptor)
        except FileExistsError:
            pass
    metadata = os.stat(LOCK_DIRECTORY, dir_fd=root_descriptor, follow_symlinks=False)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_dev != expected_device
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(LOCK_DIRECTORY, flags, dir_fd=root_descriptor)
    if not _same_inode(metadata, os.fstat(descriptor)):
        os.close(descriptor)
        raise ValueError
    return descriptor


def _open_lock_file(directory_descriptor: int, expected_device: int, *, create: bool) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(LOCK_FILENAME, flags, 0o600, dir_fd=directory_descriptor)
    metadata = os.fstat(descriptor)
    try:
        current = os.stat(LOCK_FILENAME, dir_fd=directory_descriptor, follow_symlinks=False)
    except BaseException:
        os.close(descriptor)
        raise
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not _same_inode(metadata, current)
        or metadata.st_uid != os.getuid()
        or metadata.st_dev != expected_device
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise ValueError
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--verify-held", action="store_true")
    parser.add_argument("--nonblocking", action="store_true")
    parser.add_argument("--busy-exit-code", type=int, default=75)
    parser.add_argument("--busy-message", default="Lab cache lock is already held")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = _parser().parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if (
        args.fd < 3
        or args.fd > 255
        or not 1 <= args.busy_exit_code <= 255
        or (args.verify_held and command)
        or (not args.verify_held and not command)
    ):
        print(LOCK_FAILURE, file=sys.stderr)
        return 2

    root_descriptor: int | None = None
    lock_directory_descriptor: int | None = None
    lock_descriptor: int | None = None
    inherited_lock_descriptor: int | None = None
    try:
        if args.verify_held:
            # Pin the caller's inherited open file description before opening
            # any helper-owned descriptors.  A closed forged fd must not become
            # valid merely because one of the opens below reuses its number.
            inherited_lock_descriptor = os.dup(args.fd)
        root_descriptor = _open_root(
            args.root,
            create=not args.verify_held,
            normalize_mode=not args.verify_held,
        )
        root_metadata = os.fstat(root_descriptor)
        lock_directory_descriptor = _open_lock_directory(
            root_descriptor,
            root_metadata.st_dev,
            create=not args.verify_held,
        )
        lock_descriptor = _open_lock_file(
            lock_directory_descriptor,
            root_metadata.st_dev,
            create=not args.verify_held,
        )
        if args.verify_held:
            if inherited_lock_descriptor is None:
                raise ValueError
            inherited_metadata = os.fstat(inherited_lock_descriptor)
            named_metadata = os.fstat(lock_descriptor)
            if not _same_inode(inherited_metadata, named_metadata):
                raise ValueError
            fcntl.flock(inherited_lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(lock_descriptor)
            lock_descriptor = None
            return 0
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if args.nonblocking else 0)
        try:
            fcntl.flock(lock_descriptor, operation)
        except BlockingIOError:
            print(args.busy_message, file=sys.stderr)
            return args.busy_exit_code

        if lock_descriptor != args.fd:
            os.dup2(lock_descriptor, args.fd, inheritable=True)
            os.close(lock_descriptor)
            lock_descriptor = args.fd
        os.set_inheritable(lock_descriptor, True)
        environment = dict(os.environ)
        environment["VERDIFY_CACHE_LOCK_HELD_FD"] = str(args.fd)
        os.execvpe(command[0], command, environment)  # noqa: S606 - argv-only lock-preserving exec
    except (OSError, ValueError):
        print(LOCK_FAILURE, file=sys.stderr)
        return 2
    finally:
        for descriptor in (
            inherited_lock_descriptor,
            lock_descriptor,
            lock_directory_descriptor,
            root_descriptor,
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
