import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReaderApp } from './ReaderApp';
import { WritingApp } from './WritingApp';
import './styles.css';

function writingProjectId(): string | null | undefined {
  const route = window.location.protocol === 'file:' ? window.location.hash.slice(1) : window.location.pathname;
  const match = route.match(/^\/write(?:\/([^/]+))?\/?$/);
  if (!match) return undefined;
  return match[1] || null;
}

const projectId = writingProjectId();
createRoot(document.getElementById('root')!).render(
  projectId === undefined ? <ReaderApp /> : <WritingApp projectId={projectId} />,
);
