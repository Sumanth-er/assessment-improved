// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../api", () => ({ default: apiMock }));

import Results from "./Results";

const assessment = {
  id: "assessment-1", title: "Python basics", description: null, duration: 30, negative_marking: false,
  total_questions: 2, batch_names: [], start_time: null, end_time: null, created_at: "2026-11-01T00:00:00Z",
};

function userResult(status: string, attemptId: string, name: string) {
  return {
    attempt_id: attemptId, user_id: `user-${attemptId}`, user_name: name, username: name.toLowerCase(),
    score: null, status, started_at: null, submitted_at: null, total_answered: 1,
    evaluation_status: "completed", evaluation_job_id: null, evaluation_error: null, evaluation_error_category: null,
  };
}

describe("admin Resume for attempts stopped by the tab-violation limit", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    apiMock.get.mockImplementation(async (url: string) => {
      if (url === "/assessments/") return { data: { items: [assessment] } };
      return {
        data: {
          assessment_id: "assessment-1", assessment_title: "Python basics", total_participants: 2,
          completed_count: 1, average_score: null, topic_performance: [], total_user_results: 2,
          user_results: [userResult("missed", "attempt-missed", "Asha"), userResult("in_progress", "attempt-active", "Ravi")],
        },
      };
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<Results batches={[]} />));
    const select = [...container.querySelectorAll("select")].find((item) => item.textContent?.includes("Select Assessment"))!;
    await act(async () => {
      select.value = "assessment-1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    vi.resetAllMocks();
  });

  const resumeButtons = () =>
    [...container.querySelectorAll("button")].filter((item) => item.textContent?.trim() === "Resume");

  it("shows Resume only for stopped (missed) attempts and reopens the same attempt", async () => {
    expect(resumeButtons()).toHaveLength(1);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    apiMock.post.mockResolvedValue({ data: { message: "ok" } });
    const loadsBefore = apiMock.get.mock.calls.length;

    await act(async () => resumeButtons()[0].click());

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("tab-violation count is reset to 0"));
    expect(apiMock.post).toHaveBeenCalledWith("/admin/resume-attempt/attempt-missed");
    expect(apiMock.get.mock.calls.length).toBeGreaterThan(loadsBefore); // table reloaded
  });

  it("does nothing when the admin cancels, and shows the server's reason on failure", async () => {
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    await act(async () => resumeButtons()[0].click());
    expect(apiMock.post).not.toHaveBeenCalled();

    apiMock.post.mockRejectedValue({ response: { data: { detail: "The assessment window has closed." } } });
    await act(async () => resumeButtons()[0].click());
    expect(alertSpy).toHaveBeenCalledWith("The assessment window has closed.");
  });
});
