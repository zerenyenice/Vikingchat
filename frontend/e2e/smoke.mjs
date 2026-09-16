// End-to-end smoke test against the fake-backed backend (backend/tests/e2e_app.py).
// Usage: see README "End-to-end UI test".
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8011";
const SHOTS = process.env.SHOTS ?? "./e2e/shots";
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("favicon")) errors.push(`console: ${m.text()}`); });

const user = `e2e${Date.now().toString(36)}`;
await page.goto(BASE);
await page.getByText("Register").click();
await page.getByPlaceholder("Username").fill(user);
await page.getByPlaceholder("Password (min 8 chars)").fill("supersecret1");
await page.getByRole("button", { name: "Create account" }).click();
await page.getByText(`Welcome, ${user}`).waitFor();
await page.screenshot({ path: `${SHOTS}/01-welcome.png` });

// Chat session with streaming reply
await page.getByRole("button", { name: "+ Chat" }).click();
await page.getByPlaceholder(/Message the agent/).fill("Hello agent, remember I like short answers");
await page.keyboard.press("Enter");
await page.locator(".msg.assistant .bubble").filter({ hasText: "Fake reply #1" }).waitFor({ timeout: 20000 });
await page.locator(".session-item.active .title").filter({ hasText: "Hello agent" }).waitFor();
await page.screenshot({ path: `${SHOTS}/02-chat.png` });

// Documents upload
await page.getByRole("button", { name: "Documents" }).click();
await page.locator('input[type="file"]').setInputFiles({ name: "notes.md", mimeType: "text/markdown", buffer: Buffer.from("# Notes\nhello") });
await page.locator(".card .name").filter({ hasText: "notes.md" }).waitFor();
await page.locator(".card .uri").filter({ hasText: "viking://resources/users/" }).waitFor();
await page.screenshot({ path: `${SHOTS}/03-documents.png` });

// Memory panel shows the fake stored memory
await page.getByRole("button", { name: "Memory" }).click();
await page.locator(".card .name").filter({ hasText: "preferences/style.md" }).waitFor();

// Skill session -> build skill
await page.getByRole("button", { name: "+ Skill session" }).click();
await page.getByPlaceholder(/Explain the procedure/).fill("Teach: build my weekly report from Jira tickets");
await page.keyboard.press("Enter");
await page.locator(".msg.assistant .bubble").filter({ hasText: "Fake reply" }).waitFor({ timeout: 20000 });
await page.getByRole("button", { name: "Build skill", exact: true }).click();
await page.locator(".modal button.primary", { hasText: "Build skill" }).click();
await page.getByText("Skill built: weekly-report-builder").waitFor({ timeout: 20000 });
await page.locator(".modal pre").filter({ hasText: "name: weekly-report-builder" }).waitFor();
await page.screenshot({ path: `${SHOTS}/04-skill-built.png` });
await page.getByRole("button", { name: "Done" }).click();
await page.locator(".badge.ok").filter({ hasText: "skill: weekly-report-builder" }).waitFor();
await page.getByRole("button", { name: "Skills" }).click();
await page.locator(".card .name").filter({ hasText: "weekly-report-builder" }).waitFor();
await page.screenshot({ path: `${SHOTS}/05-skills.png` });

// Reload keeps the login and sessions
await page.reload();
await page.locator(".session-item").first().waitFor();
const count = await page.locator(".session-item").count();
if (count !== 2) throw new Error(`expected 2 sessions after reload, got ${count}`);

await browser.close();
if (errors.length) {
  console.error("Browser errors:\n" + errors.join("\n"));
  process.exit(1);
}
console.log("E2E SMOKE OK");
