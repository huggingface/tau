// A deliberately schematic, user-controlled explanation, not a fake live demo.
(() => {
  const steps = [
    ["MODEL → EVENTS", "A response becomes a stream.", "The provider adapter emits text, thinking deltas, and tool-call events. The harness consumes a common event format, not a provider-specific response.", "model output → provider adapter → events"],
    ["EVENTS → A REQUEST", "The model asks for a tool.", "A tool call names an operation and supplies its arguments. The harness collects the request; the model does not execute your shell or read your files itself.", "tool call → name + arguments"],
    ["REQUEST → A RESULT", "The harness does the work.", "The requested tool runs in the coding environment. Its result, including any error, becomes information the model can use on the next turn.", "read / write / edit / bash → tool result"],
    ["RESULT → CONTEXT", "The transcript grows. The loop returns.", "The tool result is appended to the conversation. The model sees the updated context and can respond or ask for another tool. Without a tool request, the turn can finish.", "transcript + result → next model request"]
  ];
  const buttons = document.querySelectorAll("[data-step]");
  buttons.forEach(button => button.addEventListener("click", () => {
    const index = Number(button.dataset.step);
    const [label, title, description, code] = steps[index];
    buttons.forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    document.getElementById("loopLabel").textContent = `0${index + 1} / ${label}`;
    document.getElementById("loopTitle").textContent = title;
    document.getElementById("loopDescription").textContent = description;
    document.getElementById("loopCode").textContent = code;
    document.getElementById("loopProgress").textContent = `${index + 1} / 4`;
  }));
})();
