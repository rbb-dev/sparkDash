// mgllm: a unit with execUrl is read through its sparkdash-api, never over SSH (server/collectors/ssh.js execOverHttp).
import { afterEach, beforeEach, test } from "node:test";
import { strict as assert } from "node:assert";
import http from "node:http";
import { sshExec } from "../ssh.js";

let server, url, seen;

beforeEach(async () => {
  seen = [];
  server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ auth: req.headers.authorization, body: JSON.parse(body) });
      const { cmd } = JSON.parse(body);
      if (cmd === "echo ok") return res.end("ok\n");
      if (cmd === "redirect me") {
        res.statusCode = 307;
        res.setHeader("Location", "http://192.0.2.9/steal");
        return res.end();
      }
      res.statusCode = 403;
      res.end('{"error": "not a known sparkDash line"}');
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  url = `http://127.0.0.1:${server.address().port}/sparkdashapi/exec`;
  process.env.SPARKDASH_EXEC_URL_PREFIXES = `http://127.0.0.1:${server.address().port}/`;
  process.env.SPARKDASH_EXEC_TOKEN = "t".repeat(40);
});

afterEach(async () => {
  delete process.env.SPARKDASH_EXEC_URL_PREFIXES;
  delete process.env.SPARKDASH_EXEC_TOKEN;
  await new Promise((r) => server.close(r));
});

const unit = () => ({ id: "s1", lanIp: "192.0.2.1", ssh: { user: "nobody", auth: "key" }, execUrl: url });

test("a unit with execUrl sends the line to its sparkdash-api with the token and gets what it prints, trimmed", async () => {
  assert.equal(await sshExec(unit(), "echo ok"), "ok");
  assert.deepEqual(seen, [{ auth: `Bearer ${"t".repeat(40)}`, body: { cmd: "echo ok" } }]);
});

test("a refused line rejects, as a failed command does", async () => {
  await assert.rejects(sshExec(unit(), "id"), /answered 403/);
});

test("an execUrl outside SPARKDASH_EXEC_URL_PREFIXES is never called, so the token stays home", async () => {
  process.env.SPARKDASH_EXEC_URL_PREFIXES = "http://192.0.2.11:9701/";
  await assert.rejects(sshExec(unit(), "echo ok"), /not under SPARKDASH_EXEC_URL_PREFIXES/);
  assert.equal(seen.length, 0);
});

test("without any allowed prefix no execUrl is used", async () => {
  delete process.env.SPARKDASH_EXEC_URL_PREFIXES;
  await assert.rejects(sshExec(unit(), "echo ok"), /not under SPARKDASH_EXEC_URL_PREFIXES/);
  assert.equal(seen.length, 0);
});

test("a URL that names an allowed host:port as its user goes elsewhere, so it is refused", async () => {
  const port = server.address().port;
  for (const prefixes of [`http://127.0.0.1:${port}/`, `http://127.0.0.1:${port}`]) {
    process.env.SPARKDASH_EXEC_URL_PREFIXES = prefixes;
    const sneaky = { ...unit(), execUrl: `http://127.0.0.1:${port}@192.0.2.9/sparkdashapi/exec` };
    await assert.rejects(sshExec(sneaky, "echo ok"), /not under SPARKDASH_EXEC_URL_PREFIXES/);
  }
  assert.equal(seen.length, 0);
});

test("another port or scheme on an allowed host is refused", async () => {
  const port = server.address().port;
  for (const execUrl of [`http://127.0.0.1:${port + 1}/sparkdashapi/exec`, `https://127.0.0.1:${port}/sparkdashapi/exec`]) {
    await assert.rejects(sshExec({ ...unit(), execUrl }, "echo ok"), /not under SPARKDASH_EXEC_URL_PREFIXES/);
  }
});

test("a redirect is never followed, so the token cannot be sent on", async () => {
  await assert.rejects(sshExec(unit(), "redirect me"), /unreachable|redirect/i);
  assert.equal(seen.length, 1);
});
