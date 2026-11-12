import type {
  NotificationSound,
  RemoteState,
  UpdateState,
} from "../api/transport";

/**
 * The bridge a browser uses in place of the desktop app's preload. Every
 * operation goes to the install that serves this page; the clipboard, the
 * notifications and the file choices belong to the computer the user sits at,
 * so those are done here instead.
 */

const TOKEN_KEY = "dazedtl-remote-token";

let token = "";
let installed = false;
const stopped = new Set<(message: string) => void>();

/** The token in the address, which then leaves the address bar and history. */
function addressToken(): string {
  const hash = new URLSearchParams(location.hash.slice(1));
  const value = hash.get("t") ?? "";
  if (value)
    history.replaceState(null, "", location.pathname + location.search);
  return value;
}

/** Installs the browser bridge; false when the desktop app already did. */
export function installWebBridge(): boolean {
  if (window.dazedtl) return false;
  token = addressToken() || localStorage.getItem(TOKEN_KEY) || "";
  if (token) localStorage.setItem(TOKEN_KEY, token);
  window.dazedtl = bridge;
  installed = true;
  return true;
}

/** Whether this page still waits for the token Settings shows. */
export const needsToken = () => installed && !token;

export function acceptToken(value: string) {
  token = value.trim();
  localStorage.setItem(TOKEN_KEY, token);
}

/**
 * Where this page's operations live. A proxy may mount the install under a
 * path, and the page's own address is the only thing that says which.
 */
function base(): URL {
  const url = new URL(document.baseURI);
  if (!url.pathname.endsWith("/")) url.pathname += "/";
  return url;
}

/** One request to the install, with the token the browser holds. */
async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(new URL(path, base()), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    for (const handler of stopped)
      handler("The desktop app could not be reached.");
    throw new Error("The desktop app could not be reached.");
  }
  if (response.status === 401)
    throw new Error(
      "The remote access token was refused. Copy the current one from the desktop app's Settings.",
    );
  if (!response.ok) throw new Error("The desktop app could not answer.");
  return (await response.json()) as T;
}

/** An operation only the desktop can do, such as reading its diagnostics. */
async function native<T>(name: string, args: unknown[]): Promise<T> {
  const reply = await post<{
    ok: boolean;
    value?: T;
    error?: { message?: string };
  }>(`native/${name}`, { args });
  if (!reply.ok)
    throw new Error(reply.error?.message || "The operation could not finish.");
  return reply.value as T;
}

/** Writes the user's own clipboard, which needs a secure page. */
async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    field.readOnly = true;
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  }
}

async function copyText(text: string) {
  if (!(await copy(text)))
    throw new Error("The text could not be copied. Select it and copy it.");
}

/** A path on the desktop, typed where the desktop would show a dialog. */
function pickPath(message: string, value = ""): string | null {
  const answer = window.prompt(message, value);
  return answer?.trim() || null;
}

/** The bytes of a value the desktop sent as JSON. */
function bytes(value: unknown): Uint8Array {
  const data = (value as { data?: unknown })?.data;
  return new Uint8Array(Array.isArray(data) ? (data as number[]) : []);
}

function base64(data: Uint8Array): string {
  const chunks: string[] = [];
  const step = 0x8000;
  for (let index = 0; index < data.length; index += step)
    chunks.push(String.fromCharCode(...data.subarray(index, index + step)));
  return btoa(chunks.join(""));
}

/** A sound file the user picks here, which the desktop then keeps. */
function pickSound(): Promise<NotificationSound | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,.mp3,.wav,.ogg,.oga,.opus,.flac,.m4a,.aac";
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      void file
        .arrayBuffer()
        .then((buffer) =>
          resolve({ name: file.name, data: new Uint8Array(buffer) }),
        );
    });
    input.addEventListener("cancel", () => resolve(null));
    input.click();
  });
}

// Updates replace the desktop install, so a browser neither sees nor starts one.
const updates: UpdateState = {
  git: false,
  version: "",
  channel: "stable",
  status: "idle",
  progress: -1,
  latest: "",
  staged: "",
  revert: false,
  previous: "",
  skipped: "",
  checkedAt: "",
  error: "",
  outcome: null,
  notes: { releases: [], recent: 0 },
};

