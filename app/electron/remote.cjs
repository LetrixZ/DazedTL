// Serves the interface and its operations to a browser, so a translation can
// be driven from another computer while the games stay on this one.
//
// It is a plain HTTP server: it binds where Settings says, and a bearer token
// authorizes every operation. The token travels in a header and never in a
// cookie, so another page cannot borrow it, and a request naming another
// origin is refused. Encryption is the proxy's or the tunnel's job, which is
// why the default bind is this computer only.
"use strict";
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const DEFAULT_PORT = 47820;
// The largest request: image edits and translation files, not game files.
const MAX_BODY = 8_000_000;
// Where the server may bind: this computer, or every interface it has.
const HOSTS = new Set(["127.0.0.1", "0.0.0.0"]);

const TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".webp", "image/webp"],
  [".gif", "image/gif"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".ico", "image/x-icon"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
  [".ttf", "font/ttf"],
  [".md", "text/markdown; charset=utf-8"],
]);

// The renderer reaches this server and nothing else. The build's own meta tag
// would win over this header, since the stricter policy applies to each
// directive, so the page is served without it.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");
const META_CSP = /<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?>/i;

/** @typedef {{ token: string, host: string, port: number, enabled: boolean }} Saved */

class Remote {
  /**
   * @param {{
   *   root: string,
   *   profile: string,
   *   diagnostics: any,
   *   call: (version: any, method: any, params: any) => Promise<any>,
   *   native: (name: string, args: any[]) => any,
   * }} options
   */
  constructor({ root, profile, diagnostics, call, native }) {
    this.root = root;
    this.dist = path.join(root, "app/dist");
    this.docs = path.join(root, "docs");
    this.settingsFile = path.join(profile, "remote.json");
    this.diagnostics = diagnostics;
    this.call = call;
    this.native = native;
    /** @type {import("node:http").Server | null} */
    this.server = null;
    /** @type {string} */
    this.error = "";
    /** @type {Saved} */
    this.saved = this.load();
  }

  /** What Settings shows and the browser uses. */
  get state() {
    return {
      enabled: this.enabled,
      host: this.saved.host,
      port: this.port,
      urls: this.urls(),
      token: this.saved.token,
      error: this.error,
    };
  }

  get enabled() {
    return process.env.DAZEDTL_REMOTE === "1" || this.saved.enabled;
  }

  /** The port actually served, which a test asks for with port 0. */
  get port() {
    const address = this.server?.address();
    return address && typeof address === "object"
      ? address.port
      : this.saved.port;
  }

  /** The addresses a browser can open, the one to try first at the front. */
  urls() {
    const hosts =
      this.saved.host === "0.0.0.0"
        ? [...addresses(), "127.0.0.1"]
        : [this.saved.host];
    return hosts.map((host) => `http://${host}:${this.port}/`);
  }

  /**
   * The saved settings. Nothing is written until remote access is turned on,
   * so an install that never uses it keeps no token on disk.
   */
  load() {
    try {
      const value = JSON.parse(fs.readFileSync(this.settingsFile, "utf8"));
      if (
        typeof value?.token === "string" &&
        value.token.length >= 32 &&
        HOSTS.has(value.host) &&
        Number.isInteger(value.port) &&
        value.port >= 0 &&
        value.port < 65536
      )
        return {
          token: value.token,
          host: value.host,
          port: value.port,
          enabled: value.enabled === true,
        };
    } catch {
      // A missing or damaged file starts again with a new token.
    }
    const requested = Number(process.env.DAZEDTL_REMOTE_PORT);
    return {
      token: crypto.randomBytes(32).toString("base64url"),
      host: "127.0.0.1",
      port:
        Number.isInteger(requested) && requested >= 0
          ? requested
          : DEFAULT_PORT,
      enabled: false,
    };
  }

