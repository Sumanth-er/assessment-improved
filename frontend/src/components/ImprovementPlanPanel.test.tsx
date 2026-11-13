// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../api", () => ({ default: apiMock }));

import ImprovementPlanPanel from "./ImprovementPlanPanel";

const plan = {
  generated_at: "2026-11-13T10:00:00Z",
  summary: "Strong on syntax, weak on list slicing.",
  focus_topics: ["Python lists", "Big-O"],
  action_plan: ["Revise slicing rules", "Solve 10 slicing problems"],
  questions: [
    {
      question_id: "q1",
      question_number: 1,
      topic_name: "Python",
      improvement_area: "Negative indexes in slices.",
      action_plan: "Practise slicing with negative steps.",
    },
  ],
};

describe("ImprovementPlanPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.resetAllMocks();
  });

  const button = (text: string) =>
    [...container.querySelectorAll("button")].find((item) => item.textContent?.trim() === text);

  it("calls the LLM only after the button is clicked and shows the plan", async () => {
    apiMock.post.mockResolvedValue({ data: plan });
    await act(async () => root.render(<ImprovementPlanPanel endpoint="/user/assessments/a1/improvement-plan" />));

    expect(apiMock.post).not.toHaveBeenCalled();
    await act(async () => button("Get Improvement Areas")?.click());

    expect(apiMock.post).toHaveBeenCalledWith("/user/assessments/a1/improvement-plan", null, { timeout: 180_000 });
    const text = container.textContent || "";
    expect(text).toContain("Strong on syntax, weak on list slicing.");
    expect(text).toContain("Python lists");
    expect(text).toContain("Solve 10 slicing problems");
    expect(text).toContain("Q1 · Python");
    expect(text).toContain("Negative indexes in slices.");
    expect(text).toContain("Practise slicing with negative steps.");
    expect(button("Get Improvement Areas")).toBeUndefined();
    expect(button("Regenerate")).toBeUndefined();
  });

  it("shows the server's reason when the plan cannot be generated", async () => {
    apiMock.post.mockRejectedValue({
      response: { data: { detail: "Improvement areas are available after evaluation is complete." } },
    });
    await act(async () => root.render(<ImprovementPlanPanel endpoint="/x" />));
    await act(async () => button("Get Improvement Areas")?.click());

    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Improvement areas are available after evaluation is complete.",
    );
    expect(button("Get Improvement Areas")).toBeTruthy();
  });

  it("lets admins regenerate a stored plan", async () => {
    apiMock.post.mockResolvedValue({ data: plan });
    await act(async () => root.render(<ImprovementPlanPanel endpoint="/admin/attempt/t1/improvement-plan" allowRegenerate />));
    await act(async () => button("Get Improvement Areas")?.click());
    await act(async () => button("Regenerate")?.click());

    expect(apiMock.post).toHaveBeenLastCalledWith(
      "/admin/attempt/t1/improvement-plan?regenerate=true", null, { timeout: 180_000 },
    );
  });
});
