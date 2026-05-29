import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  fallback?: ReactNode;
};

type State = {
  hasError: boolean;
};

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Tool render failed', error, info);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? <div className="empty-state">工具前端渲染失败。</div>;
    }
    return this.props.children;
  }
}
