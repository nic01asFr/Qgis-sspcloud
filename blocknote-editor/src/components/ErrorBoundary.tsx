/**
 * ErrorBoundary global editeur BlockNote.
 *
 * Sprint V1.18 Vague 1 Equipe C R3 (2026-07-05).
 *
 * Capture les erreurs de render React (n'attrape PAS les rejects promise
 * async ni les erreurs handlers ; ceux-la sont deja loggees dans main.tsx
 * via window.addEventListener('error' + 'unhandledrejection').
 *
 * Fallback : bandeau rouge sobre + bouton "Recharger la page". Integre au
 * design tokens DSFR (couleur #ce0500 / white / border rouge).
 */
import { Component, type ReactNode } from 'react';
import { toast } from 'sonner';

interface Props {
  children: ReactNode;
  /** Reset callback si besoin (defaut : window.location.reload). */
  onReset?: () => void;
}

interface State {
  error: Error | null;
  errorInfo: { componentStack?: string } | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: { componentStack?: string }): void {
    this.setState({ errorInfo });
    try {
      fetch('/api/log/client-error', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        body: JSON.stringify({
          message: error.message,
          stack: error.stack,
          context: {
            type: 'react_error_boundary',
            componentStack: errorInfo.componentStack,
          },
          ua: navigator.userAgent,
          url: window.location.pathname,
        }),
      }).catch(() => {});
    } catch {}
    try {
      toast.error("L'editeur a rencontre une erreur inattendue.", {
        id: 'error-boundary',
        description: error.message.slice(0, 200),
      });
    } catch {}
  }

  handleReload = (): void => {
    if (this.props.onReset) {
      this.props.onReset();
      this.setState({ error: null, errorInfo: null });
    } else {
      window.location.reload();
    }
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div
          role="alert"
          style={{
            padding: 24,
            margin: 24,
            background: '#fff',
            border: '1px solid #ce0500',
            borderLeft: '4px solid #ce0500',
            borderRadius: 4,
            fontFamily: 'Marianne, system-ui, sans-serif',
            color: '#161616',
            maxWidth: 720,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 18, color: '#ce0500' }}>
            L'editeur a rencontre une erreur
          </h2>
          <p style={{ marginTop: 12, fontSize: 14, lineHeight: 1.5 }}>
            Nous n'avons pas pu afficher la suite du contenu. Vos dernieres
            modifications sauvegardees (v-num) sont preservees cote serveur.
          </p>
          <details style={{ marginTop: 12, fontSize: 12, color: '#666' }}>
            <summary style={{ cursor: 'pointer' }}>Details techniques</summary>
            <pre
              style={{
                marginTop: 8,
                padding: 8,
                background: '#f6f6f6',
                border: '1px solid #e5e5e5',
                borderRadius: 3,
                overflow: 'auto',
                fontSize: 11,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {this.state.error.message}
              {this.state.errorInfo?.componentStack || ''}
            </pre>
          </details>
          <button
            type="button"
            onClick={this.handleReload}
            style={{
              marginTop: 16,
              padding: '8px 16px',
              fontSize: 14,
              fontWeight: 500,
              background: '#000091',
              color: '#fff',
              border: 'none',
              borderRadius: 3,
              cursor: 'pointer',
            }}
          >
            Recharger l'editeur
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
