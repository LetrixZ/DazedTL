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
                # Keys, tool environments, originals and reference games stay out.
                ".dazedtl/len-method/work/.env": b"KEY=secret",
                ".dazedtl/len-method/work/node_modules/x/index.js": b"",
                ".dazedtl/len-method/work/notes/.gitignore": b"draft.md\n",
                ".dazedtl/len-method/work/notes/draft.md": b"draft",
                ".dazedtl/len-method/work/notes/plan.md": b"plan",
                ".dazedtl/image_manager/guided/originals/img/pictures/edited.png": b"original",
                ".dazedtl/reference-games.json": b'{"games": ["/home/me/other"]}',
            }.items():
                (game / name).parent.mkdir(parents=True, exist_ok=True)
                (game / name).write_bytes(raw)
            inventory = work / "image_manager/guided/inventory.sqlite3"
            inventory.parent.mkdir(parents=True, exist_ok=True)
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
                {
                    ".gitattributes",
                    "glossary.txt",
                    "plugin-work/state.json",
                    "len-method/work/notes/.gitignore",
                    "len-method/work/notes/plan.md",
                }
                | ({"images/img/pictures/edited.png"} if lfs else set()),
            )
            message = git(work, "log", "-1", "--format=%B", commit, **environment)
            self.assertIn("Translate: Export", message)
            self.assertIn("DazedTL-Checkpoint: abc123", message)
            if lfs:
                self.assertIn("edited.png", git(work, "lfs", "ls-files", **environment))
            self.assertFalse(work_history.marked(game))
            self.assertIsNone(work_history.commit(game, "close"))

    def test_a_switch_asks_once_and_closing_stays_within_its_budget(self):
        asked = []
        committer = work_history.Committer(
            list, lambda error: None, commit=lambda root, why: asked.append(why)
        )
        # Every snapshot reports the open game; only leaving it asks.
        for root in ("a", "a", "b", "b", "b"):
            committer.open(root)
        committer._sweep()
        self.assertEqual(asked, ["switch"])

        def slow(root, why):
            asked.append(why)
            __import__("time").sleep(0.3)

        committer.commit = slow
        started = __import__("time").monotonic()
        committer.close(budget=0.2)
        self.assertLess(__import__("time").monotonic() - started, 0.5)
        self.assertEqual(asked, ["switch", "close"])


if __name__ == "__main__":
    unittest.main()