  /** @param {Saved} value */
  store(value) {
    const temporary = `${this.settingsFile}.tmp`;
    // The token is a credential: only this account may read the file.
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), {
      mode: 0o600,
    });
    fs.renameSync(temporary, this.settingsFile);
    try {
      fs.chmodSync(this.settingsFile, 0o600);
    } catch {
      // Windows keeps the file's own permissions.
    }
  }

  /** Starts serving when remote access is on; a port in use is reported. */
  start() {
    if (this.server || !this.enabled) return;
    // A token this install has not written yet must survive a restart.
    if (!fs.existsSync(this.settingsFile)) this.store(this.saved);
    this.server = http.createServer((request, response) => {
      this.handle(request, response).catch((error) => {
        this.diagnostics.failure("desktop.error", error, {
          operation: "remote",
        });
        send(response, 500, "text/plain; charset=utf-8", "The request failed.");
      });
    });
    // A slow operation answers for itself; the browser waits for it.
    this.server.requestTimeout = 0;
    this.server.headersTimeout = 60_000;
    this.server.on("error", (error) => {
      this.error =
        /** @type {NodeJS.ErrnoException} */ (error).code === "EADDRINUSE"
          ? `Port ${this.saved.port} is already in use. Choose another port or close the program using it.`
          : "Remote access could not start.";
      this.diagnostics.failure("desktop.error", error, { operation: "remote" });
      // Nothing is listening, so turning it on again starts a fresh server.
      this.server = null;
    });
    this.server.listen(this.saved.port, this.saved.host);
  }

  close() {
    const server = this.server;
    this.server = null;
    if (!server) return;
    // A server still binding is closed too; the callback swallows the error a
    // server that never listened reports.
    server.close(() => {});
    server.closeAllConnections?.();
  }

  /** Turns remote access on or off, and answers with the new state. */
  enable(value) {
    this.saved = { ...this.saved, enabled: value === true };
    this.store(this.saved);
    this.error = "";
    if (this.saved.enabled) this.start();
    else this.close();
    return this.state;
  }

  /** Where to serve, which restarts the server when it is running. */
  configure(value) {
    const host = HOSTS.has(value?.host) ? value.host : "127.0.0.1";
    const port = Number(value?.port);
    if (!Number.isInteger(port) || port < 1024 || port > 65535)
      throw new Error("Choose a port between 1024 and 65535.");
    this.saved = { ...this.saved, host, port };
    this.store(this.saved);
    this.error = "";
    if (this.enabled) {
      this.close();
      this.start();
    }
    return this.state;
  }

  /** Replaces the token; a browser holding the old one is refused at once. */
  regenerate() {
    this.saved = {
      ...this.saved,
      token: crypto.randomBytes(32).toString("base64url"),
    };
    this.store(this.saved);
    return this.state;
  }

  /**
   * @param {import("node:http").IncomingMessage} request
   * @param {import("node:http").ServerResponse} response
   */
  async handle(request, response) {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    // A page of another origin may not drive this server, even with a token
    // it could not have read: its own origin is named and refused here.
    const origin = request.headers.origin;
    if (origin && !sameOrigin(origin, request.headers.host))
      return send(response, 403, "text/plain; charset=utf-8", "Refused.");
    if (request.method === "GET" || request.method === "HEAD")
      return this.serve(url, request, response);
    if (request.method === "POST") return this.accept(url, request, response);
    return send(response, 405, "text/plain; charset=utf-8", "Refused.");
  }

  /**
   * @param {URL} url
   * @param {import("node:http").IncomingMessage} request
   * @param {import("node:http").ServerResponse} response
   */
  async serve(url, request, response) {
    const head = request.method === "HEAD";
    if (url.pathname === "/guide")
      return this.serveFile(
        path.join(this.docs, "user-guide.md"),
        response,
        head,
      );
    if (url.pathname.startsWith("/guide/media/"))
      return this.serveFile(
        path.join(this.docs, "media", path.basename(url.pathname)),
        response,
        head,
      );
    const name = url.pathname === "/" ? "/index.html" : url.pathname;
    const target = path.resolve(this.dist, "." + decodeURIComponent(name));
    // Only the built interface is served, never a path that leads out of it.
    if (!target.startsWith(this.dist + path.sep))
      return send(response, 404, "text/plain; charset=utf-8", "Not found.");
    return this.serveFile(target, response, head);
  }

  /**
   * @param {string} target
   * @param {import("node:http").ServerResponse} response
   * @param {boolean} head
   */
  serveFile(target, response, head) {
    const type = TYPES.get(path.extname(target).toLowerCase());
    if (!type)
      return send(response, 404, "text/plain; charset=utf-8", "Not found.");
    let body;
    try {
      body = fs.readFileSync(target);
    } catch {
      return send(response, 404, "text/plain; charset=utf-8", "Not found.");
    }
    if (path.extname(target).toLowerCase() === ".html")
      body = Buffer.from(this.page(body.toString("utf8")));
    response.writeHead(200, {
      "Content-Type": type,
      "Content-Length": body.length,
      "Content-Security-Policy": CSP,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    });
    response.end(head ? undefined : body);
  }

  /**
   * The page without the build's own policy, which would win over this
   * server's, since the stricter of the two applies to each directive.
   *
   * @param {string} html
   */
  page(html) {
    return html.replace(META_CSP, "");
  }

  /**
   * @param {URL} url
   * @param {import("node:http").IncomingMessage} request
   * @param {import("node:http").ServerResponse} response
   */
  async accept(url, request, response) {
    const native = url.pathname.startsWith("/native/")
      ? url.pathname.slice("/native/".length)
      : "";
    if (url.pathname !== "/rpc" && !native)
      return send(response, 404, "text/plain; charset=utf-8", "Not found.");
    if (!this.authorized(request))
      return send(response, 401, "text/plain; charset=utf-8", "Refused.");
    let value;
    try {
      value = JSON.parse(await body(request));
    } catch {
      return json(response, 400, {
        ok: false,
        error: { message: "The request could not be read." },
      });
    }
    if (native) {
      const args = Array.isArray(value?.args) ? value.args : [];
      try {
        return json(response, 200, {
          ok: true,
          value: await this.native(native, args),
        });
      } catch (error) {
        return json(response, 200, {
          ok: false,
          error: { message: message(error) },
        });
      }
    }
    if (!value || typeof value !== "object" || !("method" in value))
      return json(response, 400, {
        ok: false,
        error: { message: "The request could not be read." },
      });
    return json(
      response,
      200,
      await this.call(value.version, value.method, value.params),
    );
  }

  /** @param {import("node:http").IncomingMessage} request */
  authorized(request) {
    const header = request.headers.authorization || "";
    const value = header.startsWith("Bearer ") ? header.slice(7) : "";
    const expected = this.saved.token;
    return (
      value.length === expected.length &&
      crypto.timingSafeEqual(Buffer.from(value), Buffer.from(expected))
    );
  }
}

