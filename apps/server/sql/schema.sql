CREATE TABLE IF NOT EXISTS papers (
  id UUID PRIMARY KEY,
  title TEXT NOT NULL,
  abstract TEXT DEFAULT '',
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  node_type TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  semantic_unit_ids TEXT[] NOT NULL DEFAULT '{}',
  reference_ids TEXT[] NOT NULL DEFAULT '{}',
  page_ranges JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  source_type TEXT NOT NULL,
  created_by TEXT NOT NULL,
  verified BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS edges (
  id TEXT PRIMARY KEY,
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  source_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  target_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  edge_type TEXT NOT NULL,
  semantic_unit_ids TEXT[] NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  inference_type TEXT NOT NULL DEFAULT 'direct_extraction',
  properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS semantic_units (
  id TEXT PRIMARY KEY,
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  source_location_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  created_by TEXT NOT NULL,
  properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_references (
  id TEXT PRIMARY KEY,
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  raw_text TEXT NOT NULL,
  title TEXT,
  authors JSONB NOT NULL DEFAULT '[]'::jsonb,
  year INTEGER,
  doi TEXT,
  arxiv_id TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS graph_patches (
  id UUID PRIMARY KEY,
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  operations_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
