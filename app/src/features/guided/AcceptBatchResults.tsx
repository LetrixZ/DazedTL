import type { BatchAcceptance } from "../../api/contracts";
import { api } from "../../api/client";
import type { useAction } from "../../state/useAction";
import { ActionBar } from "../../ui/ActionBar";
import { Button } from "../../ui/Button";
import { DialogBody, DialogHeader } from "../../ui/Dialog";
import { Message } from "../../ui/Feedback";
import { Modal } from "../../ui/Modal";

const requests = (count: number) =>
  `${count.toLocaleString()} ${count === 1 ? "request" : "requests"}`;

/** The requests accepting leaves out, each kind once. */
function leftOut(value: BatchAcceptance) {
  return [
    value.failed && `${requests(value.failed)} that failed`,
    value.pending &&
      `${requests(value.pending)} still at the provider in ${value.pendingBatches.toLocaleString()} ${
        value.pendingBatches === 1 ? "Batch" : "Batches"
      }`,
    value.unsent && `${requests(value.unsent)} not sent yet`,
  ].filter(Boolean) as string[];
}

/**
 * The review before accepting a Batch run's results so far: what its ended
 * Batches returned is saved, everything else keeps its original text for a
 * later run, and Batches still working are cancelled or abandoned.
 */
export function AcceptBatchResultsDialog({
  projectId,
  value,
  action,
  onClose,
}: {
  projectId: string;
  value: BatchAcceptance;
  action: ReturnType<typeof useAction>;
  onClose: () => void;
}) {
  const left = leftOut(value);
  return (
    <Modal
      label="Accept results so far?"
      size="sm"
      dismissible={!action.busy}
      onDismiss={onClose}
    >
      <DialogHeader title="Accept results so far?" />
      <DialogBody>
        <p>
          The {requests(value.returned)} the finished Batches translated are
          saved, and Apply writes them into the game.
        </p>
        <p>
          Left out: {left.join(", ")}. Their lines keep their original text, and
          Translate prepares a new estimate for them only.
        </p>
        {value.pendingBatches > 0 && (
          <p>
            {value.cancels
              ? "The unfinished Batches are cancelled. Requests already running may finish and be billed."
              : "OpenRouter can’t cancel Batches, so the unfinished ones keep running and may be billed, but their results are not used."}
          </p>
        )}
      </DialogBody>
      <ActionBar
        feedback={
          <Message
            message={action.key === "batch:accept" ? action.error : ""}
          />
        }
      >
        <Button disabled={action.busy} onClick={onClose}>
          Back to inspection
        </Button>
        <Button
          variant="primary"
          pending={action.busy && action.key === "batch:accept"}
          onClick={() =>
            void action.run(
              async () => {
                await api.guided.batchAccept(projectId, value.token);
                onClose();
              },
              "Saving the results so far.",
              "batch:accept",
            )
          }
        >
          Accept results
        </Button>
      </ActionBar>
    </Modal>
  );
}
