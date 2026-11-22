import { useState } from "react";
import { api } from "../../api/client";
import type { Project } from "../../api/contracts";
import { useApplication } from "../../app/ApplicationProvider";
import { useAction } from "../../state/useAction";
import { ActionBar } from "../../ui/ActionBar";
import { Button } from "../../ui/Button";
import { DialogBody, DialogHeader } from "../../ui/Dialog";
import { FieldRow } from "../../ui/FieldRow";
import { Modal } from "../../ui/Modal";
import { PathText } from "../../ui/PathText";

/**
 * Renames the project as the app lists it, such as a game opened from a
 * folder called "translation" that reads like every other one. The game's
 * folder keeps its own name, so nothing in the game or its work changes;
 * the Project page shows the folder so a game whose folder says nothing is
 * still told apart from the one beside it.
 */
export function RenameProject({
  project,
  disabled,
}: {
  project: Project;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="link" disabled={disabled} onClick={() => setOpen(true)}>
        Rename
      </Button>
      {open && <RenameDialog project={project} close={() => setOpen(false)} />}
    </>
  );
}

function RenameDialog({
  project,
  close,
}: {
  project: Project;
  close: () => void;
}) {
  const application = useApplication();
  const action = useAction({ observer: application, after: close });
  const [name, setName] = useState(project.name);
  return (
    <Modal
      label="Rename project"
      size="sm"
      dismissible={!action.busy}
      onDismiss={close}
    >
      <DialogHeader
        title="Rename project"
        description={<PathText path={project.source} />}
      />
      <DialogBody>
        <FieldRow
          id="project-name"
          label="Name"
          help="Shows wherever the app lists this project, such as the Switch project menu. The game's folder keeps its own name."
          // The saved name is checked where it is kept (projects/store.py).
          error={action.error}
        >
          {(control) => (
            <input
              {...control}
              type="text"
              value={name}
              data-autofocus
              onChange={(event) => {
                setName(event.target.value);
                action.clear();
              }}
            />
          )}
        </FieldRow>
      </DialogBody>
      <ActionBar feedback={null}>
        <Button disabled={action.busy} onClick={close}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              await api.rename(project.id, name);
            })
          }
        >
          Save
        </Button>
      </ActionBar>
    </Modal>
  );
}
