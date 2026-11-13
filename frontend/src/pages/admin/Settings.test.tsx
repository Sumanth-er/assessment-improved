// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock("../../api", () => ({ default: apiMock }));

import Settings from "./Settings";

describe("admin coding evaluation settings", () => {
  let container: HTMLDivElement;
  let root: Root;
  const setMessage = vi.fn();

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    apiMock.get.mockResolvedValue({ data: { enabled: false, prompt: "", default_prompt: "Default grading rules" } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<Settings setMessage={setMessage} />));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.resetAllMocks();
  });

  const checkbox = () => container.querySelector<HTMLInputElement>("#coding-llm-enabled")!;
  const textarea = () => container.querySelector<HTMLTextAreaElement>("#coding-llm-prompt")!;
  const saveButton = () =>
    [...container.querySelectorAll("button")].find((item) => item.textContent?.trim() === "Save")!;

  it("loads the saved setting and shows the default prompt as a placeholder", () => {
    expect(apiMock.get).toHaveBeenCalledWith("/admin/settings/coding-evaluation");
    expect(checkbox().checked).toBe(false);
    expect(textarea().placeholder).toBe("Default grading rules");
    expect(saveButton().disabled).toBe(true);
  });

  it("saves the switch and the grading prompt", async () => {
    apiMock.put.mockResolvedValue({ data: { enabled: true, prompt: "Check edge cases", default_prompt: "Default grading rules" } });

    await act(async () => checkbox().click());
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    await act(async () => {
      setValue.call(textarea(), "Check edge cases");
      textarea().dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(saveButton().disabled).toBe(false);
    await act(async () => saveButton().click());

    expect(apiMock.put).toHaveBeenCalledWith("/admin/settings/coding-evaluation", {
      enabled: true,
      prompt: "Check edge cases",
    });
    expect(setMessage).toHaveBeenCalledWith("Coding evaluation settings saved");
    expect(checkbox().checked).toBe(true);
    expect(saveButton().disabled).toBe(true);
  });
});
