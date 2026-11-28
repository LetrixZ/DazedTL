"""A game's DazedTL work, kept in a Git repository of its own.

The game's repository keeps the translation, one checkpoint per change
([checkpoint_commits](checkpoint_commits.py)), but ignores `.dazedtl/` except
its portable files, though Plugin files, Images, Guided, Manual and
Assistant-led keep their progress there. This repository gives that folder a
history of its own, to back up: its Git folder is `.dazedtl/work.git` and its
work tree `.dazedtl`, so the game's repository never meets a nested `.git`,
which would stop it adding the portable files of a game it hasn't committed
them in yet. Its `core.worktree` is relative, so a moved game keeps it, and
DazedTL names both folders on every Git call.

It holds what `wanted` lists, an allowlist, so nothing new joins it by
accident: DazedTL's records and guidance, and of the images only the
editable copies the Images inventory marks changed, never an unedited copy,
an original or an image anywhere else in the folder. What Git ignores stays
out too: the `.gitignore` files an assistant's tools leave, and the keys,
tool environments, saves, logs and caches Len's work block names
(len_translation `LOCAL_ONLY`), which the repository's own exclude file
holds. So do the Images inventory, an SQLite file a diff can't read, the
reference games, which name folders on this computer and copy other games'
data, and what Git already keeps elsewhere or can't use: the VX Ace
originals, backups, archived work, staging folders and tool feedback. Edited
images are Git LFS files from the first commit (`ATTRIBUTES`); without
git-lfs they are left out, and kept as committed, and a plain file over
`PLAIN_LIMIT` is left out too, each commit saying so in a trailer.

Every checkpoint marks its game (`touch`), which starts no process, so an
operation's worker marks too; the backend's `Committer` commits a marked
game's work in the background, the work of the game the user leaves for
another, and at close the open game's and the marked ones' within a few
seconds, since Electron ends the backend soon after. A commit follows a
checkpoint, which its `DazedTL-Checkpoint` trailer names, unless it was a
close's or a switch's. Commits to one game take turns through a lock in its
Git folder, and a mark made while one commits stays for the next.
"""

import json
import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from dazedtl import system
from dazedtl.storage import exclusive

WORK = ".dazedtl"
GIT_DIR = "work.git"
BRANCH = "main"
CHECKPOINT_TRAILER = "DazedTL-Checkpoint"
LEFT_OUT_TRAILER = "DazedTL-Left-Out"
# The edited images, as Git LFS files.
ATTRIBUTES = "images/** filter=lfs diff=lfs merge=lfs -text\n"
# Git sites refuse files of 100 MB; a plain file over half of that waits
# outside the history.
PLAIN_LIMIT = 50 * 1024 * 1024
# The files and folders of `.dazedtl` the history holds; `images` holds only
# edited copies (`_images`).
FILES = frozenset(
    {".gitattributes", "glossary.txt", "settings.json", "forge-config.json"}
)
FOLDERS = (
    "skills",
    "len-method",
    "guided",
    "manual",
    "plugin-work",
    "qa-receipts",
    "walkthrough",
    "image_translation_resources",
    "playtest",
    "image_manager",
)
# Files written beside a record while it is replaced.
_PASSING = (".tmp", ".lock", ".part", ".dazedtl-part", "-journal", "-wal", "-shm")
# Images, which the history holds only as edited copies under images/.
_IMAGES = (".png", ".png_", ".rpgmvp", ".bmp", ".jpg", ".jpeg", ".tlg", ".webp", ".gif")
# What asked for a commit no checkpoint marked, as its subject says.
REASONS = {
    "switch": "Work saved when another game opened",
    "close": "Work saved when DazedTL closed",
}
# How long closing may commit, well inside the time Electron gives the
# backend to end (app/electron/backend.cjs).
CLOSE_BUDGET = 5
_lfs: list[bool] = []


def _work(root):
    return Path(root) / WORK


def _mark(root):
    """The mark's path in the game's own Git folder, beside its project
    marker, or None without one."""
    from dazedtl.projects import location

    marker = location._marker(root)
    return None if marker is None else marker.with_name("work-pending.json")


def _image_work():
    """The Images work folder, relative to `.dazedtl`."""
    from dazedtl.images.service import WORK as IMAGES

    return IMAGES.removeprefix(WORK + "/")


def touch(root, checkpoint, subject):
    """Marks the game's work for the next commit, after the checkpoint
    `checkpoint` named `subject`; starts no process."""
    mark = _mark(root)
    if mark is None:
        return
    with exclusive(mark.with_suffix(".lock")):
        system.write_bytes(
            mark,
            json.dumps({"checkpoint": checkpoint, "subject": subject}).encode(),
        )
    if _committer is not None:
        _committer.wake()


def marked(root):
    mark = _mark(root)
    return bool(mark and mark.is_file())


