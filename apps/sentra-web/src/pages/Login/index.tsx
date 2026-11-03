import { useState, type CSSProperties, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiError, USE_MOCKS } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { Button } from '../../components/Button';
import { Backdrop } from '../../components/Layout';

const order = (i: number) => ({ '--i': i }) as CSSProperties;

const STACK = ['SAP BTP', 'AWS Bedrock AgentCore', 'SAP Generative AI Hub', 'LangGraph'];

interface LocationState {
  from?: { pathname?: string; search?: string; hash?: string };
}

function destinationFrom(state: unknown): string {
  const from = (state as LocationState | null)?.from;
  if (!from?.pathname) {
    return '/dashboard';
  }
  return `${from.pathname}${from.search ?? ''}${from.hash ?? ''}`;
}

export function LoginPage() {
  const { status, signIn } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const destination = destinationFrom(location.state);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (status === 'authenticated') {
    return <Navigate to={destination} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      await signIn({ username, password });
      navigate(destination, { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Unable to sign in. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <Backdrop />
      <section className="hero-card hero-card--animated login-hero">
        <span className="orb orb--1" aria-hidden="true" />
        <span className="orb orb--2" aria-hidden="true" />
        <p className="eyebrow enter">Supply Chain Risk &amp; Recovery Agent</p>
        <h1 className="display enter" style={order(1)}>
          Sign in to SENTRA
        </h1>
        <p className="page-description enter" style={order(2)}>
          <span className="text-gradient">Find supply chain risk before it reaches the production line.</span>
        </p>
        <div className="login-card enter" style={order(3)}>
          <p className="eyebrow">Demo account</p>
          {status === 'checking' ? (
            <p className="login-card__hint" role="status">Checking your session...</p>
          ) : (
            <form className="login-form" onSubmit={handleSubmit}>
              <label className="login-form__field">
                <span>Username</span>
                <input
                  autoComplete="username"
                  disabled={isSubmitting}
                  name="username"
                  onChange={(event) => setUsername(event.target.value)}
                  required
                  value={username}
                />
              </label>
              <label className="login-form__field">
                <span>Password</span>
                <input
                  autoComplete="current-password"
                  disabled={isSubmitting}
                  name="password"
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </label>
              {error && <p className="login-form__error" role="alert">{error}</p>}
              <Button loading={isSubmitting} type="submit">Sign in</Button>
              {USE_MOCKS && (
                <p className="login-card__hint">Mock demo: use a role username and any non-empty password.</p>
              )}
            </form>
          )}
        </div>
        <div className="marquee enter" style={order(4)}>
          <div className="marquee__track">
            {[0, 1].map((copy) => (
              <ul key={copy} className="marquee__list" aria-hidden={copy === 1 || undefined}>
                {STACK.map((name) => (
                  <li key={name}>Built on {name}</li>
                ))}
              </ul>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
