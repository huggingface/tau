import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

class FakeClassList {
  #values = new Set();

  add(value) {
    this.#values.add(value);
  }

  remove(value) {
    this.#values.delete(value);
  }

  contains(value) {
    return this.#values.has(value);
  }
}

class FakeElement {
  constructor(tagName, text = "") {
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.classList = new FakeClassList();
    this._text = text;
  }

  set className(value) {
    this._className = value;
    for (const name of value.split(/\s+/).filter(Boolean)) this.classList.add(name);
  }

  get className() {
    return this._className ?? "";
  }

  set textContent(value) {
    this._text = value;
    this.children = [];
  }

  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((child) => child.textContent).join("");
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  querySelector(selector) {
    return this.children.find((child) => {
      if (selector === "code") return child.tagName === "code";
      if (selector === ".copy-btn") return child.classList.contains("copy-btn");
      return false;
    });
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  async click() {
    return this.listeners.get("click")?.();
  }
}

class FakeDocument {
  constructor(blocks) {
    this.blocks = blocks;
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  createElement(tagName) {
    return new FakeElement(tagName);
  }

  querySelectorAll(selector) {
    assert.equal(selector, ".docs-content pre");
    return this.blocks;
  }

  dispatchDOMContentLoaded() {
    this.listeners.get("DOMContentLoaded")?.();
  }
}

function loadCopyScript(blocks, clipboard) {
  const source = globalThis.copySource;
  const document = new FakeDocument(blocks);
  const timers = new Map();
  let nextTimer = 0;
  const window = {
    clearTimeout(id) {
      timers.delete(id);
    },
    setTimeout(callback) {
      const id = ++nextTimer;
      timers.set(id, callback);
      return id;
    },
    flushTimers() {
      for (const callback of timers.values()) callback();
      timers.clear();
    },
  };

  runInNewContext(source, { document, navigator: { clipboard }, window });
  document.dispatchDOMContentLoaded();
  return { blocks, document, window };
}

test.before(async () => {
  globalThis.copySource = await readFile("website/assets/js/copy-code.js", "utf8");
});

test("injects one keyboard-focusable button per code block and announces success", async () => {
  const code = new FakeElement("code", "echo hello\n");
  const firstBlock = new FakeElement("pre");
  firstBlock.appendChild(code);
  const secondBlock = new FakeElement("pre", "plain text\n");
  const writes = [];
  const { blocks, window } = loadCopyScript(
    [firstBlock, secondBlock],
    { writeText: async (text) => writes.push(text) },
  );

  const button = blocks[0].querySelector(".copy-btn");
  const status = blocks[0].children.find((child) => child.className === "copy-status");
  assert.ok(button);
  assert.equal(button.type, "button");
  assert.equal(button.getAttribute("aria-label"), "Copy code to clipboard");
  assert.equal(status.getAttribute("role"), "status");
  assert.equal(status.getAttribute("aria-live"), "polite");
  assert.equal(status.getAttribute("aria-atomic"), "true");
  assert.equal(blocks[1].children.length, 2);
  assert.equal(blocks[0].children.filter((child) => child.className === "copy-btn").length, 1);
  // DOMContentLoaded can only be handled once in a real page, but this also
  // guards against duplicate controls if the hook is triggered again.
  const { document } = loadCopyScript([firstBlock, secondBlock], {
    writeText: async () => undefined,
  });
  document.dispatchDOMContentLoaded();
  assert.equal(blocks[0].children.filter((child) => child.className === "copy-btn").length, 1);

  await button.click();
  assert.deepEqual(writes, ["echo hello\n"]);
  assert.equal(button.textContent, "Copied!");
  assert.equal(status.textContent, "Code copied to clipboard.");
  assert.equal(button.classList.contains("copied"), true);

  window.flushTimers();
  assert.equal(button.textContent, "Copy");
  assert.equal(status.textContent, "");
  assert.equal(button.classList.contains("copied"), false);

  // Native <button type="button"> activation preserves keyboard behavior and
  // does not submit a surrounding form. Re-running injection stays idempotent.
  assert.equal(button.tagName, "button");
  const buttonCount = blocks[0].children.filter((child) => child.className === "copy-btn").length;
  assert.equal(buttonCount, 1);
});

test("announces rejected and unavailable clipboard operations, then resets", async () => {
  const rejectedBlock = new FakeElement("pre");
  rejectedBlock.appendChild(new FakeElement("code", "rejected"));
  const rejected = loadCopyScript([rejectedBlock], {
    writeText: async () => {
      throw new Error("permission denied");
    },
  });
  const rejectedButton = rejectedBlock.querySelector(".copy-btn");
  const rejectedStatus = rejectedBlock.children.find((child) => child.className === "copy-status");

  await rejectedButton.click();
  assert.equal(rejectedButton.textContent, "Failed");
  assert.equal(rejectedStatus.textContent, "Unable to copy code to clipboard.");
  assert.equal(rejectedButton.classList.contains("copied"), false);
  rejected.window.flushTimers();
  assert.equal(rejectedStatus.textContent, "");

  const unavailableBlock = new FakeElement("pre");
  unavailableBlock.appendChild(new FakeElement("code", "unavailable"));
  const unavailable = loadCopyScript([unavailableBlock], undefined);
  const unavailableButton = unavailableBlock.querySelector(".copy-btn");
  const unavailableStatus = unavailableBlock.children.find((child) => child.className === "copy-status");

  await unavailableButton.click();
  assert.equal(unavailableButton.textContent, "Failed");
  assert.equal(unavailableStatus.textContent, "Unable to copy code to clipboard.");
});

test("keeps the visible focus affordance for keyboard users", async () => {
  const css = await readFile("website/assets/css/main.css", "utf8");
  assert.match(css, /\.copy-btn:focus-visible\s*\{[^}]*opacity:1;/s);
});
