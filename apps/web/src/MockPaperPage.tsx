import React from 'react';
import { PaperSummary } from './api';
import { MOCK_PAPER_ID } from './mockPaper';

type MockPaperPageProps = {
  paper: PaperSummary;
  page: number;
};

const paragraphClass = 'm-0 text-justify leading-[1.48] text-slate-700';
const sectionClass = 'mb-[1.4cqw] mt-[2.4cqw] font-sans text-[2.9cqw] font-bold tracking-tight text-slate-950';

function PageNumber({ page }: { page: number }) {
  return (
    <span className="absolute bottom-[3.4%] left-1/2 -translate-x-1/2 font-serif text-[2.2cqw] text-slate-500">
      {page}
    </span>
  );
}

function RelatedPaperPage({ paper, page }: MockPaperPageProps) {
  const venue = String(paper.metadata?.venue ?? 'CHI');
  const year = String(paper.metadata?.year ?? '2022');
  if (page === 1) {
    return (
      <>
        <header className="mb-[4cqw] text-center">
          <h1 className="m-0 font-sans text-[4.6cqw] font-bold leading-[1.1] tracking-[-0.03em] text-slate-950">
            {paper.title}
          </h1>
          <p className="mb-0 mt-[2cqw] font-sans text-[2.2cqw] text-slate-600">
            Research Interfaces Group · {venue} {year}
          </p>
        </header>
        <section className="mx-auto mb-[3cqw] w-[84%] border-y border-slate-300 py-[2.2cqw]">
          <h2 className="m-0 text-center font-sans text-[2.55cqw] font-bold text-slate-900">Abstract</h2>
          <p className={`${paragraphClass} mt-[1cqw]`}>{paper.abstract}</p>
        </section>
        <div className="grid grid-cols-2 gap-[4.4cqw]">
          <section>
            <h2 className={sectionClass}>1 Introduction</h2>
            <p className={paragraphClass}>
              Reading scientific literature requires frequent movement between unfamiliar terms, related passages, and prior
              work. These context switches interrupt comprehension and make it difficult to preserve a coherent reading thread.
            </p>
            <h2 className={sectionClass}>2 Design rationale</h2>
            <p className={paragraphClass}>
              The interface keeps the source document central while placing contextual assistance close to the passage that
              triggered it. Readers can inspect supporting material without losing their position in the paper.
            </p>
          </section>
          <section>
            <h2 className={sectionClass}>3 Interface approach</h2>
            <p className={paragraphClass}>{paper.abstract}</p>
            <div className="mt-[3cqw] rounded-[1.3cqw] border border-sky-200 bg-sky-50 p-[2.6cqw] font-sans text-sky-950">
              <strong className="block text-[2.5cqw]">Source-preserving assistance</strong>
              <span className="mt-[1cqw] block leading-[1.45]">
                Keep the paper visible, expose context on demand, and retain every navigation step.
              </span>
            </div>
          </section>
        </div>
      </>
    );
  }

  return (
    <>
      <h2 className={sectionClass}>4 Evaluation</h2>
      <p className={paragraphClass}>
        We compared the contextual interface with a conventional PDF reader using comprehension and evidence-retrieval tasks.
        Participants worked with unfamiliar papers and could freely revisit the source throughout each task.
      </p>
      <div className="my-[4cqw] grid grid-cols-2 gap-[2.5cqw] font-sans text-center">
        <div className="rounded-[1.2cqw] border border-emerald-200 bg-emerald-50 p-[3cqw]">
          <strong className="block text-[4cqw] text-emerald-700">−31%</strong>
          <span className="text-[2cqw] text-slate-600">navigation effort</span>
        </div>
        <div className="rounded-[1.2cqw] border border-blue-200 bg-blue-50 p-[3cqw]">
          <strong className="block text-[4cqw] text-blue-700">+16%</strong>
          <span className="text-[2cqw] text-slate-600">evidence accuracy</span>
        </div>
      </div>
      <p className={paragraphClass}>
        The interface reduced navigation effort while preserving readers’ access to the original scholarly source. Participants
        reported that contextual previews were most useful when they remained visually connected to the active passage.
      </p>
      <h2 className={`${sectionClass} mt-[5cqw]`}>5 Discussion</h2>
      <p className={paragraphClass}>
        Scholarly reading tools should augment rather than replace source documents. Structured representations are most useful
        when every generated explanation remains traceable to the paper and uncertainty is visible to the reader.
      </p>
      <h2 className={`${sectionClass} mt-[5cqw]`}>6 Conclusion</h2>
      <p className={paragraphClass}>
        Contextual scholarly interfaces can reduce interaction cost while helping readers maintain a coherent understanding of a
        paper’s argument and evidence.
      </p>
    </>
  );
}

