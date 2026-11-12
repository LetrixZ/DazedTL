import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

const { Remote } = createRequire(import.meta.url)("../app/electron/remote.cjs");

/** An install with the built interface and the guide the server reads. */
function install() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dazedtl-remote-"));
  fs.mkdirSync(path.join(root, "app/dist"), { recursive: true });
  fs.mkdirSync(path.join(root, "docs/media"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "app/dist/index.html"),
    [
      "<!doctype html><html><head>",
      '<meta http-equiv="Content-Security-Policy"',
      " content=\"default-src 'self'; connect-src 'none'\">",
      '</head><body><script type="module" src="./assets/app.js"></script>',
      "</body></html>",
    ].join(""),
  );
  fs.writeFileSync(path.join(root, "app/dist/app.js"), "console.log(1);");
  fs.writeFileSync(path.join(root, "docs/user-guide.md"), "# Guide");
  fs.writeFileSync(
    path.join(root, "docs/media/shot.webp"),
    Buffer.from([1, 2]),
  );
  fs.writeFileSync(path.join(root, "secret.txt"), "outside the interface");
  return root;
}

/** A server with a stand-in backend, on a free port. */
async function serve() {
  const root = install();
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "dazedtl-profile-"));
  process.env.DAZEDTL_REMOTE_PORT = "0";
  const calls: { version: string; method: string; params: unknown }[] = [];
  const remote = new Remote({
    root,
    profile,
    diagnostics: { record() {}, failure() {} },
    call: async (version: string, method: string, params: unknown) => {
      calls.push({ version, method, params });
      return { version, ok: true, value: { method } };
    },
    native: async (name: string, args: unknown[]) => {
      if (name === "refuse") throw new Error("The desktop refused.");
      return { name, args };
    },
  });
  delete process.env.DAZEDTL_REMOTE_PORT;
  remote.enable(true);
  await new Promise((resolve) => remote.server.once("listening", resolve));
  return { remote, calls, port: remote.port as number };
}

function request(
  port: number,
  options: {
    method?: string;
    path?: string;
    headers?: Record<string, string>;
    body?: string;
  } = {},
) {
  return new Promise<{
    status: number;
    headers: http.IncomingHttpHeaders;
    text: string;
  }>((resolve, reject) => {
    const call = http.request(
      {
        host: "127.0.0.1",
        port,
        method: options.method ?? "GET",
        path: options.path ?? "/",
        headers: options.headers,
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode ?? 0,
            headers: response.headers,
            text: Buffer.concat(chunks).toString("utf8"),
          }),
        );
      },
    );
    call.on("error", reject);
    if (options.body) call.write(options.body);
    call.end();
  });
}

test("the interface is served without the build's own policy, which would block it", async (t) => {
  const { remote, port } = await serve();
  t.after(() => remote.close());
  const page = await request(port);
  assert.equal(page.status, 200);
  assert.match(String(page.headers["content-type"]), /text\/html/);
  // The build's meta tag would still say connect-src 'none', and the stricter
  // of the two applies, so it must not reach the browser.
  assert.doesNotMatch(page.text, /http-equiv="Content-Security-Policy"/);
  assert.match(
    String(page.headers["content-security-policy"]),
    /connect-src 'self'/,
  );
  // Without a proxy the page names its assets as the build wrote them.
  assert.match(page.text, /src="\.\/assets\/app\.js"/);
  const script = await request(port, { path: "/app.js" });
  assert.equal(script.status, 200);
  assert.match(String(script.headers["content-type"]), /javascript/);
  const guide = await request(port, { path: "/guide" });
  assert.equal(guide.text, "# Guide");
  const image = await request(port, { path: "/guide/media/shot.webp" });
  assert.equal(image.status, 200);
  assert.equal(image.headers["content-type"], "image/webp");
});

test("a path that leads out of the build is refused", async (t) => {
  const { remote, port } = await serve();
  t.after(() => remote.close());
  for (const target of [
    "/../secret.txt",
    "/..%2Fsecret.txt",
    "/%2e%2e/secret.txt",
    "/app/dist/../../secret.txt",
  ]) {
    const reply = await request(port, { path: target });
    assert.equal(reply.status, 404, target);
    assert.doesNotMatch(reply.text, /outside the interface/, target);
  }
});

