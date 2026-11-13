import { useState } from "react";
import api from "../api";

export interface ImprovementPlan {
  generated_at: string | null;
  summary: string;
  focus_topics: string[];
  action_plan: string[];
  questions: {
    question_id: string;
    question_number: number;
    topic_name: string;
    improvement_area: string;
    action_plan: string;
  }[];
}

interface Props {
  /** POST endpoint: returns the stored plan, or generates it with the LLM on the first call. */
  endpoint: string;
  /** Admins can replace an existing plan. */
  allowRegenerate?: boolean;
}

function errorDetail(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export default function ImprovementPlanPanel({ endpoint, allowRegenerate = false }: Props) {
  const [plan, setPlan] = useState<ImprovementPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async (regenerate = false) => {
    setLoading(true);
    setError("");
    try {
      // The first request analyses every question with the LLM, so allow extra time.
      const res = await api.post(`${endpoint}${regenerate ? "?regenerate=true" : ""}`, null, { timeout: 180_000 });
      setPlan(res.data);
    } catch (err: unknown) {
      setError(errorDetail(err, "Could not get improvement areas. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  const buttonClass =
    "h-9 px-4 text-sm font-medium rounded-[var(--radius-sm)] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer";

  return (
    <section
      aria-label="Improvement areas and action plan"
      className="bg-white rounded-[var(--radius)] shadow-[var(--shadow)] border border-[var(--color-border)] p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Improvement Areas &amp; Action Plan</h3>
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
            AI analysis of each question with technical next steps. Once generated, it is included in the Excel export.
          </p>
        </div>
        {!plan ? (
          <button
            onClick={() => load()}
            disabled={loading}
            className={`${buttonClass} bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)]`}
          >
            {loading ? "Analysing answers..." : "Get Improvement Areas"}
          </button>
        ) : allowRegenerate ? (
          <button
            onClick={() => load(true)}
            disabled={loading}
            className={`${buttonClass} border border-[var(--color-border)] hover:bg-gray-50`}
          >
            {loading ? "Regenerating..." : "Regenerate"}
          </button>
        ) : null}
      </div>

      {loading && (
        <p role="status" className="mt-3 text-xs text-[var(--color-text-secondary)]">
          Analysing every question. This can take up to a minute.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-[var(--radius-sm)] border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}

      {plan && (
        <div className="mt-4 space-y-4">
          <p className="text-sm">{plan.summary}</p>

          {plan.focus_topics.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--color-text-secondary)]">Focus topics</p>
              <div className="flex flex-wrap gap-2">
                {plan.focus_topics.map((topic, index) => (
                  <span key={`${index}-${topic}`} className="inline-flex h-6 items-center rounded-full bg-amber-50 px-2.5 text-xs font-medium text-amber-800">
                    {topic}
                  </span>
                ))}
              </div>
            </div>
          )}

          {plan.action_plan.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--color-text-secondary)]">Action plan</p>
              <ol className="ml-5 list-decimal space-y-1 text-sm">
                {plan.action_plan.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </div>
          )}

          <details open>
            <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-secondary)]">
              By question ({plan.questions.length})
            </summary>
            <ul className="mt-2 divide-y divide-[var(--color-border)]">
              {plan.questions.map((item) => (
                <li key={item.question_id} className="py-3 text-sm">
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    Q{item.question_number} · {item.topic_name}
                  </p>
                  <p className="mt-1"><span className="font-medium">Improve: </span>{item.improvement_area}</p>
                  <p className="mt-1"><span className="font-medium">Action plan: </span>{item.action_plan}</p>
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}
