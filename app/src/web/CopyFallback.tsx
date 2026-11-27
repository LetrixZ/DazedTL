import { useEffect, useState } from "react";
import { ActionBar } from "../ui/ActionBar";
import { Button } from "../ui/Button";
import { DialogBody, DialogHeader } from "../ui/Dialog";
import { Modal } from "../ui/Modal";
import { onCopyFailed } from "./bridge";

/**
 * Shows a text the browser would not copy, so the user can copy it by hand or
 * with a fresh click, which the browser accepts.
 */
export function CopyFallback() {
  const [text, setText] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  useEffect(
    () =>
      onCopyFailed((value) => {
        setFeedback("");
        setText(value);
      }),
    [],
  );
  if (text === null) return null;
  const close = () => setText(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback("Copied.");
    } catch {
      setFeedback("Press Ctrl+C to copy the selected text.");
    }
  };
  return (
    <Modal label="Copy the text" size="md" onDismiss={close}>
      <DialogHeader
        title="Copy the text"
        description="The browser would not copy it for you. Copy it from here."
        onClose={close}
      />
      <DialogBody>
        <textarea
          readOnly
          data-autofocus
          aria-label="Text to copy"
          rows={16}
          value={text}
          onFocus={(event) => event.currentTarget.select()}
        />
      </DialogBody>
      <ActionBar feedback={feedback || null}>
        <Button onClick={close}>Close</Button>
        <Button variant="primary" onClick={() => void copy()}>
          Copy
        </Button>
      </ActionBar>
    </Modal>
  );
}
