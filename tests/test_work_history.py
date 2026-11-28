"""The .dazedtl work history holds what its allowlist names, leaves the
game's repository able to add its portable files, and keeps only edited
images, as Git LFS files."""

import json
import sqlite3
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dazedtl.compatibility import work_history


def git(folder, *arguments, **environment):
    return subprocess.run(
        ["git", "-C", str(folder), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env={**__import__("os").environ, **environment},
    ).stdout


class WorkHistoryTests(unittest.TestCase):
    def test_commits_the_allowlist_beside_the_game_repository(self):
        with TemporaryDirectory() as temporary:
            game = Path(temporary) / "game"
            work = game / ".dazedtl"
            for name, raw in {
                "data/Map001.json": b"{}",
                ".gitignore": b"/.dazedtl/*\n!/.dazedtl/glossary.txt\n",
                ".dazedtl/glossary.txt": b"term=word\n",
                ".dazedtl/plugin-work/state.json": b'{"version": 1}',
                ".dazedtl/ace/Game.rgss3a": b"original archive",
                ".dazedtl/plugin-work/state.json.tmp": b"half written",
                ".dazedtl/images/img/pictures/edited.png": b"edited",
                ".dazedtl/images/img/pictures/untouched.png": b"copy",
            }.items():
                (game / name).parent.mkdir(parents=True, exist_ok=True)
                (game / name).write_bytes(raw)
            inventory = work / "image_manager/guided/inventory.sqlite3"
            inventory.parent.mkdir(parents=True)
            with sqlite3.connect(inventory) as db:
                db.execute("CREATE TABLE assets (id TEXT, data TEXT)")
                for name, changed in (("edited", True), ("untouched", False)):
                    row = {
                        "editable": f".dazedtl/images/img/pictures/{name}.png",
                        "changed": changed,
                    }
                    db.execute(
                        "INSERT INTO assets VALUES (?, ?)", (name, json.dumps(row))
                    )
            db.close()
            git(game, "init", "-q")
            git(game, "add", "data", ".gitignore")
            git(
                game,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t.invalid",
                "commit",
                "-qm",
                "set up",
            )

            work_history.touch(game, "abc123", "Translate: Export")
            commit = work_history.commit(game)

            # A nested .git would make this add nothing, and the glossary
            # would leave the game's checkpoints.
            git(game, "add", ".dazedtl/glossary.txt")
            self.assertIn(
                ".dazedtl/glossary.txt", git(game, "diff", "--cached", "--name-only")
            )
            environment = {
                "GIT_DIR": str(work / "work.git"),
                "GIT_WORK_TREE": str(work),
            }
            held = set(git(work, "ls-files", **environment).split())
            lfs = work_history.lfs_installed(game)
            self.assertEqual(
                held,
                {".gitattributes", "glossary.txt", "plugin-work/state.json"}
                | ({"images/img/pictures/edited.png"} if lfs else set()),
            )
            message = git(work, "log", "-1", "--format=%B", commit, **environment)
            self.assertIn("Translate: Export", message)
            self.assertIn("DazedTL-Checkpoint: abc123", message)
            if lfs:
                self.assertIn("edited.png", git(work, "lfs", "ls-files", **environment))
            self.assertFalse(work_history.marked(game))
            self.assertIsNone(work_history.commit(game, "close"))


if __name__ == "__main__":
    unittest.main()
