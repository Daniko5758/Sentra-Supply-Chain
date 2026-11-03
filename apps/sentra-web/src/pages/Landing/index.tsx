import { Fragment, lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { LazyMotion, MotionConfig, domAnimation } from 'motion/react';
import sentraMark from '../../assets/sentra-mark.png';
import { useScrollReveal } from '../../lib/useScrollReveal';
import { BeforeAfterSlider } from './BeforeAfterSlider';
import { DecodeText } from './DecodeText';
import { DisruptionDemo } from './DisruptionDemo';
import './landing.css';
import './landing-motion.css';

const LandingScene = lazy(() => import('./LandingScene'));

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M3 10h13M11 5l5 5-5 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="m4.5 10.2 3.2 3.2 7.8-7.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" />
    </svg>
  );
}

function ScrollRevealTitle({ id, lines }: { id?: string; lines: string[] }) {
  let wordIndex = 0;

  return (
    <h2 id={id} className="landing-scroll-title" data-reveal="title" aria-label={lines.join(' ')}>
      {lines.map((line, lineIndex) => (
        <span className="landing-scroll-title__line" key={`${lineIndex}-${line}`} aria-hidden="true">
          {line.split(/\s+/).map((word, index) => {
            const currentWordIndex = wordIndex++;
            return (
              <Fragment key={`${lineIndex}-${index}-${word}`}>
                {index > 0 ? ' ' : null}
                <span className="landing-scroll-title__word-clip">
                  <span className="landing-scroll-title__word" style={{ '--title-word-index': currentWordIndex } as CSSProperties}>{word}</span>
                </span>
              </Fragment>
            );
          })}
        </span>
      ))}
    </h2>
  );
}