const bridge: Window["dazedtl"] = {
  web: true,
  // Paths read as the desktop writes them; this computer has no home folder
  // to shorten them to.
  home: "",
  call: (version, method, params) => post("rpc", { version, method, params }),
  ready: async () => {},
  copyText,
  copyDiagnostics: async () => {
    await copyText(await native<string>("copy-diagnostics", []));
  },
  copyProblemReport: async (context) => {
    await copyText(await native<string>("copy-problem-report", [context]));
  },
  copyFixTask: async (context) => {
    await copyText(await native<string>("copy-fix-task", [context]));
  },
  toolFeedback: (context, feedback) =>
    native<string>("tool-feedback", [context, feedback]),
  saveToolFeedback: async (name, text) => {
    const url = URL.createObjectURL(
      new Blob([text + "\n"], { type: "text/markdown" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = name || "tool-feedback.md";
    link.click();
    URL.revokeObjectURL(url);
    return link.download;
  },
  reportRendererError: async (failure) => {
    await native("renderer-error", [failure]).catch(() => undefined);
  },
  reloadInterface: async () => {
    location.reload();
  },
  chooseFolder: async () =>
    pickPath("Type the folder's path on the desktop computer."),
  chooseEditor: async () =>
    pickPath("Type the editor's path on the desktop computer."),
  chooseTranslationFile: async () =>
    pickPath("Type the translation file's path on the desktop computer."),
  saveTranslationFile: async (name) =>
    pickPath(
      "Type where to save the translation file on the desktop computer.",
      name,
    ),
  // A browser cannot open a folder on another computer, so the path lands on
  // the clipboard for the user to paste where it is needed.
  openFolder: async (kind, target) => {
    await copyText(await native<string>("folder-path", [kind, target]));
  },
  notify: async (message) => {
    if (!("Notification" in window))
      throw new Error("This browser does not show notifications.");
    if (Notification.permission === "default")
      await Notification.requestPermission();
    if (Notification.permission !== "granted")
      throw new Error("Allow notifications for this page to see them.");
    new Notification(message.title, { body: message.body });
  },
  phone: (message) => native("phone", [message]),
  guide: {
    read: async () => {
      const response = await fetch(new URL("guide", base()));
      if (!response.ok) throw new Error("The guide could not be read.");
      return response.text();
    },
    image: async (source) =>
      new URL(
        `guide/media/${encodeURIComponent(source.split("/").pop() ?? "")}`,
        base(),
      ).href,
    open: async (href) => {
      const url = await native<string>("guide-link", [href]);
      window.open(url, "_blank", "noopener,noreferrer");
    },
  },
  sound: {
    read: async () => {
      const value = await native<{ name: string; data: unknown } | null>(
        "sound-read",
        [],
      );
      return value ? { name: value.name, data: bytes(value.data) } : null;
    },
    choose: pickSound,
    save: async (sound) => {
      await native("sound-save", [
        { name: sound.name, data: base64(sound.data) },
      ]);
    },
    clear: async () => {
      await native("sound-clear", []);
    },
  },
  remote: {
    state: async (): Promise<RemoteState> => ({
      enabled: true,
      url: location.origin,
      path: "/",
      token: "",
      port: 0,
      error: "",
    }),
    enable: async () => {
      throw new Error("Remote access is set in the desktop app.");
    },
    regenerate: async () => {
      throw new Error("Remote access is set in the desktop app.");
    },
  },
  // The browser has no close handshake; drafts are saved as the page unloads.
  onClose: (handler) => {
    const listener = () => {
      void handler();
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  },
  onStopped: (handler) => {
    stopped.add(handler);
    return () => {
      stopped.delete(handler);
    };
  },
  updates: {
    state: async () => updates,
    check: async () => {
      throw new Error("Updates run in the desktop app.");
    },
    channel: async () => {
      throw new Error("Updates run in the desktop app.");
    },
    revert: async () => {
      throw new Error("Updates run in the desktop app.");
    },
    keep: async () => {
      throw new Error("Updates run in the desktop app.");
    },
    restart: async () => {
      throw new Error("Updates run in the desktop app.");
    },
    onState: () => () => {},
  },
};
