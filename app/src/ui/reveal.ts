/**
 * Whether a folder opens here. A browser cannot open a folder on the computer
 * running DazedTL, so the action copies its path instead, to paste where the
 * user needs it.
 */
export const opensFolders = () => !window.dazedtl.web;

/** What a folder action is called: it opens the folder, or copies its path. */
export function revealLabel(subject = ""): string {
  const name = subject ? ` ${subject}` : "";
  return opensFolders() ? `Open${name} folder` : `Copy${name} path`;
}

/** What it reports: the folder opened, or its path is on the clipboard. */
export function revealNotice(subject = ""): string {
  const name = subject ? ` ${subject}` : "";
  return opensFolders()
    ? `The${name} folder opened.`
    : `Copied the${name} path.`;
}