function FirstPage() {
  return (
    <>
      <header className="mb-[3.2cqw] text-center">
        <h1 className="m-0 font-sans text-[4.8cqw] font-bold leading-[1.08] tracking-[-0.035em] text-slate-950">
          Argument Graphs for Evidence-Grounded Scholarly Reading
        </h1>
        <p className="mb-0 mt-[2.1cqw] font-sans text-[2.35cqw] text-slate-600">
          Mira Chen · Alex Rivera · Noah Williams
        </p>
        <p className="m-0 font-sans text-[2cqw] text-slate-500">Human-Centered Intelligence Lab · UIST 2026</p>
      </header>

      <section className="mx-auto mb-[2.5cqw] w-[84%] border-y border-slate-300 py-[2.1cqw]">
        <h2 className="m-0 text-center font-sans text-[2.55cqw] font-bold text-slate-900">Abstract</h2>
        <p className={`${paragraphClass} mt-[1cqw]`}>
          Scientific papers present evidence linearly even when their arguments are deeply interconnected. We introduce a
          contribution-centered representation that links claims to motivation, implementation, formalization, experiments,
          and prior work. A three-pane reading workspace turns this representation into a recursively explorable argument graph.
        </p>
      </section>

      <div className="grid grid-cols-2 gap-[4.4cqw]">
        <section>
          <h2 className={sectionClass}>1 Introduction</h2>
          <p className={paragraphClass}>
            Readers must repeatedly connect a paper’s claims, evidence, and prior work across distant sections. This reconstruction
            is especially costly when a paper introduces several contributions that share methods or evaluation evidence.
          </p>
          <p className={`${paragraphClass} mt-[1.7cqw]`}>
            Existing PDF readers expose text, search, and annotations, but they do not make the author’s argument structure
            inspectable. Consequently, readers see what a paper contains without seeing why each contribution follows.
          </p>
          <h2 className={sectionClass}>2 Design goals</h2>
          <p className={paragraphClass}>
            Our design preserves the original paper as the source of truth while providing a compact overview, direct evidence
            navigation, and progressive disclosure of contribution-level detail.
          </p>
        </section>
        <section>
          <h2 className={sectionClass}>Contributions</h2>
          <ol className="m-0 grid gap-[1.6cqw] pl-[4cqw] text-slate-700">
            <li>A Paper Argument Graph centered on explicit scholarly contributions.</li>
            <li>A grounded coverage score that exposes missing facets and weak evidence.</li>
            <li>An interactive workspace connecting graph nodes to precise PDF locations.</li>
          </ol>
          <div className="mt-[3.4cqw] rounded-[1.4cqw] border border-blue-200 bg-blue-50 p-[2.5cqw] font-sans text-blue-950">
            <strong className="block text-[2.45cqw]">Paper Argument Graph</strong>
            <span className="mt-[1cqw] block text-[2.1cqw] leading-[1.45]">
              Contribution → Why / How / Proof → grounded evidence
            </span>
          </div>
          <h2 className={sectionClass}>Organization</h2>
          <p className={paragraphClass}>
            We first define the representation, then describe graph construction and evaluate comprehension, navigation effort,
            and evidence coverage in a controlled reader study.
          </p>
        </section>
      </div>
    </>
  );
}

function MethodPage() {
  return (
    <>
      <div className="grid grid-cols-2 gap-[4.4cqw]">
        <section>
          <h2 className={sectionClass}>3 Paper Argument Graph</h2>
          <p className={paragraphClass}>
            We represent a paper as a contribution-centered graph linking motivation, research gaps, methods, formalization,
            experiments, results, and references. Every authored node retains a precise locator into the source document.
          </p>
        </section>
        <section>
          <h2 className={sectionClass}>3.1 Grounded construction</h2>
          <p className={paragraphClass}>
            The agent reads stable text blocks, proposes nodes and relations, and iteratively repairs the graph against structural
            and provenance constraints. Bounding boxes are derived by the server rather than generated by the model.
          </p>
        </section>
      </div>

      <div className="mx-auto my-[3.4cqw] w-[74%] rounded-[1.2cqw] border border-violet-200 bg-violet-50 px-[3cqw] py-[2.5cqw] text-center font-serif text-[3.2cqw] text-violet-950">
        coverage(c) = | grounded facets(c) | / | required facets |
      </div>

      <figure className="mx-auto mt-[4.5cqw] w-[74%]">
        <div className="relative h-[33cqw] rounded-[1.8cqw] border border-slate-300 bg-slate-50 shadow-inner">
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-[1.2cqw] border border-emerald-400 bg-emerald-100 px-[3.2cqw] py-[2cqw] font-sans font-bold text-emerald-950">
            Contribution
          </div>
          {[
            ['Why', 'left-[7%] top-[17%]', 'border-amber-300 bg-amber-100 text-amber-950'],
            ['How', 'right-[7%] top-[17%]', 'border-lime-300 bg-lime-100 text-lime-950'],
            ['Proof', 'bottom-[12%] left-1/2 -translate-x-1/2', 'border-pink-300 bg-pink-100 text-pink-950'],
          ].map(([label, position, colors]) => (
            <div key={label} className={`absolute rounded-[1cqw] border px-[2.5cqw] py-[1.5cqw] font-sans font-semibold ${position} ${colors}`}>
              {label}
            </div>
          ))}
          <div className="absolute left-[27%] top-[30%] h-px w-[15%] rotate-[18deg] bg-slate-400" />
          <div className="absolute right-[27%] top-[30%] h-px w-[15%] -rotate-[18deg] bg-slate-400" />
          <div className="absolute bottom-[31%] left-1/2 h-[14%] w-px bg-slate-400" />
        </div>
        <figcaption className="mt-[1.6cqw] text-center font-sans text-[2.1cqw] text-slate-600">
          Figure 2. Contributions recursively expand into WHY, HOW, and PROOF evidence subgraphs.
        </figcaption>
      </figure>

      <h2 className={`${sectionClass} mt-[4cqw]`}>3.2 Interaction model</h2>
      <p className={paragraphClass}>
        Selecting a node focuses its owning contribution, opens supporting evidence in the inspector, and scrolls the paper to the
        corresponding semantic unit. Search dims unrelated nodes without destroying the current graph context.
      </p>
    </>
  );
}

