"use client";

import Image from "next/image";
import Link from "next/link";

import { useLocale } from "@/lib/locale-context";

/**
 * The public landing page.
 *
 * Rules this page follows, because it is the first thing a visitor reads and
 * therefore the easiest place to overstate the product:
 *
 * - Every claim maps to a capability that exists in the backend and is covered
 *   by a test. There is no "AI that writes your thesis" copy, because nothing
 *   here generates text.
 * - Limitations are stated on the page itself, not buried in a doc: no OCR, no
 *   bundled antivirus, lexical rather than semantic search.
 * - The imagery is decorative and marked as such (`alt=""` plus `aria-hidden` on
 *   the wrapper) where it adds nothing to a screen reader, and carries a real
 *   description where it illustrates something.
 * - Motion is opt-in respectful: all animation is CSS, disabled under
 *   `prefers-reduced-motion`, and nothing depends on JavaScript to become
 *   visible.
 */
export function HomeContent() {
  const { t } = useLocale();

  const features = [
    {
      key: "1",
      image: "/landing/grounded-research.webp",
      alt: t("landing.feature1Alt"),
      title: t("landing.feature1Title"),
      body: t("landing.feature1Body"),
    },
    {
      key: "2",
      image: null,
      alt: "",
      title: t("landing.feature2Title"),
      body: t("landing.feature2Body"),
    },
    {
      key: "3",
      image: "/landing/study-flashcards.webp",
      alt: t("landing.feature3Alt"),
      title: t("landing.feature3Title"),
      body: t("landing.feature3Body"),
    },
    {
      key: "4",
      image: "/landing/slides-tools.webp",
      alt: t("landing.feature4Alt"),
      title: t("landing.feature4Title"),
      body: t("landing.feature4Body"),
    },
  ];

  const steps = [
    { key: "1", title: t("landing.howStep1Title"), body: t("landing.howStep1Body") },
    { key: "2", title: t("landing.howStep2Title"), body: t("landing.howStep2Body") },
    { key: "3", title: t("landing.howStep3Title"), body: t("landing.howStep3Body") },
    { key: "4", title: t("landing.howStep4Title"), body: t("landing.howStep4Body") },
  ];

  const principles = [
    t("landing.principle1"),
    t("landing.principle2"),
    t("landing.principle3"),
    t("landing.principle4"),
  ];

  return (
    <div className="landing">
      {/* ------------------------------------------------------------- hero */}
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy reveal">
          <p className="hero-eyebrow">{t("app.tagline")}</p>
          <h1 id="hero-title" className="hero-title">
            {t("landing.heroTitle")}
          </h1>
          <p className="hero-lead">{t("landing.heroLead")}</p>
          <div className="hero-actions">
            <Link className="btn btn-primary btn-lg" href="/?mode=register">
              {t("landing.heroCta")}
            </Link>
            <a className="btn btn-secondary btn-lg" href="#features">
              {t("landing.heroSecondary")}
            </a>
          </div>
          <p className="hero-note">{t("landing.heroNote")}</p>
        </div>

        <div className="hero-art reveal reveal-late">
          <Image
            src="/landing/hero-documents.webp"
            alt={t("landing.heroImageAlt")}
            width={1280}
            height={714}
            priority
            sizes="(max-width: 900px) 100vw, 52vw"
          />
        </div>
      </section>

      {/* ------------------------------------------------------------ stats */}
      <ul className="stat-strip reveal" aria-label={t("landing.featuresTitle")}>
        <li>
          <span className="stat-mark" aria-hidden="true">
            PDF
          </span>
          <span>{t("landing.statsDocuments")}</span>
        </li>
        <li>
          <span className="stat-mark" aria-hidden="true">
            RTL
          </span>
          <span>{t("landing.statsCitations")}</span>
        </li>
        <li>
          <span className="stat-mark" aria-hidden="true">
            OCR
          </span>
          <span>{t("landing.statsNoOcr")}</span>
        </li>
      </ul>

      {/* --------------------------------------------------------- features */}
      <section id="features" className="landing-section" aria-labelledby="features-title">
        <header className="section-head reveal">
          <h2 id="features-title">{t("landing.featuresTitle")}</h2>
          <p className="section-lead">{t("landing.featuresLead")}</p>
        </header>

        <div className="feature-grid">
          {features.map((feature, index) => (
            <article
              key={feature.key}
              className={`feature-card reveal${index % 2 === 1 ? " reveal-late" : ""}`}
            >
              {feature.image ? (
                <div className="feature-art">
                  <Image
                    src={feature.image}
                    alt={feature.alt}
                    width={880}
                    height={657}
                    sizes="(max-width: 700px) 100vw, 44vw"
                  />
                </div>
              ) : (
                <div className="feature-art feature-art-mark" aria-hidden="true">
                  <span className="quote-mark">»</span>
                  <span className="quote-source">PDF · p. 42</span>
                </div>
              )}
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* -------------------------------------------------------------- how */}
      <section className="landing-section" aria-labelledby="how-title">
        <header className="section-head reveal">
          <h2 id="how-title">{t("landing.howTitle")}</h2>
        </header>
        <ol className="how-list">
          {steps.map((step, index) => (
            <li key={step.key} className={`reveal${index % 2 === 1 ? " reveal-late" : ""}`}>
              <span className="how-number" aria-hidden="true">
                {index + 1}
              </span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* ------------------------------------------------------- principles */}
      <section className="landing-section principles" aria-labelledby="principles-title">
        <header className="section-head reveal">
          <h2 id="principles-title">{t("landing.principlesTitle")}</h2>
        </header>
        <ul className="principle-list">
          {principles.map((principle, index) => (
            <li key={principle} className={`reveal${index % 2 === 1 ? " reveal-late" : ""}`}>
              {principle}
            </li>
          ))}
        </ul>
        <a className="text-link" href="https://github.com/Devmraustro/MAFHUM/blob/main/README.md">
          {t("landing.limitsLink")}
        </a>
      </section>

      {/* ------------------------------------------------------------- cta */}
      <section className="cta-band reveal" aria-labelledby="cta-title">
        <div>
          <h2 id="cta-title">{t("landing.finalTitle")}</h2>
          <p>{t("landing.finalBody")}</p>
        </div>
        <Link className="btn btn-inverse btn-lg" href="/?mode=register">
          {t("landing.finalCta")}
        </Link>
      </section>

      <footer className="landing-footer">
        <p>{t("landing.footerRights")}</p>
        <nav aria-label={t("landing.footerDocs")}>
          <a href="https://github.com/Devmraustro/MAFHUM/blob/main/README.md">
            {t("landing.footerDocs")}
          </a>
          <a href="https://github.com/Devmraustro/MAFHUM/blob/main/docs/THREAT_MODEL.md">
            {t("landing.footerSecurity")}
          </a>
        </nav>
      </footer>
    </div>
  );
}
