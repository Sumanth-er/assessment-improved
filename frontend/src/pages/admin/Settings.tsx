import { useEffect, useState } from "react";
import api from "../../api";
import { btnPrimary, card } from "./types";

interface CodingEvaluationSettings {
  enabled: boolean;
  prompt: string;
  default_prompt: string;
}

interface Props {
  setMessage: (message: string) => void;
}

const MAX_PROMPT_LENGTH = 4000;

export default function Settings({ setMessage }: Props) {
  const [saved, setSaved] = useState<CodingEvaluationSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);

  const apply = (data: CodingEvaluationSettings) => {
    setSaved(data);
    setEnabled(data.enabled);
    setPrompt(data.prompt);
  };

  useEffect(() => {
    api.get("/admin/settings/coding-evaluation")
      .then((res) => apply(res.data))
      .catch(() => setLoadError("Could not load the coding evaluation settings."));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.put("/admin/settings/coding-evaluation", { enabled, prompt });
      apply(res.data);
      setMessage("Coding evaluation settings saved");
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      setMessage(typeof detail === "string" ? detail : "Failed to save the coding evaluation settings");
    } finally {
      setSaving(false);
    }
  };

  const unchanged = saved !== null && saved.enabled === enabled && saved.prompt === prompt.trim();

  return (
    <div className="space-y-6">
      <section className={`${card} p-6`} aria-labelledby="coding-evaluation-heading">
        <h2 id="coding-evaluation-heading" className="text-base font-semibold">Coding evaluation</h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Choose how Python answers to Coding questions are scored after an assessment is submitted.
        </p>

        {loadError && (
          <p role="alert" className="mt-4 rounded-[var(--radius-sm)] border border-red-200 bg-red-50 p-3 text-sm text-red-800">{loadError}</p>
        )}

        <label htmlFor="coding-llm-enabled" className="mt-5 flex cursor-pointer items-start gap-3">
          <input
            id="coding-llm-enabled"
            type="checkbox"
            checked={enabled}
            disabled={!saved}
            onChange={(e) => setEnabled(e.target.checked)}
            className="mt-0.5 h-4 w-4 cursor-pointer"
          />
          <span>
            <span className="block text-sm font-medium">Grade Python coding answers with the AI model (LLM)</span>
            <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">
              On: test cases are not run and do not count toward the score. The LLM reads the submitted code and scores it
              from 0 to 1 using the grading prompt below. Off: the score is the share of test cases passed.
              SQL questions and Coding (Output Only) questions are not affected.
            </span>
          </span>
        </label>

        <div className="mt-5">
          <label htmlFor="coding-llm-prompt" className="mb-1.5 block text-xs font-medium text-[var(--color-text-secondary)]">
            Grading prompt (optional)
          </label>
          <textarea
            id="coding-llm-prompt"
            rows={6}
            maxLength={MAX_PROMPT_LENGTH}
            value={prompt}
            disabled={!saved}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={saved?.default_prompt}
            className="w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white p-3 text-sm placeholder:text-gray-400 hover:border-gray-400 focus:border-[var(--color-primary)] focus:ring-2 focus:ring-blue-100"
          />
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            Tell the LLM how to judge the code, for example what counts as correct and how to give partial credit. Leave it
            empty to use the default shown in the box. The question, its reference answer and up to five example test cases
            are sent with the code. {prompt.length}/{MAX_PROMPT_LENGTH}
          </p>
        </div>

        <p className="mt-4 text-xs text-[var(--color-text-secondary)]">
          Changes apply to submissions evaluated after you save. Scores that were already given are not changed.
        </p>

        <div className="mt-5">
          <button onClick={save} disabled={!saved || saving || unchanged} className={btnPrimary}>
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </section>
    </div>
  );
}
