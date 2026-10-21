
import { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

import App from "./app/App.tsx";
import "./styles/index.css";

type AppErrorBoundaryState = { failed: boolean };

class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("The inspection interface failed to render", error, info);
  }

  render() {
    if (this.state.failed) {
      return <main className="startup-error" role="alert">
        <div className="startup-error__card">
          <span className="startup-error__mark" aria-hidden="true">!</span>
          <h1>界面加载失败</h1>
          <p>本机巡检服务可能仍在运行。请重新加载界面；如果问题持续出现，请查看运行日志。</p>
          <button type="button" onClick={() => window.location.reload()}>重新加载</button>
        </div>
      </main>;
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <AppErrorBoundary>
    <App />
  </AppErrorBoundary>,
);
