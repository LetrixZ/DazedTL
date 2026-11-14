import { api } from "../../api/client";
import type { TranslationJob, TranslationState } from "../../api/contracts";
import type { useAction } from "../../state/useAction";
import { ActionBar } from "../../ui/ActionBar";
import { Button } from "../../ui/Button";
import { DialogBody, DialogHeader } from "../../ui/Dialog";
import { Message } from "../../ui/Feedback";
import { Modal } from "../../ui/Modal";

/** What cancelling covers: every Batch the run has at the provider, since a
    run can keep several in flight. */
function scope(run: TranslationJob) {
  const sent = run.batches.filter((batch) => batch.state !== "pending");
  const requests = sent.reduce((sum, batch) => sum + batch.requests, 0);
  if (!sent.length)
    return "Nothing has reached the provider yet, so cancelling ends the run before it submits.";
  return `This cancels ${sent.length.toLocaleString()} ${
    sent.length === 1 ? "Batch" : "Batches"
  } at the provider${
    requests
      ? `, ${requests.toLocaleString()} ${requests === 1 ? "request" : "requests"} in all`
      : ""
  }.`;
}

/**
 * The review before cancelling a Batch run's provider work, shared by
 * Progress and History: the provider may finish and bill what is already
 * running, and the run ends so a new estimate translates the rest.
 */
export function CancelBatchRunDialog({
  run,
  state,
  action,
  onClose,
}: {
  run: TranslationJob;
  state: TranslationState;
  action: ReturnType<typeof useAction>;
  onClose: () => void;
}) {
  return (
    <Modal
      label="Cancel this Batch run?"
      size="sm"
      dismissible={!action.busy}
      onDismiss={onClose}
    >
      <DialogHeader title="Cancel this Batch run?" />
      <DialogBody>
        <p>
          The provider stops unfinished requests where possible. Requests
          already running may finish and be billed, and cancellation can take
          several minutes.
        </p>
        <p>
          {scope(run)} Results already downloaded are kept, and nothing is sent
          again.
        </p>
        <p>Once the run ends, a new estimate translates the rest.</p>
      </DialogBody>
      <ActionBar
        feedback={
          <Message
            message={action.key === "cancel-batch" ? action.error : ""}
          />
        }
      >
        <Button disabled={action.busy} onClick={onClose}>
          Back
        </Button>
        <Button
          variant="danger"
          pending={action.busy && action.key === "cancel-batch"}
          onClick={() =>
            void action.run(
              async () => {
                await api.translation.stop(state.projectId, run.id, true);
                onClose();
              },
              "",
              "cancel-batch",
            )
          }
        >
          Cancel Batch run
        </Button>
      </ActionBar>
    </Modal>
  );
}