test("an operation needs the token, and another origin's request is refused", async (t) => {
  const { remote, calls, port } = await serve();
  t.after(() => remote.close());
  const body = JSON.stringify({ version: "v1", method: "workspace_snapshot" });
  const json = { "Content-Type": "application/json" };
  const missing = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: json,
    body,
  });
  assert.equal(missing.status, 401);
  const wrong = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: { ...json, Authorization: "Bearer nope" },
    body,
  });
  assert.equal(wrong.status, 401);
  // A page of another origin names itself, and cannot have read the token.
  const foreign = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: {
      ...json,
      Authorization: `Bearer ${remote.state.token}`,
      Origin: "https://elsewhere.example",
    },
    body,
  });
  assert.equal(foreign.status, 403);
  assert.equal(calls.length, 0);
  const allowed = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: {
      ...json,
      Authorization: `Bearer ${remote.state.token}`,
      Origin: `http://127.0.0.1:${port}`,
    },
    body,
  });
  assert.equal(allowed.status, 200);
  assert.deepEqual(JSON.parse(allowed.text), {
    version: "v1",
    ok: true,
    value: { method: "workspace_snapshot" },
  });
  assert.deepEqual(calls, [
    { version: "v1", method: "workspace_snapshot", params: undefined },
  ]);
});

test("a desktop operation answers with its value or its own message", async (t) => {
  const { remote, port } = await serve();
  t.after(() => remote.close());
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${remote.state.token}`,
  };
  const value = await request(port, {
    method: "POST",
    path: "/native/copy-diagnostics",
    headers,
    body: JSON.stringify({ args: [] }),
  });
  assert.deepEqual(JSON.parse(value.text), {
    ok: true,
    value: { name: "copy-diagnostics", args: [] },
  });
  const refused = await request(port, {
    method: "POST",
    path: "/native/refuse",
    headers,
    body: JSON.stringify({ args: [] }),
  });
  assert.deepEqual(JSON.parse(refused.text), {
    ok: false,
    error: { message: "The desktop refused." },
  });
  // Only the two operation paths answer a write.
  const elsewhere = await request(port, {
    method: "POST",
    path: "/elsewhere",
    headers,
    body: JSON.stringify({ args: [] }),
  });
  assert.equal(elsewhere.status, 404);
});

test("a new token refuses the old one at once", async (t) => {
  const { remote, port } = await serve();
  t.after(() => remote.close());
  const before = remote.state.token;
  const body = JSON.stringify({ version: "v1", method: "workspace_snapshot" });
  const next = remote.regenerate();
  assert.notEqual(next.token, before);
  const stale = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${before}`,
    },
    body,
  });
  assert.equal(stale.status, 401);
  const current = await request(port, {
    method: "POST",
    path: "/rpc",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${next.token}`,
    },
    body,
  });
  assert.equal(current.status, 200);
  // The token is a credential, so the file holding it is not readable by others.
  const saved = JSON.parse(fs.readFileSync(remote.settingsFile, "utf8"));
  assert.equal(saved.token, next.token);
  if (process.platform !== "win32")
    assert.equal(fs.statSync(remote.settingsFile).mode & 0o777, 0o600);
});

test("an install that never turns remote access on keeps no token on disk", () => {
  const remote = new Remote({
    root: install(),
    profile: fs.mkdtempSync(path.join(os.tmpdir(), "dazedtl-profile-")),
    diagnostics: { record() {}, failure() {} },
    call: async () => ({}),
    native: async () => ({}),
  });
  assert.equal(fs.existsSync(remote.settingsFile), false);
  remote.enable(true);
  assert.equal(fs.existsSync(remote.settingsFile), true);
  remote.close();
});

test("the address follows where the server binds", () => {
  const remote = new Remote({
    root: install(),
    profile: fs.mkdtempSync(path.join(os.tmpdir(), "dazedtl-profile-")),
    diagnostics: { record() {}, failure() {} },
    call: async () => ({}),
    native: async () => ({}),
  });
  assert.deepEqual(remote.configure({ host: "127.0.0.1", port: 47820 }).urls, [
    "http://127.0.0.1:47820/",
  ]);
  // Every interface puts the network's own addresses first, the loopback last.
  const network = remote.configure({ host: "0.0.0.0", port: 47820 });
  assert.equal(network.host, "0.0.0.0");
  assert.equal(network.urls.at(-1), "http://127.0.0.1:47820/");
  assert.ok(network.urls.length >= 1);
});

test("a port that cannot work is refused", () => {
  const remote = new Remote({
    root: install(),
    profile: fs.mkdtempSync(path.join(os.tmpdir(), "dazedtl-profile-")),
    diagnostics: { record() {}, failure() {} },
    call: async () => ({}),
    native: async () => ({}),
  });
  for (const port of [80, 0, 70000, Number.NaN])
    assert.throws(
      () => remote.configure({ host: "127.0.0.1", port }),
      /port between/,
      String(port),
    );
  // A host the server cannot bind is not a choice Settings offers.
  assert.equal(
    remote.configure({ host: "10.0.0.1", port: 47820 }).host,
    "127.0.0.1",
  );
});
