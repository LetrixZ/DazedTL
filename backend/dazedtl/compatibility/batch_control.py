"""Project-bound Batch cancellation and collection; never create provider work."""

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager, nullcontext
from copy import deepcopy
from typing import Any

from dazedtl.storage import write_json
from dazedtl.translation.files import digest, live_path

from .batch_refusals import effective_results
from .process_view import (
    BATCH_ENDED,
    accepted,
    batch_results,
    evidence_root,
    queue,
    saved,
)
from .provider_responses import NO_REASON
from .translation import TranslationProvider

TERMINAL = BATCH_ENDED
PROVIDERS = {"openai", "anthropic", "gemini", "kimi", "openrouter"}


def can_cancel(provider):
    return provider in PROVIDERS - {"openrouter"}


# What an accepted run says of a request whose Batch it stopped waiting for.
ABANDONED = (
    "Not collected: this run's finished results were accepted before this Batch ended."
)


def unsent_requests(root):
    """A fetched marker covers downloaded jobs, not the entire prepared queue."""
    history = saved(evidence_root(root), "batch_history.json").get("batches", [])
    submitted = {
        key for batch in history for key in (batch.get("custom_ids") or {}).values()
    }
    return set(queue(root)) - submitted


def require_complete_submission(root):
    if accepted(root):
        return
    remaining = len(unsent_requests(root))
    if remaining:
        raise ValueError(
            f"{remaining:,} requests were not submitted. Collected responses are retained; "
            "saving the full run cannot start from an incomplete submission."
        )


def no_successful_results(root):
    batches = saved(evidence_root(root), "batch_history.json").get("batches", [])
    return (
        bool(batches)
        and not batch_results(root)
        and all(
            batch.get("api_status") in TERMINAL
            and batch.get("custom_ids")
            and type((batch.get("request_counts") or {}).get("succeeded")) is int
            and batch["request_counts"]["succeeded"] == 0
            for batch in batches
        )
    )


def receipt(root, identity):
    batch = next(
        (
            row
            for row in saved(evidence_root(root), "batch_history.json").get(
                "batches", []
            )
            if row.get("id") == identity
        ),
        None,
    )
    if batch is None:
        from .batch_refusals import records

        for record in records(evidence_root(root)):
            retry = next(
                (item for item in record["batches"] if item.get("id") == identity), None
            )
            if retry:
                original = receipt(root, record["original_id"])
                batch = {
                    **original,
                    "id": identity,
                    "custom_ids": {
                        item["custom_id"]: item["key"] for item in retry["items"]
                    },
                }
                break
    if (
        not batch
        or batch.get("provider") not in PROVIDERS
        or not isinstance(batch.get("custom_ids"), dict)
        or not batch["custom_ids"]
    ):
        raise ValueError("Choose a submitted Batch with retained request IDs.")
    return batch


def binding(batch):
    return digest(
        {
            key: batch.get(key)
            for key in (
                "id",
                "provider",
                "key_name",
                "endpoint",
                "organization",
                "custom_ids",
            )
        }
    )


@contextmanager
def connection(batch, resolve, *, receipt_root=None):
    """The saved connection a Batch already created is followed through,
    its refusal clarification included: the Batch's own key at the server
    it was created on (batch_providers.batch_client)."""
    from util.batch_providers import batch_client

    value = resolve(batch)
    provider = TranslationProvider(
        {**value, "protocol": batch["provider"], "mode": "batch"},
        value["secret"] or "not-needed",
        receipt_root=receipt_root,
    )
    provider.client = batch_client(provider.provider, provider.client)
    try:
        yield provider
    finally:
        close = getattr(provider.client, "close", None)
        if close:
            close()


def cancel(root, identity, expected, resolve):
    batch = receipt(root, identity)
    if not can_cancel(batch["provider"]):
        raise ValueError(
            "OpenRouter does not expose Batch cancellation. Submitted work continues at the provider."
        )
    if binding(batch) != expected:
        raise ValueError("This Batch changed. Review cancellation again.")
    with connection(batch, resolve) as provider:
        current = provider.status(identity)
        status = current["api_status"]
        if status in TERMINAL or status in {"cancelling", "canceling"}:
            return {
                "id": identity,
                "status": status,
                "requested": False,
                "counts": current.get("counts") or {},
            }
        result = provider.cancel(identity)
        return {**result, "requested": True}


