import { useState } from "react";
import { useAction } from "../../state/useAction";
import { useRead } from "../../state/useRead";
import { Button } from "../../ui/Button";
import { CheckField, FieldRow } from "../../ui/FieldRow";
import { Message } from "../../ui/Feedback";
import { Notice } from "../../ui/Notice";
import { PageBody } from "../../ui/PageLayout";
import { PathInput } from "../../ui/PathInput";
import { SegmentedControl } from "../../ui/SegmentedControl";
import type { RemoteSettings, RemoteState } from "../../api/transport";

/** Drives this install from another computer's browser. */
export default function Remote() {
  const read = useRead("remote", () => window.dazedtl.remote.state());
  const [saved, setSaved] = useState<RemoteState | null>(null);
  const action = useAction();
  const current = saved ?? read.value ?? null;
  // Typed values wait here until the field is left, so a port is not applied
  // one digit at a time.
  const [port, setPort] = useState<string | null>(null);
  if (!current)
    return (
      <PageBody>
        <Message
          message={read.error ? "Remote access could not be read." : ""}
        />
      </PageBody>
    );
  const commit = async (change: Partial<RemoteSettings>, key: string) => {
    action.clear();
    const result = await action.run(
      () =>
        window.dazedtl.remote.configure({
          host: current.host,
          port: current.port,
          ...change,
        }),
      "",
      key,
    );
    if (result.ok) {
      setSaved(result.value);
      setPort(null);
    }
  };
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
  const [address, ...others] = current.urls;
  return (
    <PageBody>
      <CheckField
        id="remote-enabled"
        label="Allow remote access"
        help="A browser can drive this install, while the games stay on this computer. The server speaks plain HTTP, so reach it through a proxy or tunnel that adds HTTPS whenever it leaves this computer."
        checked={current.enabled}
        onChange={change}
      />
      {current.error && <Message message={current.error} />}
      {current.enabled && (
        <>
          <FieldRow
            id="remote-host"
            label="Reachable from"
            help="Other computers need a proxy or tunnel in front, or a network you trust."
          >
            {() => (
              <SegmentedControl
                label="Reachable from"
                value={current.host === "0.0.0.0" ? "network" : "local"}
                options={[
                  { value: "local", label: "This computer only" },
                  { value: "network", label: "My network" },
                ]}
                onChange={(value) =>
                  void commit(
                    { host: value === "network" ? "0.0.0.0" : "127.0.0.1" },
                    "host",
                  )
                }
              />
            )}
          </FieldRow>
          {current.host === "0.0.0.0" && (
            <Notice tone="warning">
              Other computers can reach this server. Put it behind a proxy or
              tunnel that adds HTTPS, or the token travels unencrypted.
            </Notice>
          )}
          <FieldRow
            id="remote-port"
            label="Port"
            help="The port the server listens on."
            error={action.key === "port" ? action.error : ""}
          >
            {(control) => (
              <input
                {...control}
                inputMode="numeric"
                value={port ?? String(current.port)}
                onChange={(event) => setPort(event.target.value)}
                onBlur={() => {
                  const next = Number(port);
                  if (port === null || next === current.port)
                    return setPort(null);
                  void commit({ port: next }, "port");
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
              />
            )}
          </FieldRow>
          <FieldRow
            id="remote-address"
            label="Address"
            help="Open it in the other computer's browser."
            note={others.length ? `Also ${others.join(", ")}` : undefined}
          >
            {(control) => (
              <div className="remote-field">
                <PathInput {...control} readOnly value={address ?? ""} />
                <Button
                  variant="quiet"
                  onClick={() => copy(address ?? "", "address")}
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
        </>
      )}
      <p className="feedback" role="status">
        {action.error || action.notice}
      </p>
    </PageBody>
  );
}
