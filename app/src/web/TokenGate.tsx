import { useEffect, useState, type ReactNode } from "react";
import { PageBody, PageHeader, PageLayout } from "../ui/PageLayout";
import { FieldRow } from "../ui/FieldRow";
import { Button } from "../ui/Button";
import { acceptToken, needsToken, onTokenRejected } from "./bridge";

/** Asks for the token Settings shows on the desktop before the app starts. */
export function TokenGate({ children }: { children: ReactNode }) {
  const [needed, setNeeded] = useState(needsToken);
  const [value, setValue] = useState("");
  // A token replaced on the desktop brings the gate back, so the browser is
  // never left failing every operation with no way to enter the current one.
  useEffect(() => onTokenRejected(() => setNeeded(true)), []);
  if (!needed) return <>{children}</>;
  const connect = () => {
    if (!value.trim()) return;
    acceptToken(value);
    setNeeded(false);
  };
  return (
    <PageLayout>
      <PageHeader
        title="Connect to DazedTL"
        description="This page drives DazedTL on the desktop computer that serves it."
      />
      <PageBody>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            connect();
          }}
        >
          <FieldRow
            id="remote-token"
            label="Remote access token"
            help="Settings > Remote access on the desktop computer shows it."
          >
            {(control) => (
              <input
                {...control}
                type="password"
                autoFocus
                autoComplete="off"
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            )}
          </FieldRow>
          <Button variant="primary" type="submit" disabled={!value.trim()}>
            Connect
          </Button>
        </form>
      </PageBody>
    </PageLayout>
  );
}