def _walk(work, folder, left_out):
    """The regular files under `folder` of `work`, as work-relative paths,
    but images and `left_out`; a link is never followed."""
    top = work / folder
    if top.is_symlink() or not top.is_dir():
        return
    for directory, names, entries in os.walk(top):
        here = Path(directory)
        base = here.relative_to(work).as_posix()
        names[:] = [
            name
            for name in names
            if not (here / name).is_symlink() and f"{base}/{name}" not in left_out
        ]
        for name in entries:
            relative = f"{base}/{name}"
            if (
                relative not in left_out
                and not name.casefold().endswith(_PASSING + _IMAGES)
                and not (here / name).is_symlink()
            ):
                yield relative


def _images(root):
    """The work-relative edited copies the Images inventory names."""
    from dazedtl.images.inventory import edited_copies

    work = _work(root)
    found = set()
    for path in edited_copies(work / _image_work() / "inventory.sqlite3"):
        relative = path.removeprefix(WORK + "/")
        if relative.startswith("images/") and (work / relative).is_file():
            found.add(relative)
    return found


def _size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def wanted(root, lfs=True):
    """The work-relative files the history holds now, before Git's ignore
    rules, and what it leaves out of them, by reason; without `lfs` no image
    is wanted."""
    work = _work(root)
    images = _image_work()
    left_out = {f"{images}/inventory.sqlite3", f"{images}/stage", f"{images}/.lock"}
    files = {name for name in FILES if (work / name).is_file()}
    for folder in FOLDERS:
        files.update(_walk(work, folder, left_out))
    left = {}
    if lfs:
        files |= _images(root)
    elif (work / "images").is_dir():
        left["edited images"] = "git-lfs is not installed"
    large = {
        name
        for name in files
        if not name.startswith("images/") and _size(work / name) > PLAIN_LIMIT
    }
    if large:
        left[f"{len(large)} files over 50 MB"] = "Git sites refuse big files"
    return files - large, left


def _git(root, *args, input_text=None, check=True, literal=True):
    """Git in the work history; `literal` reads paths as names, as every
    command but check-ignore takes them."""
    from util.version_update.git_workflow import _run_git

    work = _work(root).resolve()
    return _run_git(
        work,
        *args,
        input_text=input_text,
        check=check,
        env={
            "GIT_DIR": str(work / GIT_DIR),
            "GIT_WORK_TREE": str(work),
            "GIT_LITERAL_PATHSPECS": "1" if literal else "0",
        },
        errors="surrogateescape",
        timeout=600,
    )


def _listed(names):
    return "".join(name + "\0" for name in sorted(names))


def lfs_installed(root):
    """Whether git-lfs runs, asked once per process."""
    if not _lfs:
        try:
            _lfs.append(_git(root, "lfs", "version", check=False).returncode == 0)
        except OSError, ValueError:
            _lfs.append(False)
    return _lfs[0]


def _exclude():
    from util.len_translation import LOCAL_ONLY

    return "".join(line + "\n" for line in (f"/{GIT_DIR}/", *LOCAL_ONLY)).encode()


def _ensure(root, lfs):
    """Makes the repository once, keeps its exclude file and attributes
    current, and sets Git LFS up in it once git-lfs runs."""
    work = _work(root)
    git = work / GIT_DIR
    if not (git / "HEAD").is_file():
        _git(root, "init", "-q", "-b", BRANCH)
        _git(root, "config", "core.worktree", "..")
    for path, content, mode in (
        (git / "info/exclude", _exclude(), 0o600),
        (work / ".gitattributes", ATTRIBUTES.encode(), 0o644),
    ):
        if not path.is_file() or system.read_bytes(path) != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            system.write_bytes(path, content, mode=mode)
    if lfs and b'[filter "lfs"]' not in system.read_bytes(git / "config"):
        _git(root, "lfs", "install", "--local")


def _unlock(git):
    """Takes away an index lock older than a minute, which a Git ended
    while it committed left: every commit holds this module's lock."""
    lock = git / "index.lock"
    try:
        if time.time() - lock.stat().st_mtime > 60:
            system.unlink(lock, missing_ok=True)
    except FileNotFoundError:
        pass


def _ignored(root, names):
    """Those of `names` Git's ignore rules leave out, tracked or not."""
    if not names:
        return set()
    found = _git(
        root,
        "check-ignore",
        "--no-index",
        "--stdin",
        "-z",
        input_text=_listed(names),
        check=False,
        literal=False,
    )
    if found.returncode not in (0, 1):
        raise ValueError(found.stderr.strip()[:300] or "git check-ignore failed")
    return set(filter(None, found.stdout.split("\0")))


def _add(root, names):
    """Stages `names`, leaving out one removed since it was listed, as an
    assistant's tools remove files while a commit runs."""
    for attempt in (1, 2):
        present = {name for name in names if (_work(root) / name).is_file()}
        if not present:
            return
        added = _git(
            root,
            "add",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            input_text=_listed(present),
            check=False,
        )
        if added.returncode == 0:
            return
        if attempt == 2 or "did not match" not in added.stderr:
            raise ValueError(added.stderr.strip()[:300] or "git add failed")


