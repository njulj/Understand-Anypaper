import React from 'react';
import { createRoot } from 'react-dom/client';
import { GitBranch, UploadCloud } from 'lucide-react';
import './styles.css';

function App() {
  return (
    <main className="shell">
      <header className="toolbar">
        <div className="brand"><GitBranch /> Understand Anypaper</div>
        <nav>Paper / Contribution / Reference / Search / Filter</nav>
      </header>
      <section className="workspace">
        <aside className="pdf-pane">
          <UploadCloud />
          <h2>PDF Reader</h2>
          <p>Upload a digital CS paper. Node clicks will jump to highlighted source evidence.</p>
        </aside>
        <section className="graph-pane">
          <div className="node paper">Paper</div>
          <div className="row">
            <div className="node contribution">Contribution 1</div>
            <div className="node contribution">Contribution 2</div>
            <div className="node contribution">Contribution 3</div>
          </div>
          <div className="lanes">
            <span>WHY</span><span>HOW</span><span>PROOF</span>
          </div>
        </section>
        <aside className="inspector">
          <h2>Node Inspector</h2>
          <ul>
            <li>Summary and confidence</li>
            <li>Evidence IDs and page ranges</li>
            <li>Motivation, method, result, and reference counts</li>
            <li>Human correction patch actions</li>
          </ul>
        </aside>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