def collect(
    root,
    resolve,
    *,
    commit: Callable[[], AbstractContextManager[Any]] = nullcontext,
    accept=False,
):
    """Retain terminal successes and prepare only a local consume pass.

    The caller excludes running workers. All provider jobs must be terminal,
    unless the user accepts the results so far (`accept`): then each job
    still working is cancelled where its provider allows it and abandoned
    (`ABANDONED`), and the run saves what the ended jobs returned. Mappings,
    queue and prior receipts remain intact, including unsent work.
    """
    evidence = evidence_root(root)
    state = deepcopy(saved(evidence, "batch_state.json"))
    history = deepcopy(saved(evidence, "batch_history.json"))
    batches = history.get("batches", [])
    if not batches or state.get("status") not in {
        "submitted",
        "partially_submitted",
        "fetched",
    }:
        raise ValueError(
            "This saved Batch needs its original submission records before collection."
        )
    original = digest({"state": state, "history": history, "queue": queue(root)})
    results = deepcopy(saved(evidence, "batch_results.json"))
    results = results.get("results", results)
    for batch in batches:
        if (
            batch.get("provider") not in PROVIDERS
            or not isinstance(batch.get("custom_ids"), dict)
            or not batch["custom_ids"]
        ):
            raise ValueError("A provider Batch is missing its request mapping.")
        if batch.get("api_status") == "abandoned":
            continue
        with connection(batch, resolve, receipt_root=evidence) as provider:
            status = provider.status(batch["id"])
            if status["api_status"] not in TERMINAL and accept:
                abandon(provider, batch, status)
                continue
            if status["api_status"] not in TERMINAL:
                raise ValueError(
                    "The provider is still working. Keep monitoring until every Batch finishes or cancels."
                )
            part, errors, usage = provider.collect_terminal(
                batch["id"], batch["custom_ids"]
            )
        if set(part) - set(batch["custom_ids"].values()):
            raise ValueError("Received results are outside this Batch’s saved scope.")
        try:
            # A refused request's saved result is its clarification's reply,
            # so the provider's original response is read against the
            # clarification receipts (batch_refusals) and not against it.
            results = effective_results(evidence, results, part)
        except ValueError:
            raise ValueError(
                "Received results conflict with a saved response. Existing results were retained."
            ) from None
        batch.update(
            api_status=status["api_status"],
            request_counts=status.get("counts") or {},
            usage=usage,
        )
        if type(batch["request_counts"].get("succeeded")) is int and batch[
            "request_counts"
        ]["succeeded"] == len(part):
            # Complete downloaded success coverage distinguishes canceled or
            # rejected requests from a missing paid response, with the
            # provider's reason where it gave one, such as a refused thinking
            # effort (process_view empty_batch).
            reasons = errors if isinstance(errors, dict) else {}
            previous = {
                error.get("custom_id"): error
                for error in batch.get("provider_errors") or []
            }
            batch["provider_errors"] = [
                {"custom_id": custom, "message": reasons[custom]}
                if reasons.get(custom)
                else previous.get(custom) or {"custom_id": custom, "message": NO_REASON}
                for custom, key in batch["custom_ids"].items()
                if key not in part
            ]
    if accept and not results:
        raise ValueError(
            "No finished Batch has returned translations yet. Keep waiting, or cancel the run."
        )
    with commit():
        if (
            digest(
                {
                    "state": saved(evidence, "batch_state.json"),
                    "history": saved(evidence, "batch_history.json"),
                    "queue": queue(root),
                }
            )
            != original
        ):
            raise ValueError(
                "The saved Batch changed during collection. Try again after the worker stops."
            )
        for name in ("batch_results.json", "batch_history.json", "batch_state.json"):
            live_path(evidence, "log/" + name, exists=False)
        # The final fetched marker is written last. A retry never creates requests.
        write_json(evidence / "log/batch_results.json", results)
        write_json(evidence / "log/batch_history.json", history)
        write_json(
            evidence / "log/batch_state.json",
            {
                **state,
                "status": "fetched",
                "batch_ids": [batch["id"] for batch in batches],
                **({"accepted": True} if accept else {}),
            },
        )
    return {"received": len(results)}


def abandon(provider, batch, status):
    """Stop waiting for a Batch still working when its run's results so far
    are accepted: cancel it where its provider allows it, since its results
    are never read, and record each of its requests as not collected."""
    if can_cancel(batch["provider"]):
        try:
            provider.cancel(batch["id"])
        except Exception as error:  # noqa: BLE001
            # The provider may have ended it meanwhile; the run no longer
            # reads it either way.
            batch["cancel_error"] = type(error).__name__
    total = len(batch["custom_ids"])
    # None of its results is read, so its requests count as canceled; what
    # the provider reported is kept beside them.
    batch.update(
        api_status="abandoned",
        abandoned_status=status["api_status"],
        abandoned_counts=status.get("counts") or {},
        request_counts={
            "total": total,
            "succeeded": 0,
            "errored": 0,
            "expired": 0,
            "canceled": total,
        },
        provider_errors=[
            {"custom_id": custom, "message": ABANDONED}
            for custom in batch["custom_ids"]
        ],
    )