/** This computer's addresses on its networks, without its own loopback. */
function addresses() {
  const found = [];
  for (const entries of Object.values(os.networkInterfaces()))
    for (const entry of entries ?? [])
      if (entry.family === "IPv4" && !entry.internal) found.push(entry.address);
  return found;
}

/** Whether a request's origin is the server's own. */
function sameOrigin(origin, host) {
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** @param {unknown} error */
function message(error) {
  return error instanceof Error && error.message
    ? error.message
    : "The operation could not finish.";
}

/** The request's body, refused once it is larger than a request may be. */
function body(request) {
  return new Promise((resolve, reject) => {
    /** @type {Buffer[]} */
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new Error("The request is too large."));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

/**
 * @param {import("node:http").ServerResponse} response
 * @param {number} status
 * @param {string} type
 * @param {string} text
 */
function send(response, status, type, text) {
  response.writeHead(status, {
    "Content-Type": type,
    "Content-Length": Buffer.byteLength(text),
    "X-Content-Type-Options": "nosniff",
  });
  response.end(text);
}

/**
 * @param {import("node:http").ServerResponse} response
 * @param {number} status
 * @param {unknown} value
 */
function json(response, status, value) {
  const raw = Buffer.from(JSON.stringify(value), "utf8");
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": raw.length,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(raw);
}

module.exports = { Remote };
