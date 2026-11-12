import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ActionBar } from "./ActionBar";
import { Button } from "./Button";
import { DialogBody, DialogHeader } from "./Dialog";
import { FieldRow } from "./FieldRow";
import { Modal } from "./Modal";
import { PathInput } from "./PathInput";

/** What a path is chosen for, which says what to call it. */
type Kind = "folder" | "editor" | "open" | "save";

type Request = {
  kind: Kind;
  title: string;
  value: string;
  resolve: (path: string | null) => void;
};

type Ask = (request: Omit<Request, "resolve">) => Promise<string | null>;

const Asking = createContext<Ask | null>(null);

/**
 * A path on the computer running DazedTL. The desktop app opens its own
 * dialog; a browser has none, so it asks for the path here instead.
 */
export function PathChoiceProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const [value, setValue] = useState("");
  const ask = useCallback<Ask>(
    (next) =>
      new Promise<string | null>((resolve) => {
        setValue(next.value);
        setRequest({ ...next, resolve });
      }),
    [],
  );
  const finish = (path: string | null) => {
    request?.resolve(path);
    setRequest(null);
  };
  return (
    <Asking.Provider value={ask}>
      {children}
      {request && (
        <Modal label={request.title} size="sm" onDismiss={() => finish(null)}>
          <DialogHeader title={request.title} onClose={() => finish(null)} />
          <DialogBody>
            <FieldRow
              id="path-choice"
              label={request.kind === "folder" ? "Folder" : "File"}
              help="The full path on the computer running DazedTL."
            >
              {(control) => (
                <PathInput
                  {...control}
                  data-autofocus
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && value.trim())
                      finish(value.trim());
                  }}
                />
              )}
            </FieldRow>
          </DialogBody>
          <ActionBar feedback={null}>
            <Button onClick={() => finish(null)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!value.trim()}
              onClick={() => finish(value.trim())}
            >
              {request.kind === "save" ? "Save here" : "Use this path"}
            </Button>
          </ActionBar>
        </Modal>
      )}
    </Asking.Provider>
  );
}

export type PathChoice = {
  /** A folder, such as a game's. */
  folder(title: string): Promise<string | null>;
  /** An executable, such as a code editor. */
  editor(title: string): Promise<string | null>;
  /** An existing file, such as a translation file to import. */
  open(title: string): Promise<string | null>;
  /** Where to save a file the desktop names. */
  save(name: string): Promise<string | null>;
};

/**
 * The path choices a feature asks for. The desktop app answers with its own
 * dialogs; a browser asks for the full path, since the file lives on the
 * computer running DazedTL and not on the one the user sits at.
 */
export function usePathChoice(): PathChoice {
  const ask = useContext(Asking);
  return useMemo<PathChoice>(() => {
    if (!window.dazedtl.web)
      return {
        folder: () => window.dazedtl.chooseFolder(),
        editor: () => window.dazedtl.chooseEditor(),
        open: () => window.dazedtl.chooseTranslationFile(),
        save: (name) => window.dazedtl.saveTranslationFile(name),
      };
    if (!ask) throw new Error("Path choices need their provider.");
    return {
      folder: (title) => ask({ kind: "folder", title, value: "" }),
      editor: (title) => ask({ kind: "editor", title, value: "" }),
      open: (title) => ask({ kind: "open", title, value: "" }),
      save: (name) =>
        ask({ kind: "save", title: "Save the translation file", value: name }),
    };
  }, [ask]);
}