def commit(root, reason=None):
    """Commits the game's work as it is now, after the checkpoint its mark
    names, or for the `reason` in REASONS that asked; returns the commit,
    or None when nothing changed or the game has no work or Git."""
    from util.paths import game_git_set_up
    from util.version_update.git_workflow import _TOOL_EMAIL, _TOOL_NAME

    from dazedtl.compatibility.checkpoint_commits import SUBJECT_LIMIT

    root = Path(root)
    mark = _mark(root)
    if mark is None or not _work(root).is_dir() or not game_git_set_up(root):
        return None
    git = _work(root) / GIT_DIR
    with exclusive(mark.with_name("work.lock")):
        try:
            seen = system.read_bytes(mark)
            point = json.loads(seen)
        except FileNotFoundError, ValueError:
            seen, point = None, {}
        lfs = lfs_installed(root)
        _ensure(root, lfs)
        _unlock(git)
        want, left = wanted(root, lfs)
        want -= _ignored(root, want)
        tracked = set(filter(None, _git(root, "ls-files", "-z").stdout.split("\0")))
        # Without git-lfs the images committed before stay as they are.
        drop = {
            name for name in tracked - want if lfs or not name.startswith("images/")
        }
        if drop:
            _git(
                root,
                "rm",
                "--cached",
                "-q",
                "--ignore-unmatch",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                input_text=_listed(drop),
            )
        _add(root, want)
        subject = point.get("subject") or REASONS.get(reason or "", "Work saved")
        trailers = [
            *(
                [f"{CHECKPOINT_TRAILER}: {point['checkpoint']}"]
                if point.get("checkpoint")
                else []
            ),
            *(
                f"{LEFT_OUT_TRAILER}: {what} ({why})"
                for what, why in sorted(left.items())
            ),
        ]
        text = subject[:SUBJECT_LIMIT] + (
            "\n\n" + "\n".join(trailers) if trailers else ""
        )
        made = _git(
            root,
            "-c",
            f"user.name={_TOOL_NAME}",
            "-c",
            f"user.email={_TOOL_EMAIL}",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            text,
            check=False,
        )
        if made.returncode not in (0, 1) or (
            made.returncode == 1 and "nothing" not in made.stdout + made.stderr
        ):
            raise ValueError(
                "The game's work could not be committed: "
                + (made.stderr or made.stdout).strip()[:300]
            )
        with exclusive(mark.with_suffix(".lock")):
            try:
                if system.read_bytes(mark) == seen:
                    system.unlink(mark, missing_ok=True)
            except FileNotFoundError:
                pass
        if made.returncode != 0:
            return None
        return _git(root, "rev-parse", "HEAD").stdout.strip()


class Committer:
    """Commits marked games' work in the background, in the backend: each
    `interval`, when a checkpoint in this process marks one, and the game
    the user leaves for another (`open`); `close` commits what is left
    within CLOSE_BUDGET."""

    def __init__(
        self,
        roots: Callable[[], Iterable[str]],
        failure: Callable[[BaseException], None],
        interval=15,
        commit=commit,
    ):
        self.roots = roots
        self.failure = failure
        self.interval = interval
        self.commit = commit
        self.requested: dict[str, str] = {}
        self.current: str | None = None
        self.event = threading.Event()
        self.stopping = False
        self.failed: dict[str, str] = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(
            target=self._run, name="work-history", daemon=True
        )

    def start(self):
        global _committer
        _committer = self
        self.thread.start()

    def wake(self):
        self.event.set()

    def open(self, root):
        """Notes the game open now, which every snapshot reports; once it is
        another game than before, asks for the work of the one it left."""
        root = str(Path(root).resolve())
        with self.lock:
            left, self.current = self.current, root
            if left is None or left == root:
                return
            self.requested[left] = "switch"
        self.wake()

    def _run(self):
        while not self.stopping:
            self.event.wait(self.interval)
            self.event.clear()
            if not self.stopping:
                self._sweep()

    def _sweep(self, closing=False, deadline=None):
        with self.lock:
            asked = dict(self.requested)
            self.requested.clear()
            if closing and self.current:
                asked.setdefault(self.current, "close")
        try:
            marked_roots = [r for r in self.roots() if marked(r)]
        except Exception as error:  # noqa: BLE001 - the next sweep tries again
            self.failure(error)
            marked_roots = []
        for root in dict.fromkeys([*asked, *marked_roots]):
            if deadline is not None and time.monotonic() > deadline:
                return
            try:
                self.commit(root, asked.get(root))
                self.failed.pop(root, None)
            except Exception as error:  # noqa: BLE001 - the mark stays for the next sweep
                # One report per game until it commits again.
                if self.failed.get(root) != str(error):
                    self.failed[root] = str(error)
                    self.failure(error)

    def close(self, budget=CLOSE_BUDGET):
        """Commits the open game's work and the marked games' until
        `budget` seconds have passed; what is left waits for the next
        session, its marks kept."""
        global _committer
        deadline = time.monotonic() + budget
        self.stopping = True
        self.event.set()
        if self.thread.is_alive():
            self.thread.join(budget)
        _committer = None
        left = deadline - time.monotonic()
        if left <= 0:
            return
        done = threading.Thread(target=self._sweep, args=(True, deadline), daemon=True)
        done.start()
        done.join(left)


_committer: Committer | None = None
