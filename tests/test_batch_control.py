"""Cancel and partial-result recovery must never destroy receipts or resubmit."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dazedtl.compatibility import batch_control, process_view, request_scope
from dazedtl.storage import write_json
from dazedtl.translation.files import read_json


class BatchControlTests(unittest.TestCase):
    def test_cancel_binds_provider_request_scope_and_retains_every_local_artifact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {
                "id": "paid",
                "provider": "openai",
                "key_name": "original-account",
                "custom_ids": {"one": "key-one"},
            }
            write_json(root / "log/batch_history.json", {"batches": [batch]})
            write_json(
                root / "log/batch_state.json",
                {"status": "submitted", "batches": [batch]},
            )
            write_json(
                root / "log/batch_requests.json",
                {"key-one": {"payload": '{"Line1":"薬"}', "params": {}}},
            )
            before = {
                path: path.read_bytes() for path in root.rglob("*") if path.is_file()
            }
            provider = Mock()
            provider.status.return_value = {"api_status": "in_progress"}
            provider.cancel.return_value = {"id": "paid", "status": "cancelling"}
            resolve = Mock(
                return_value={
                    "secret": "fixture",
                    "endpoint": "https://fixture.invalid",
                    "organization": "",
                }
            )
            with patch.object(
                batch_control, "TranslationProvider", return_value=provider
            ) as create:
                with self.assertRaises(ValueError):
                    batch_control.cancel(
                        root, "foreign", batch_control.binding(batch), resolve
                    )
                with self.assertRaises(ValueError):
                    batch_control.cancel(root, "paid", "changed-scope", resolve)
                create.assert_not_called()
                result = batch_control.cancel(
                    root, "paid", batch_control.binding(batch), resolve
                )
                self.assertTrue(result["requested"])
                resolve.assert_called_once_with(batch)
                provider.cancel.assert_called_once_with("paid")
                provider.client.close.assert_called_once()
                for status in ("completed", "cancelled", "cancelling"):
                    provider.status.return_value = {"api_status": status}
                    self.assertFalse(
                        batch_control.cancel(
                            root, "paid", batch_control.binding(batch), resolve
                        )["requested"]
                    )
                self.assertEqual(provider.cancel.call_count, 1)
            self.assertEqual(
                before,
                {path: path.read_bytes() for path in root.rglob("*") if path.is_file()},
            )
            provider.submit.assert_not_called()
            provider.live.assert_not_called()
            batch["provider"] = "openrouter"
            write_json(root / "log/batch_history.json", {"batches": [batch]})
            with patch.object(batch_control, "TranslationProvider") as create:
                with self.assertRaisesRegex(
                    ValueError, "does not expose Batch cancellation"
                ):
                    batch_control.cancel(
                        root, "paid", batch_control.binding(batch), resolve
                    )
                create.assert_not_called()

    def test_terminal_collection_keeps_paid_successes_and_releases_only_proven_unsuccessful_requests(
        self,
    ):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {
                "id": "paid",
                "provider": "openai",
                "custom_ids": {"one": "key-one", "two": "key-two"},
            }
            write_json(root / "log/batch_history.json", {"batches": [batch]})
            write_json(
                root / "log/batch_state.json",
                {
                    "status": "partially_submitted",
                    "batches": [batch],
                    "run_id": "original",
                },
            )
            queue = {
                key: {
                    "payload": '{"Line1":"' + text + '"}',
                    "params": {},
                    "dazedtl_sources": [key],
                    "dazedtl_file": "Items.json",
                }
                for key, text in [
                    ("key-one", "薬"),
                    ("key-two", "毒"),
                    ("unsent", "盾"),
                ]
            }
            write_json(root / "log/batch_requests.json", queue)
            frozen = {
                path: path.read_bytes() for path in root.rglob("*") if path.is_file()
            }
            provider = Mock()
            provider.status.return_value = {"api_status": "cancelling"}
            provider.collect_terminal.return_value = (
                {"key-one": {"text": '{"Line1":"Medicine"}'}},
                ["two"],
                {"input_tokens": 5, "output_tokens": 2},
            )
            resolve = lambda _: {
                "secret": "fixture",
                "endpoint": "https://fixture.invalid",
                "organization": "",
            }
            with patch.object(
                batch_control, "TranslationProvider", return_value=provider
            ):
                with self.assertRaisesRegex(ValueError, "still working"):
                    batch_control.collect(root, resolve)
                self.assertEqual(
                    frozen,
                    {
                        path: path.read_bytes()
                        for path in root.rglob("*")
                        if path.is_file()
                    },
                )
                provider.collect_terminal.assert_not_called()
                provider.status.return_value = {
                    "api_status": "cancelled",
                    "counts": {
                        "succeeded": 1,
                        "canceled": 1,
                        "processing": 0,
                        "errored": 0,
                        "expired": 0,
                    },
                }
                self.assertEqual(batch_control.collect(root, resolve)["received"], 1)
                self.assertEqual(
                    (root / "log/batch_requests.json").read_bytes(),
                    frozen[root / "log/batch_requests.json"],
                )
                self.assertEqual(
                    read_json(root / "log/batch_state.json")["status"], "fetched"
                )
                rows = list(
                    request_scope.requests(
                        root, {"mode": "batch", "dazedtl_submission_intent": True}
                    )
                )
                self.assertEqual(
                    [row["state"] for row in rows], ["received", "failed", "queued"]
                )
                summary = process_view.summary(root, {"mode": "batch"})
                self.assertTrue(summary["resultsCollected"])
                self.assertEqual(summary["batches"][0]["total"], 2)
                self.assertEqual(summary["batches"][0]["status"], "cancelled")
                # Repeated collection may re-read; it cannot rewrite a different
                # saved response or turn a download into another paid request.
                batch_control.collect(root, resolve)
                saved = (root / "log/batch_results.json").read_bytes()
                provider.collect_terminal.return_value = (
                    {"key-one": {"text": "Different paid response"}},
                    [],
                    {},
                )
                with self.assertRaisesRegex(ValueError, "conflict"):
                    batch_control.collect(root, resolve)
                self.assertEqual((root / "log/batch_results.json").read_bytes(), saved)
            provider.submit.assert_not_called()
            provider.live.assert_not_called()
            provider.cancel.assert_not_called()

    def test_collecting_again_reads_a_clarified_request_by_its_original_response(self):
        # A refused request's saved result is its clarification's reply, so a
        # later collection, such as accepting the run's results, reads the
        # provider's refusal against the receipts and does not stop on it
        # before it reaches the Batches that follow.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {
                "id": "paid",
                "provider": "openai",
                "custom_ids": {"one": "key-one"},
            }
            write_json(root / "log/batch_history.json", {"batches": [batch]})
            write_json(
                root / "log/batch_state.json",
                {"status": "partially_submitted", "batches": [batch]},
            )
            write_json(root / "log/batch_requests.json", {"key-one": {"params": {}}})
            refusal = {"text": "", "refusal": True}
            reply = {"text": '{"Line1":"Medicine"}'}
            write_json(root / "log/batch_results.json", {"key-one": reply})
            write_json(
                root / "log/dazedtl-clarified-results.json",
                {"original": {"key-one": refusal}, "results": {"key-one": reply}},
            )
            provider = Mock()
            provider.status.return_value = {"api_status": "completed", "counts": {}}
            provider.collect_terminal.return_value = ({"key-one": refusal}, [], {})
            resolve = lambda _: {
                "secret": "fixture",
                "endpoint": "https://fixture.invalid",
                "organization": "",
            }
            with patch.object(
                batch_control, "TranslationProvider", return_value=provider
            ):
                batch_control.collect(root, resolve)
                self.assertEqual(
                    read_json(root / "log/batch_results.json")["key-one"], reply
                )
                provider.collect_terminal.return_value = (
                    {"key-one": {"text": "Another response"}},
                    [],
                    {},
                )
                with self.assertRaisesRegex(ValueError, "conflict"):
                    batch_control.collect(root, resolve)

    def test_accepting_results_so_far_saves_ended_batches_and_abandons_the_rest(
        self,
    ):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batches = [
                {"id": "done", "provider": "openai", "custom_ids": {"a": "key-a"}},
                {"id": "busy", "provider": "openai", "custom_ids": {"b": "key-b"}},
            ]
            write_json(root / "log/batch_history.json", {"batches": batches})
            write_json(
                root / "log/batch_state.json",
                {"status": "partially_submitted", "batches": batches},
            )
            write_json(
                root / "log/batch_requests.json",
                {
                    key: {"payload": '{"Line1":"薬"}', "params": {}}
                    for key in ("key-a", "key-b", "unsent")
                },
            )
            provider = Mock()
            provider.status.side_effect = lambda identity: {
                "api_status": "completed" if identity == "done" else "in_progress",
                "counts": {"succeeded": 1},
            }
            provider.collect_terminal.return_value = (
                {"key-a": {"text": '{"Line1":"Medicine"}'}},
                [],
                {},
            )
            resolve = lambda _: {
                "secret": "fixture",
                "endpoint": "https://fixture.invalid",
                "organization": "",
            }
            with patch.object(
                batch_control, "TranslationProvider", return_value=provider
            ):
                with self.assertRaisesRegex(ValueError, "still working"):
                    batch_control.collect(root, resolve)
                self.assertEqual(
                    batch_control.collect(root, resolve, accept=True)["received"], 1
                )
            # The working Batch is cancelled once and never read; the unsent
            # request no longer holds the run back, and nothing is sent.
            provider.cancel.assert_called_once_with("busy")
            self.assertEqual(
                {call.args[0] for call in provider.collect_terminal.call_args_list},
                {"done"},
            )
            provider.submit.assert_not_called()
            self.assertTrue(process_view.accepted(root))
            batch_control.require_complete_submission(root)
            busy = read_json(root / "log/batch_history.json")["batches"][1]
            self.assertEqual(busy["api_status"], "abandoned")
            self.assertEqual(busy["request_counts"]["succeeded"], 0)
            self.assertEqual(
                busy["provider_errors"],
                [{"custom_id": "b", "message": batch_control.ABANDONED}],
            )
            self.assertEqual(
                read_json(root / "log/batch_results.json"),
                {"key-a": {"text": '{"Line1":"Medicine"}'}},
            )

    def test_a_failed_save_is_savable_only_when_the_provider_failed_the_rest(self):
        # Accepting a failed save keeps the missing requests' lines in their
        # source text, so it is offered only when each was sent and its
        # provider ended it with an error: never for a request never sent, a
        # paid result not downloaded, a complete run or one with no result.
        batch = {
            "id": "paid",
            "provider": "openrouter",
            "api_status": "completed",
            "custom_ids": {"one": "key", "two": "failed"},
            "provider_errors": [{"custom_id": "two", "message": "error"}],
        }
        cases = [
            ("provider failed the rest", {}, {}, 1),
            ("run complete", {"status": "complete"}, {}, 0),
            ("already accepted", {"dazedtl_batch_accepted": True}, {}, 0),
            ("every result present", {}, {"results": ["key", "failed"]}, 0),
            ("no result at all", {}, {"results": []}, 0),
            ("a paid result missing", {}, {"batch": {"provider_errors": []}}, 0),
            ("a request never sent", {}, {"queue": ["key", "failed", "new"]}, 0),
            ("collection not ended", {}, {"state": "submitted"}, 0),
        ]
        for name, job, change, expected in cases:
            with self.subTest(name), TemporaryDirectory() as directory:
                root = Path(directory)
                sent = {**batch, **change.get("batch", {})}
                write_json(root / "log/batch_history.json", {"batches": [sent]})
                write_json(
                    root / "log/batch_state.json",
                    {"status": change.get("state", "fetched"), "batch_ids": ["paid"]},
                )
                write_json(
                    root / "log/batch_requests.json",
                    {
                        key: {"payload": '{"Line1":"薬"}', "params": {}}
                        for key in change.get("queue", ["key", "failed"])
                    },
                )
                write_json(
                    root / "log/batch_results.json",
                    {key: {"text": "ok"} for key in change.get("results", ["key"])},
                )
                self.assertEqual(
                    process_view.provider_failed(root, {"status": "failed", **job}),
                    expected,
                )

    def test_accepting_refuses_until_an_ended_batch_returned_translations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {"id": "busy", "provider": "openrouter", "custom_ids": {"b": "k"}}
            write_json(root / "log/batch_history.json", {"batches": [batch]})
            write_json(
                root / "log/batch_state.json",
                {"status": "submitted", "batches": [batch]},
            )
            write_json(
                root / "log/batch_requests.json",
                {"k": {"payload": '{"Line1":"薬"}', "params": {}}},
            )
            frozen = {
                path: path.read_bytes() for path in root.rglob("*") if path.is_file()
            }
            provider = Mock()
            provider.status.return_value = {"api_status": "in_progress"}
            with (
                patch.object(
                    batch_control, "TranslationProvider", return_value=provider
                ),
                self.assertRaisesRegex(ValueError, "No finished Batch"),
            ):
                batch_control.collect(
                    root,
                    lambda _: {"secret": "", "endpoint": "", "organization": ""},
                    accept=True,
                )
            # OpenRouter cannot cancel, and nothing is saved.
            provider.cancel.assert_not_called()
            self.assertEqual(
                frozen,
                {path: path.read_bytes() for path in root.rglob("*") if path.is_file()},
            )