function ResultsPage() {
  return (
    <>
      <h2 className={sectionClass}>4 Evaluation</h2>
      <p className={paragraphClass}>
        Twenty-four readers completed comprehension and evidence-retrieval tasks using either a conventional PDF reader or the
        graph workspace. Papers, task order, and interface condition were counterbalanced.
      </p>

      <h2 className={`${sectionClass} mt-[4cqw]`}>4.1 Quantitative results</h2>
      <table className="w-full border-collapse font-sans text-[2.15cqw] text-slate-700">
        <thead>
          <tr className="bg-slate-100 text-slate-900">
            <th className="border border-slate-300 p-[1.5cqw] text-left">Condition</th>
            <th className="border border-slate-300 p-[1.5cqw]">Evidence accuracy</th>
            <th className="border border-slate-300 p-[1.5cqw]">Navigation time</th>
            <th className="border border-slate-300 p-[1.5cqw]">Confidence</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="border border-slate-300 p-[1.5cqw]">PDF reader</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">68.4%</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">94 s</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">3.4 / 5</td>
          </tr>
          <tr className="bg-emerald-50 font-semibold text-emerald-950">
            <td className="border border-slate-300 p-[1.5cqw]">Argument graph</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">86.7%</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">57 s</td>
            <td className="border border-slate-300 p-[1.5cqw] text-center">4.2 / 5</td>
          </tr>
        </tbody>
      </table>

      <div className="my-[4.2cqw] grid grid-cols-3 gap-[2.2cqw] font-sans text-center">
        {[
          ['+18.3%', 'evidence accuracy', 'text-blue-600'],
          ['−39%', 'navigation time', 'text-emerald-600'],
          ['+0.8', 'reader confidence', 'text-violet-600'],
        ].map(([value, label, color]) => (
          <div key={label} className="rounded-[1.2cqw] border border-slate-200 bg-slate-50 p-[2.5cqw]">
            <strong className={`block text-[3.6cqw] ${color}`}>{value}</strong>
            <span className="mt-[0.7cqw] block text-[1.9cqw] text-slate-600">{label}</span>
          </div>
        ))}
      </div>

      <p className={paragraphClass}>
        Graph workspace users identified supporting evidence more accurately and with less navigation. The largest gains appeared
        on questions requiring readers to connect a contribution with results reported several pages later.
      </p>

      <h2 className={`${sectionClass} mt-[4.5cqw]`}>5 Conclusion</h2>
      <p className={paragraphClass}>
        Argument graphs can turn a linear paper into a traceable structure for learning and review. Preserving precise links to the
        source is essential: the graph should guide attention without replacing the paper or hiding uncertainty.
      </p>

      <h2 className={`${sectionClass} mt-[4cqw]`}>References</h2>
      <p className="m-0 font-serif text-[2cqw] leading-[1.5] text-slate-600">
        [1] Head et al. 2021. Augmenting Scientific Papers with Just-in-Time Definitions. CHI. [2] Kang et al. 2022.
        Threddy: An Interactive System for Personalized Thread-based Exploration and Organization of Scientific Literature.
      </p>
    </>
  );
}

export function MockPaperPage({ paper, page }: MockPaperPageProps) {
  return (
    <div className="absolute inset-0 select-none overflow-hidden bg-white px-[7.5%] py-[6.5%] [container-type:inline-size]">
      <div className="relative h-full font-serif text-[2.25cqw] text-slate-800">
        {paper.paper_id === MOCK_PAPER_ID
          ? page === 1
            ? <FirstPage />
            : page === 2
              ? <MethodPage />
              : <ResultsPage />
          : <RelatedPaperPage paper={paper} page={page} />}
        <PageNumber page={page} />
      </div>
    </div>
  );
}