function CapabilityIcon({ name }: { name: 'signal' | 'network' | 'action' }) {
  if (name === 'signal') {
    return (
      <svg aria-hidden="true" viewBox="0 0 28 28">
        <circle cx="14" cy="14" r="3.2" fill="currentColor" />
        <path d="M7.5 20.5a9.2 9.2 0 0 1 0-13M20.5 7.5a9.2 9.2 0 0 1 0 13M4 24a14.1 14.1 0 0 1 0-20M24 4a14.1 14.1 0 0 1 0 20" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
      </svg>
    );
  }

  if (name === 'network') {
    return (
      <svg aria-hidden="true" viewBox="0 0 28 28">
        <path d="M8 8.5 14 14l6-5.5M8 19.5l6-5.5 6 5.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
        <circle cx="8" cy="8.5" r="2.7" fill="currentColor" />
        <circle cx="20" cy="8.5" r="2.7" fill="currentColor" />
        <circle cx="14" cy="14" r="2.7" fill="currentColor" />
        <circle cx="8" cy="19.5" r="2.7" fill="currentColor" />
        <circle cx="20" cy="19.5" r="2.7" fill="currentColor" />
      </svg>
    );
  }

  return (
    <svg aria-hidden="true" viewBox="0 0 28 28">
      <path d="M8.2 14.5 12 18.3 20.4 9.7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
      <path d="M14 3.6a10.4 10.4 0 1 1-7.35 3.05A10.3 10.3 0 0 1 14 3.6Z" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

const capabilities = [
  {
    icon: 'signal' as const,
    eyebrow: 'Watch',
    title: 'See risk before it reaches the line',
    body: 'Bring verified external signals and operational checks into one focused monitoring view.',
  },
  {
    icon: 'network' as const,
    eyebrow: 'Understand',
    title: 'Follow the impact through your network',
    body: 'Connect supplier exposure to parts, production plans, sales orders, and the decisions that matter.',
  },
  {
    icon: 'action' as const,
    eyebrow: 'Recover',
    title: 'Turn insight into a coordinated response',
    body: 'Compare recovery options, align stakeholders, and keep every proposed action traceable.',
  },
];

const outcomes = ['Early signals', 'Connected impact', 'Confident recovery', 'Human-led action'];

const responseLayers = [
  {
    label: '01 / SIGNAL',
    title: 'A verified signal enters the picture.',
    description: 'Illustrative external alert · source checked',
    visual: 'signal',
  },
  {
    label: '02 / IMPACT',
    title: 'Exposure resolves through the network.',
    description: 'Supplier · component · production · orders',
    visual: 'impact',
  },
  {
    label: '03 / RECOVERY',
    title: 'Options are ready for human review.',
    description: 'Compare · align · keep the decision traceable',
    visual: 'recovery',
  },
];

export function LandingPage() {
  const pageRef = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState(false);
  useScrollReveal(pageRef, [], { repeat: true });

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setLoaded(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const sequence = pageRef.current?.querySelector<HTMLElement>('.landing-stack__sequence');
    if (!sequence) return;

    const cards = [...sequence.querySelectorAll<HTMLElement>('.landing-stack__card')];
    let frame = 0;

    const updateScrollProgress = () => {
      frame = 0;
      const bounds = sequence.getBoundingClientRect();
      const viewportAnchor = window.innerHeight * 0.5;
      const railStart = bounds.top + bounds.height * 0.07;
      const railEnd = bounds.bottom - bounds.height * 0.07;
      const progress = Math.max(0, Math.min(1, (viewportAnchor - railStart) / Math.max(1, railEnd - railStart)));
      sequence.style.setProperty('--stack-progress', `${(progress * 100).toFixed(2)}%`);

      let activeIndex = -1;
      let closestDistance = Number.POSITIVE_INFINITY;
      cards.forEach((card, index) => {
        const cardBounds = card.getBoundingClientRect();
        const cardCenter = (cardBounds.top + cardBounds.bottom) / 2;
        const distance = Math.abs(cardCenter - viewportAnchor);
        const focusRange = Math.max(1, window.innerHeight * 0.72);
        const focus = cardBounds.bottom <= 0 || cardBounds.top >= window.innerHeight
          ? 0
          : Math.max(0, 1 - distance / focusRange);
        card.style.setProperty('--stack-card-focus', focus.toFixed(3));
        card.style.setProperty('--stack-card-opacity', (0.28 + focus * 0.72).toFixed(3));
        card.style.setProperty('--stack-card-blur', `${((1 - focus) * 1.6).toFixed(2)}px`);
        card.style.setProperty('--stack-card-y', `${((1 - focus) * 10).toFixed(2)}px`);
        card.style.setProperty('--stack-card-scale', (0.965 + focus * 0.035).toFixed(3));
        if (cardBounds.bottom <= 0 || cardBounds.top >= window.innerHeight) return;
        if (distance < closestDistance) {
          closestDistance = distance;
          activeIndex = index;
        }
      });

      cards.forEach((card, index) => {
        if (index === activeIndex) card.dataset.active = 'true';
        else card.removeAttribute('data-active');
      });
    };

    const scheduleUpdate = () => {
      if (!frame) frame = window.requestAnimationFrame(updateScrollProgress);
    };

    window.addEventListener('scroll', scheduleUpdate, { passive: true });
    window.addEventListener('resize', scheduleUpdate);
    updateScrollProgress();

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', scheduleUpdate);
      window.removeEventListener('resize', scheduleUpdate);
    };
  }, []);

  useEffect(() => {
    const cards = [...(pageRef.current?.querySelectorAll<HTMLElement>('.landing-capability') ?? [])];
    if (cards.length === 0) return;

    let frame = 0;
    const updateCardFocus = () => {
      frame = 0;
      const viewportCenter = window.innerHeight * 0.52;
      const focusRange = Math.max(1, window.innerHeight * 0.72);

      cards.forEach((card) => {
        const bounds = card.getBoundingClientRect();
        const cardCenter = (bounds.top + bounds.bottom) / 2;
        const focus = Math.max(0, 1 - Math.abs(cardCenter - viewportCenter) / focusRange);
        card.style.setProperty('--capability-lift', `${((1 - focus) * 25).toFixed(2)}px`);
        card.style.setProperty('--capability-scale', (0.9 + focus * 0.1).toFixed(3));
        card.style.setProperty('--capability-icon-scale', (0.84 + focus * 0.16).toFixed(3));
        card.style.setProperty('--capability-icon-rotation', `${((1 - focus) * -10).toFixed(2)}deg`);
        card.dataset.scrollActive = focus > 0.52 ? 'true' : 'false';
      });
    };

    const scheduleUpdate = () => {
      if (!frame) frame = window.requestAnimationFrame(updateCardFocus);
    };

    window.addEventListener('scroll', scheduleUpdate, { passive: true });
    window.addEventListener('resize', scheduleUpdate);
    updateCardFocus();

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', scheduleUpdate);
      window.removeEventListener('resize', scheduleUpdate);
    };
  }, []);

  const scrollToElement = (target: HTMLElement) => {
    const top = target.getBoundingClientRect().top + window.scrollY - 18;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: Math.max(0, top), behavior: reducedMotion ? 'auto' : 'smooth' });
  };

  const handleSectionNavigation = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    const { hash } = event.currentTarget;
    const target = document.querySelector<HTMLElement>(hash);
    if (!target) return;

    event.preventDefault();
    scrollToElement(target);
    if (window.location.hash !== hash) window.history.pushState(null, '', hash);
  };

  const handleHomeNavigation = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    event.preventDefault();
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
    if (window.location.hash) window.history.pushState(null, '', window.location.pathname);
  };

  return (
    <LazyMotion features={domAnimation} strict>
    <MotionConfig reducedMotion="user">
    <div ref={pageRef} className="landing-page" data-loaded={loaded ? 'true' : 'false'}>
      <div className="landing-glow landing-glow--one" aria-hidden="true" />
      <div className="landing-glow landing-glow--two" aria-hidden="true" />

      <header className="landing-nav landing-shell">
        <Link className="landing-brand" to="/" aria-label="SENTRA home" onClick={handleHomeNavigation}>
          <span className="landing-brand__mark"><img src={sentraMark} alt="" /></span>
          <span>
            <strong>SENTRA</strong>
            <small>Supply Chain Intelligence</small>
          </span>
        </Link>

        <nav className="landing-nav__links" aria-label="Landing page navigation">
          <a href="#capabilities" onClick={handleSectionNavigation}>Capabilities</a>
          <a href="#workflow" onClick={handleSectionNavigation}>How it works</a>
          <a href="#platform" onClick={handleSectionNavigation}>Platform</a>
        </nav>

        <Link className="landing-nav__signin" to="/login">Sign in <ArrowIcon /></Link>
      </header>

      <main>
        <section className="landing-hero landing-shell" aria-labelledby="landing-title">
          <div className="landing-hero__copy">
            <p className="landing-kicker"><span /> Supply Chain Risk &amp; Recovery Agent</p>
            <h1 id="landing-title">
              <span className="landing-hero__title-line"><DecodeText text="See disruption." delay={450} /></span>
              <br />
              <em><DecodeText text="Shape recovery." delay={1150} duration={800} /></em>
            </h1>
            <p className="landing-hero__lede">
              SENTRA turns weak signals across your supply chain into a clear, coordinated path forward—before disruption reaches the production line.
            </p>
            <div className="landing-hero__actions">
              <Link className="landing-button landing-button--primary" to="/login">
                Open workspace <ArrowIcon />
              </Link>
              <a className="landing-button landing-button--quiet" href="#capabilities" onClick={handleSectionNavigation}>
                Explore capabilities <span aria-hidden="true">↓</span>
              </a>
            </div>
            <ul className="landing-hero__proof" aria-label="SENTRA platform principles">
              <li><CheckIcon /> Context, not noise</li>
              <li><CheckIcon /> Decisions stay traceable</li>
            </ul>
          </div>

          <section className="landing-command" aria-label="SENTRA platform preview">
            <div className="landing-command__topbar">
              <div>
                <span className="landing-command__eyebrow">SENTRA COMMAND CENTER</span>
                <strong>Supply continuity view</strong>
              </div>
              <span className="landing-command__mode"><i /> DEMO WORKSPACE</span>
            </div>

            <div className="landing-command__canvas">
              <div className="landing-command__label landing-command__label--top">Network signal</div>
              <Suspense fallback={<div className="landing-scene landing-scene--loading" aria-hidden="true" />}>
                <LandingScene />
              </Suspense>

              <DisruptionDemo />
            </div>

            <div className="landing-command__footer">
              <div className="landing-command__health"><span /> Monitoring connected sources</div>
              <div>From signal to response, in one view</div>
            </div>
          </section>
        </section>

        <section className="landing-band" aria-label="SENTRA outcomes" data-reveal="up">
          <div className="landing-shell landing-band__viewport">
            <div className="landing-band__track">
              {[false, true].map((isDuplicate) => (
                <div className="landing-band__group" aria-hidden={isDuplicate || undefined} key={isDuplicate ? 'duplicate' : 'original'}>
                  {outcomes.map((outcome) => <span className="landing-band__item" key={outcome}>{outcome}</span>)}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="capabilities" className="landing-capabilities landing-shell" aria-labelledby="capabilities-title">
          <div className="landing-section-heading">
            <p className="landing-kicker" data-reveal="left"><span /> Built for the moment uncertainty arrives</p>
            <ScrollRevealTitle id="capabilities-title" lines={['From the first signal', 'to the next best move.']} />
            <p data-reveal="right" style={{ '--reveal-i': 2 } as CSSProperties}>SENTRA gives operations teams a shared picture of risk—then keeps the route from analysis to action clear.</p>
          </div>

          <div className="landing-capability-grid" data-reveal="up" style={{ '--reveal-i': 2 } as CSSProperties}>
            {capabilities.map((capability, index) => (
              <article
                className="landing-capability"
                data-reveal={index === 1 ? 'zoom' : index === 0 ? 'left' : 'right'}
                key={capability.title}
                style={{ '--reveal-i': index + 1 } as CSSProperties}
              >
                <span className="landing-capability__icon"><CapabilityIcon name={capability.icon} /></span>
                <p>{capability.eyebrow}</p>
                <h3>{capability.title}</h3>
                <span className="landing-capability__line" />
                <div>{capability.body}</div>
              </article>
            ))}
          </div>
        </section>

        <section id="difference" className="landing-difference landing-shell" aria-labelledby="difference-title">
          <div className="landing-section-heading">
            <p className="landing-kicker" data-reveal="left"><span /> The difference, in one drag</p>
            <ScrollRevealTitle id="difference-title" lines={['Scattered alerts, or', 'one connected picture.']} />
            <p data-reveal="right" style={{ '--reveal-i': 2 } as CSSProperties}>Drag the handle to see what changes when signals, suppliers, and orders live in one place.</p>
          </div>
          <div data-reveal="zoom" style={{ '--reveal-i': 1 } as CSSProperties}>
            <BeforeAfterSlider />
          </div>
        </section>

        <section className="landing-showcase" aria-labelledby="showcase-title">
          <div className="landing-shell landing-showcase__inner">
            <div className="landing-showcase__copy" data-reveal="left">
              <p className="landing-kicker"><span /> One shared operating picture</p>
              <ScrollRevealTitle id="showcase-title" lines={['The right context, in the same place as the next move.']} />
              <p>
                Signals, impact paths, and recovery work stay connected in a workspace designed to help teams move together.
              </p>
              <span className="landing-showcase__note"><i /> From signal to response, without losing the thread</span>
            </div>

            <div className="landing-workspace" data-reveal="zoom" style={{ '--reveal-i': 1 } as CSSProperties} aria-label="SENTRA workspace preview">
              <div className="landing-workspace__chrome">
                <span className="landing-workspace__dots" aria-hidden="true"><i /><i /><i /></span>
                <span>SENTRA / CONTINUITY WORKSPACE</span>
                <span className="landing-workspace__state"><i /> Connected</span>
              </div>

              <div className="landing-workspace__body">
                <aside className="landing-workspace__sidebar" aria-hidden="true">
                  <span className="landing-workspace__sidebar-mark">S</span>
                  <span className="landing-workspace__nav landing-workspace__nav--active"><i /> Overview</span>
                  <span className="landing-workspace__nav"><i /> Signals</span>
                  <span className="landing-workspace__nav"><i /> Recovery</span>
                </aside>

                <div className="landing-workspace__main">
                  <div className="landing-workspace__heading">
                    <div><p>Operational view</p><strong>Continuity watch</strong></div>
                    <span><i /> Monitoring</span>
                  </div>

                  <div className="landing-workspace__grid">
                    <article className="landing-workspace__signal-panel">
                      <p>Signal activity</p>
                      <svg aria-hidden="true" viewBox="0 0 270 108" preserveAspectRatio="none">
                        <defs><linearGradient id="workspace-signal-fill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="#47d4df" stopOpacity=".34" /><stop offset="1" stopColor="#47d4df" stopOpacity="0" /></linearGradient></defs>
                        <path className="landing-workspace__signal-area" d="M0 93 31 78 55 83 82 50 109 64 141 36 174 48 205 18 237 34 270 11 V108 H0Z" />
                        <path className="landing-workspace__signal-trace" d="M0 93 31 78 55 83 82 50 109 64 141 36 174 48 205 18 237 34 270 11" />
                        <circle cx="205" cy="18" r="4" />
                      </svg>
                    </article>

                    <article className="landing-workspace__impact-panel">
                      <p>Impact path</p>
                      <div className="landing-workspace__impact-path"><span>Supplier</span><i /><span>Production</span><i /><span>Orders</span></div>
                      <small>Mapped from connected operational context</small>
                    </article>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="workflow" className="landing-workflow">
          <div className="landing-shell landing-workflow__inner">
            <div className="landing-workflow__copy" data-reveal="left">
              <p className="landing-kicker"><span /> One operating rhythm</p>
              <ScrollRevealTitle lines={['Understand the chain reaction—then own the response.']} />
              <p>
                The platform brings together the information your team needs at each moment, without separating monitoring from the decisions that follow.
              </p>
              <Link className="landing-text-link" to="/login">Enter the workspace <ArrowIcon /></Link>
            </div>

            <div className="landing-flow" aria-label="SENTRA workflow">
              <article className="landing-flow__step" data-reveal="left" style={{ '--reveal-i': 1 } as CSSProperties}>
                <span className="landing-flow__tag">Detect</span>
                <h3>Signals arrive with their source and context.</h3>
                <p>Separate noise from disruptions worth the team’s attention.</p>
              </article>
              <article className="landing-flow__step landing-flow__step--highlight" data-reveal="up" style={{ '--reveal-i': 2 } as CSSProperties}>
                <span className="landing-flow__tag">Connect</span>
                <h3>Exposure resolves across the supply network.</h3>
                <p>See how a local event can reach plans, parts, and commercial commitments.</p>
              </article>
              <article className="landing-flow__step" data-reveal="right" style={{ '--reveal-i': 3 } as CSSProperties}>
                <span className="landing-flow__tag">Recover</span>
                <h3>Teams align around a transparent next move.</h3>
                <p>Evaluate options with the decision trail ready for review.</p>
              </article>
            </div>
          </div>
        </section>

        <section className="landing-stack" aria-label="Illustrative SENTRA response animation" data-reveal="up">
          <div className="landing-stack__backdrop" aria-hidden="true">
            <span className="landing-stack__monogram">S</span>
            <svg viewBox="0 0 1200 900" preserveAspectRatio="none">
              <path d="M1220 80C960 90 1100 300 820 346S400 333 434 510s469 100 291 290S280 881 -40 820" />
              <circle cx="820" cy="346" r="6" />
              <circle cx="434" cy="510" r="6" />
              <circle cx="725" cy="800" r="6" />
            </svg>
          </div>

          <div className="landing-shell landing-stack__inner">
            <div className="landing-stack__intro" data-reveal="up">
              <p><span /> THE RESPONSE, AS IT TAKES SHAPE</p>
              <span className="landing-stack__scroll-hint">SIGNAL <i /> IMPACT <i /> RECOVERY</span>
            </div>

            <div className="landing-stack__sequence">
              <span className="landing-stack__rail" aria-hidden="true"><i /></span>
              {responseLayers.map((layer, index) => (
                <article
                  className={`landing-stack__card landing-stack__card--${index === 1 ? 'right' : 'left'}`}
                  data-reveal={index === 1 ? 'right' : 'left'}
                  key={layer.label}
                  style={{ '--reveal-i': index + 1, gridRow: index + 1 } as CSSProperties}
                >
                  <span className="landing-stack__node" aria-hidden="true"><i /></span>
                  <div className="landing-stack__art" data-visual={layer.visual} aria-hidden="true">
                    {layer.visual === 'signal' && (
                      <>
                        <span className="landing-stack__art-label">INCOMING SIGNAL <i /></span>
                        <div className="landing-stack__radar"><i /><i /><b /></div>
                        <div className="landing-stack__signal-wave"><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
                        <span className="landing-stack__art-foot">SOURCE VERIFIED <b>●</b></span>
                      </>
                    )}
                    {layer.visual === 'impact' && (
                      <>
                        <span className="landing-stack__art-label">EXPOSURE PATH <i /></span>
                        <div className="landing-stack__network">
                          <span>SUPPLIER</span><i />
                          <span>PART</span><i />
                          <span>LINE</span>
                        </div>
                        <div className="landing-stack__network-branch"><i /><span>OPEN ORDERS</span></div>
                        <span className="landing-stack__art-foot">CONNECTED CONTEXT <b>03 NODES</b></span>
                      </>
                    )}
                    {layer.visual === 'recovery' && (
                      <>
                        <span className="landing-stack__art-label">RESPONSE OPTIONS <i /></span>
                        <div className="landing-stack__option"><i>↗</i><span>Reroute supply</span><b>REVIEW</b></div>
                        <div className="landing-stack__option"><i>＋</i><span>Expedite order</span><b>READY</b></div>
                        <span className="landing-stack__art-foot">DECISION OWNER <b>OPERATIONS</b></span>
                      </>
                    )}
                  </div>

                  <div className="landing-stack__copy">
                    <p>{layer.label}</p>
                    <h3>{layer.title}</h3>
                    <span>{layer.description}</span>
                  </div>
                </article>
              ))}
            </div>

            <p className="landing-stack__closing" data-reveal="up">One connected picture. The next move stays yours.</p>
          </div>
        </section>

        <section id="platform" className="landing-platform landing-shell" aria-labelledby="platform-title">
          <div className="landing-platform__eyebrow" data-reveal="left"><span className="landing-platform__spark">✦</span> A calmer way to run a complex response</div>
          <div data-reveal="up" style={{ '--reveal-i': 1 } as CSSProperties}>
            <ScrollRevealTitle id="platform-title" lines={['Intelligence that keeps people in command.']} />
            <p>
              SENTRA combines automated analysis with human review, giving supply-chain teams a practical way to move from alert to accountable action.
            </p>
          </div>
          <Link className="landing-button landing-button--light" data-reveal="right" style={{ '--reveal-i': 2 } as CSSProperties} to="/login">Explore SENTRA <ArrowIcon /></Link>
        </section>
      </main>

      <footer className="landing-footer landing-shell" data-reveal="up">
        <Link className="landing-brand" to="/" aria-label="SENTRA home" onClick={handleHomeNavigation}>
          <span className="landing-brand__mark"><img src={sentraMark} alt="" /></span>
          <span><strong>SENTRA</strong><small>Supply Chain Intelligence</small></span>
        </Link>
        <p>Supply Chain Risk &amp; Recovery Agent</p>
        <Link to="/login">Sign in <ArrowIcon /></Link>
      </footer>
    </div>
    </MotionConfig>
    </LazyMotion>
  );
}
