import "./styles/index.css";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import { ApplicationProvider } from "./app/ApplicationProvider";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { reportRendererFailure } from "./app/rendererErrors";
import { PathChoiceProvider } from "./ui/PathChoice";
import { installWebBridge } from "./web/bridge";
import { TokenGate } from "./web/TokenGate";

// A browser installs its own bridge before anything reads it; the desktop app
// already has the preload's.
installWebBridge();
window.addEventListener("error", (event) =>
  reportRendererFailure(event.error, "error"),
);
window.addEventListener("unhandledrejection", (event) =>
  reportRendererFailure(event.reason, "unhandledrejection"),
);
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary label="The interface">
    <TokenGate>
      <PathChoiceProvider>
        <ApplicationProvider>
          <App />
        </ApplicationProvider>
      </PathChoiceProvider>
    </TokenGate>
  </ErrorBoundary>,
);
