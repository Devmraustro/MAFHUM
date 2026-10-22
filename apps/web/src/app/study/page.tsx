"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, InfoAlert } from "@/components/alerts";
import { ApiError, documents, study } from "@/lib/api";
import type { Flashcard, StudyPlan, StudyQuestion, StudySession, StudyTopic } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Phase 5 learning workspace.
 *
 * Everything on this page is derived from material the student attached
 * themselves. When the evidence does not support an answer the page says so;
 * it never shows something plausible in its place.
 *
 * Two rules the rendering respects:
 *
 * * **Untrusted text stays text.** Passages, questions and card content come
 *   from uploaded documents, so nothing is ever interpreted as markup. There is
 *   no dangerouslySetInnerHTML anywhere in this file.
 * * **Provenance is visible.** Citations, question sources and card locators are
 *   rendered alongside the content, and a source that has since been deleted is
 *   labelled as unavailable rather than shown as though it could be checked.
 */
export default function StudyPage() {
  const { t } = useLocale();

  const [topics, setTopics] = useState<StudyTopic[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [active, setActive] = useState<StudyTopic | null>(null);

  const refreshTopics = useCallback(async () => {
    try {
      const response = await study.listTopics();
      setTopics(response.topics);
      setLoadError(null);
    } catch {
      setLoadError(t("study.error"));
    }
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    study
      .listTopics()
      .then((response) => {
        if (!cancelled) {
          setTopics(response.topics);
          setLoadError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setLoadError(t("study.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  if (loadError) return <ErrorAlert message={loadError} />;
  if (topics === null) return <p className="muted">{t("study.loading")}</p>;

  if (active) {
    return <TopicWorkspace topic={active} onBack={() => setActive(null)} />;
  }

  return (
    <main className="page">
      <h1>{t("study.title")}</h1>
      <p className="muted">{t("study.subtitle")}</p>

      <CreateTopic onCreated={refreshTopics} />

      <section aria-labelledby="study-topics-heading">
        <h2 id="study-topics-heading">{t("study.topics")}</h2>
        {topics.length === 0 ? (
          <InfoAlert>{t("study.empty")}</InfoAlert>
        ) : (
          <ul className="topic-list">
            {topics.map((topic) => (
              <li key={topic.id}>
                <button type="button" className="link-button" onClick={() => setActive(topic)}>
                  {topic.title}
                </button>
                <span className="muted" lang={topic.language}>
                  {topic.language === "ar" ? "عربية" : "français"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function CreateTopic({ onCreated }: { onCreated: () => Promise<void> }) {
  const { t } = useLocale();
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState<"ar" | "fr">("ar");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await study.createTopic({ title, language });
      setTitle("");
      await onCreated();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="study-create-heading">
      <h2 id="study-create-heading">{t("study.createTitle")}</h2>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}
      <form onSubmit={submit}>
        <label htmlFor="study-topic-title">{t("study.topicLabel")}</label>
        <input
          id="study-topic-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder={t("study.topicPlaceholder")}
          required
        />
        <label htmlFor="study-topic-language">{t("study.languageLabel")}</label>
        <select
          id="study-topic-language"
          value={language}
          onChange={(event) => setLanguage(event.target.value as "ar" | "fr")}
        >
          <option value="ar">العربية</option>
          <option value="fr">Français</option>
        </select>
        <button type="submit" disabled={busy || title.trim().length === 0}>
          {busy ? t("study.loading") : t("study.create")}
        </button>
      </form>
    </section>
  );
}

function TopicWorkspace({ topic, onBack }: { topic: StudyTopic; onBack: () => void }) {
  const { t } = useLocale();
  const [plan, setPlan] = useState<StudyPlan | null>(null);
  const [questions, setQuestions] = useState<StudyQuestion[]>([]);
  const [cards, setCards] = useState<Flashcard[]>([]);
  const [sessions, setSessions] = useState<StudySession[]>([]);
  const [materials, setMaterials] = useState<{ available: boolean; title: string | null }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [planResponse, questionResponse, cardResponse, sessionResponse, materialResponse] =
        await Promise.all([
          study.plan(topic.id),
          study.listQuestions(topic.id),
          study.dueCards(topic.id),
          study.listSessions(topic.id),
          study.listMaterials(topic.id),
        ]);
      setPlan(planResponse);
      setQuestions(questionResponse.questions);
      setCards(cardResponse.cards);
      setSessions(sessionResponse.sessions);
      setMaterials(materialResponse.materials);
      setError(null);
    } catch {
      setError(t("study.error"));
    }
  }, [topic.id, t]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      study.plan(topic.id),
      study.listQuestions(topic.id),
      study.dueCards(topic.id),
      study.listSessions(topic.id),
      study.listMaterials(topic.id),
    ])
      .then(([planResponse, questionResponse, cardResponse, sessionResponse, materialResponse]) => {
        if (cancelled) return;
        setPlan(planResponse);
        setQuestions(questionResponse.questions);
        setCards(cardResponse.cards);
        setSessions(sessionResponse.sessions);
        setMaterials(materialResponse.materials);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError(t("study.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [topic.id, t]);

  async function attachFirstDocument() {
    // Convenience for an empty topic: attach the most recent ready document.
    try {
      const listing = await documents.list({ pageSize: 1 });
      const ready = listing.items.find((item) => item.status === "ready");
      if (!ready) {
        setError(t("study.attachPrompt"));
        return;
      }
      await study.attachMaterial(topic.id, { documentId: ready.id });
      await refresh();
    } catch {
      setError(t("study.error"));
    }
  }

  return (
    <main className="page" lang={topic.language} dir={topic.language === "ar" ? "rtl" : "ltr"}>
      <button type="button" className="link-button" onClick={onBack}>
        ← {t("study.topics")}
      </button>
      <h1>{topic.title}</h1>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}

      <section aria-labelledby="study-materials-heading">
        <h2 id="study-materials-heading">{t("study.materials")}</h2>
        {materials.length === 0 ? (
          <>
            <InfoAlert>{t("study.attachPrompt")}</InfoAlert>
            <button type="button" onClick={() => void attachFirstDocument()}>
              {t("study.attachPrompt")}
            </button>
          </>
        ) : (
          <ul>
            {materials.map((material, index) => (
              <li key={index}>
                {material.title ?? "—"}
                {material.available ? null : (
                  <span className="badge badge-warn">{t("study.materialUnavailable")}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <AskPanel topicId={topic.id} sessions={sessions} onAsked={refresh} />
      <QuestionPanel topicId={topic.id} questions={questions} onAnswered={refresh} />
      <FlashcardPanel topicId={topic.id} cards={cards} onReviewed={refresh} />
      <PlanPanel topicId={topic.id} plan={plan} onChanged={refresh} />
    </main>
  );
}

function AskPanel({
  topicId,
  sessions,
  onAsked,
}: {
  topicId: string;
  sessions: StudySession[];
  onAsked: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = sessions[0] ?? null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await study.ask(topicId, question);
      setQuestion("");
      await onAsked();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="study-ask-heading">
      <h2 id="study-ask-heading">{t("study.askLabel")}</h2>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}
      <form onSubmit={submit}>
        <label htmlFor="study-question">{t("study.askLabel")}</label>
        <textarea
          id="study-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          required
        />
        <button type="submit" disabled={busy || question.trim().length === 0}>
          {busy ? t("study.loading") : t("study.ask")}
        </button>
      </form>

      {latest ? <SessionResult session={latest} /> : null}
    </section>
  );
}

function SessionResult({ session }: { session: StudySession }) {
  const { t } = useLocale();

  if (session.status === "no_evidence_available") {
    return <InfoAlert>{t("study.noEvidence")}</InfoAlert>;
  }
  if (session.status === "insufficient_evidence") {
    return (
      <div role="status">
        <span className="badge badge-warn">{t("study.insufficient")}</span>
        <p>{t("study.insufficientNote")}</p>
        {session.citations.length > 0 ? (
          <CitationList citations={session.citations} />
        ) : null}
      </div>
    );
  }

  return (
    <div role="status">
      <span className="badge badge-ok">{t("study.grounded")}</span>
      <CitationList citations={session.citations} />
    </div>
  );
}

function CitationList({
  citations,
}: {
  citations: { quote: string; locator: string }[];
}) {
  const { t } = useLocale();
  return (
    <ol aria-label={t("study.citations")}>
      {citations.map((citation, index) => (
        <li key={index}>
          {/* Untrusted text, rendered as text. Never as markup. */}
          <blockquote>{citation.quote}</blockquote>
          <cite>{citation.locator}</cite>
        </li>
      ))}
    </ol>
  );
}

function QuestionPanel({
  topicId,
  questions,
  onAnswered,
}: {
  topicId: string;
  questions: StudyQuestion[];
  onAnswered: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await study.generateQuestions(topicId, 5);
      await onAnswered();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    } finally {
      setBusy(false);
    }
  }

  async function submit(questionId: string) {
    const answer = answers[questionId];
    if (!answer?.trim()) return;
    setError(null);
    try {
      await study.submitAnswer(topicId, questionId, answer);
      await onAnswered();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    }
  }

  return (
    <section aria-labelledby="study-questions-heading">
      <h2 id="study-questions-heading">{t("study.questions")}</h2>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}
      <button type="button" onClick={() => void generate()} disabled={busy}>
        {busy ? t("study.loading") : t("study.generate")}
      </button>

      <ol>
        {questions.map((question) => (
          <li key={question.id}>
            <p>{question.prompt}</p>
            {!question.source_available ? (
              <span className="badge badge-warn">{t("study.materialUnavailable")}</span>
            ) : null}
            <label htmlFor={`answer-${question.id}`}>{t("study.yourAnswer")}</label>
            <textarea
              id={`answer-${question.id}`}
              value={answers[question.id] ?? question.submitted_answer ?? ""}
              onChange={(event) =>
                setAnswers((current) => ({ ...current, [question.id]: event.target.value }))
              }
            />
            <button type="button" onClick={() => void submit(question.id)}>
              {t("study.submit")}
            </button>
            {question.feedback ? (
              <div role="status">
                <span
                  className={
                    question.feedback.supported_by_source
                      ? "badge badge-ok"
                      : "badge badge-warn"
                  }
                >
                  {question.feedback.supported_by_source
                    ? t("study.supported")
                    : t("study.notEstablished")}
                </span>
                <p>{question.feedback.explanation}</p>
                {!question.feedback.supported_by_source ? (
                  <p className="muted">{t("study.notEstablishedNote")}</p>
                ) : null}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function FlashcardPanel({
  topicId,
  cards,
  onReviewed,
}: {
  topicId: string;
  cards: Flashcard[];
  onReviewed: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [revealed, setRevealed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const card = cards[0] ?? null;

  async function rate(rating: number) {
    if (!card) return;
    setError(null);
    try {
      await study.reviewCard(topicId, card.id, rating);
      setRevealed(null);
      await onReviewed();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    }
  }

  return (
    <section aria-labelledby="study-flashcards-heading">
      <h2 id="study-flashcards-heading">{t("study.reviewDue")}</h2>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}
      {!card ? (
        <InfoAlert>{t("study.noDue")}</InfoAlert>
      ) : (
        <div>
          <p>{card.prompt}</p>
          {!card.source_available ? (
            <span className="badge badge-warn">{t("study.materialUnavailable")}</span>
          ) : null}
          {revealed === card.id ? (
            <>
              <p>{card.answer}</p>
              <div role="group" aria-label={t("study.flashcards")}>
                <button type="button" onClick={() => void rate(0)}>
                  {t("study.rateAgain")}
                </button>
                <button type="button" onClick={() => void rate(3)}>
                  {t("study.rateHard")}
                </button>
                <button type="button" onClick={() => void rate(4)}>
                  {t("study.rateGood")}
                </button>
                <button type="button" onClick={() => void rate(5)}>
                  {t("study.rateEasy")}
                </button>
              </div>
            </>
          ) : (
            <button type="button" onClick={() => setRevealed(card.id)}>
              {t("study.reveal")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function PlanPanel({
  topicId,
  plan,
  onChanged,
}: {
  topicId: string;
  plan: StudyPlan | null;
  onChanged: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [objective, setObjective] = useState("");
  const [scheduledFor, setScheduledFor] = useState("");
  const [error, setError] = useState<string | null>(null);

  const stateLabel: Record<string, string> = {
    not_started: t("study.notStarted"),
    in_progress: t("study.inProgress"),
    completed: t("study.stateCompleted"),
    blocked: t("study.blocked"),
  };

  async function add(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await study.addPlanItem(topicId, { objective, scheduledFor });
      setObjective("");
      await onChanged();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    }
  }

  async function setState(itemId: string, state: "in_progress" | "completed") {
    setError(null);
    try {
      await study.setPlanState(topicId, itemId, state);
      await onChanged();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("study.error"));
    }
  }

  return (
    <section aria-labelledby="study-plan-heading">
      <h2 id="study-plan-heading">{t("study.plan")}</h2>
      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}

      {plan ? (
        <p role="status">
          {t("study.progress")}: {Math.round(plan.progress * 100)}% — {t("study.completed")}{" "}
          {plan.completed} / {t("study.outstanding")} {plan.outstanding}
        </p>
      ) : null}

      <form onSubmit={add}>
        <label htmlFor="plan-objective">{t("study.addObjective")}</label>
        <input
          id="plan-objective"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          required
        />
        <label htmlFor="plan-date">{t("study.addDate")}</label>
        <input
          id="plan-date"
          type="date"
          value={scheduledFor}
          onChange={(event) => setScheduledFor(event.target.value)}
          required
        />
        <button type="submit" disabled={!objective.trim() || !scheduledFor}>
          {t("study.addItem")}
        </button>
      </form>

      {!plan || plan.items.length === 0 ? (
        <InfoAlert>{t("study.planEmpty")}</InfoAlert>
      ) : (
        <ul>
          {plan.items.map((item) => (
            <li key={item.id}>
              <span>{item.objective}</span>
              <span className="muted">{item.scheduled_for}</span>
              <span className="badge">{stateLabel[item.state] ?? item.state}</span>
              {item.material_note ? (
                <span className="badge badge-warn">{item.material_note}</span>
              ) : null}
              {item.state === "completed" ? null : (
                <>
                  <button type="button" onClick={() => void setState(item.id, "in_progress")}>
                    {t("study.markInProgress")}
                  </button>
                  <button type="button" onClick={() => void setState(item.id, "completed")}>
                    {t("study.markCompleted")}
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
