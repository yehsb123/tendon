import { useState } from "react";

import { Curate } from "./views/Curate";
import { Episodes } from "./views/Episodes";
import { Live } from "./views/Live";
import { Progress } from "./views/Progress";
import { Skills } from "./views/Skills";

type View = "live" | "progress" | "episodes" | "skills" | "curate" | "training";

/**
 * Shell root.
 *
 * Live is the default and the only view an operator sees during a shift. The other three
 * are for whoever improves the system afterwards, and may be as dense as they need to be.
 * Designing one interface for both readers is how monitoring tools become unusable in the
 * situation they were built for.
 */
export function App() {
  const [view, setView] = useState<View>("live");

  return (
    <div className="app">
      <nav className="app-nav" aria-label="Views">
        {(["live", "progress", "episodes", "skills", "curate", "training"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setView(v)}
            aria-current={view === v ? "page" : undefined}
            className="app-nav-item"
          >
            {v}
          </button>
        ))}
      </nav>

      <main className="app-main">
        {view === "live" ? <Live /> : null}
        {view === "progress" ? <Progress /> : null}
        {view === "episodes" ? <Episodes /> : null}
        {view === "skills" ? <Skills /> : null}
        {view === "curate" ? <Curate /> : null}
        {view === "training" ? <NotBuiltYet view={view} /> : null}
      </main>
    </div>
  );
}

function NotBuiltYet({ view }: { view: View }) {
  return (
    <div className="placeholder">
      <h2>{view}</h2>
      <p>
        Not built yet. See <code>docs/roadmap.md</code> — the shell arrives at v0.2, and
        this view is for reviewing runs rather than supervising one.
      </p>
    </div>
  );
}
