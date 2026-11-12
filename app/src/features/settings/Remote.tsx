import { useState } from "react";
import { useAction } from "../../state/useAction";
import { useRead } from "../../state/useRead";
import { Button } from "../../ui/Button";
import { CheckField, FieldRow } from "../../ui/FieldRow";
import { Message } from "../../ui/Feedback";
import { PageBody } from "../../ui/PageLayout";
import { PathInput } from "../../ui/PathInput";
import type { RemoteState } from "../../api/transport";

/** Drives this install from another computer's browser. */
export default function Remote() {
  const read = useRead("remote", () => window.dazedtl.remote.state());
  const [saved, setSaved] = useState<RemoteState | null>(null);
  const action = useAction();
  const current = saved ?? read.value ?? null;
  if (!current)
    return (
      <PageBody>
        <Message
          message={read.error ? "Remote access could not be read." : ""}
        />
      </PageBody>
    );
  const change = (enabled: boolean) => {
    action.clear();
    void action.run(
      async () => setSaved(await window.dazedtl.remote.enable(enabled)),
      "",
      "enable",
    );
  };
  const regenerate = () => {
    action.clear();
    void action.run(
      async () => setSaved(await window.dazedtl.remote.regenerate()),
      "A new token replaces the old one at once.",
      "regenerate",
    );
  };
  const copy = (text: string, key: string) => {
    action.clear();
    void action.run(() => window.dazedtl.copyText(text), "Copied.", key);
  };
  const mount =
    current.path && current.path !== "/" ? ` --set-path=${current.path}` : "";
  const command = `tailscale serve --bg --https=443${mount} http://127.0.0.1:${current.port}`;
  return (
    <PageBody>
      <CheckField
        id="remote-enabled"
        label="Allow remote access"
        help="A browser on your own network can drive this install, while the games stay on this computer. The server listens only on this computer, and a proxy such as Tailscale provides the encrypted connection."
        checked={current.enabled}
        onChange={change}
      />
      {current.error && <Message message={current.error} />}
      {current.enabled && (
        <>
          <FieldRow
            id="remote-address"
            label="Address"
            help="Open it in the other computer's browser."
          >
            {(control) => (
              <div className="remote-field">
                <PathInput {...control} readOnly value={current.url} />
                <Button
                  variant="quiet"
                  onClick={() => copy(current.url, "address")}
                >
                  Copy
                </Button>
              </div>
            )}
          </FieldRow>
          <FieldRow
            id="remote-token"
            label="Token"
            help="Paste it once in the browser; a new token refuses the old one at once."
          >
            {(control) => (
              <div className="remote-field">
                <PathInput
                  {...control}
                  readOnly
                  type="password"
                  value={current.token}
                />
                <Button
                  variant="quiet"
                  onClick={() => copy(current.token, "token")}
                >
                  Copy
                </Button>
                <Button variant="quiet" onClick={regenerate}>
                  New token
                </Button>
              </div>
            )}
          </FieldRow>
          <FieldRow
            id="remote-command"
            label="Tailscale"
            help="Run this once on this computer, so the address above reaches it over HTTPS."
            note="Run it in PowerShell or Command Prompt: Git Bash rewrites a path argument."
          >
            {(control) => (
              <div className="remote-field">
                <PathInput {...control} readOnly value={command} />
                <Button
                  variant="quiet"
                  onClick={() => copy(command, "command")}
                >
                  Copy
                </Button>
              </div>
            )}
          </FieldRow>
        </>
      )}
      <p className="feedback" role="status">
        {action.error || action.notice}
      </p>
    </PageBody>
  );
}
