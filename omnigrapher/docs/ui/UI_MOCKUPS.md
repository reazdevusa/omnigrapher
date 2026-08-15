# OmniGrapher UI Mockups

## Interactive Mockup

Open `omnigrapher/docs/ui/mockups.html` in a browser to view five tabbed screens:

1. **Main Dashboard**
2. **Graph Explorer**
3. **Agent Console**
4. **Knowledge Inspector**
5. **Settings / Infra Panel**

## 1. Main Dashboard

### Layout

- **Top:** title, tagline, global search.
- **Left (2/3):** graph visualization panel — D3 / Cytoscape render of the active knowledge graph.
- **Right (1/3):** stacked cards:
  - Agent Activity Feed
  - Knowledge Base Status
  - Model Health

### Data

| Card | Fields |
|---|---|
| Agent Feed | live agent events with timestamps and status dots |
| KB Status | documents, chunks, entities |
| Model Health | Ollama port, loaded models, embedding model |

## 2. Graph Explorer

### Layout

- **Left:** searchable node list with entity-type badges.
- **Top right:** relationship view (force-directed subgraph).
- **Bottom right:** filters (entity, document, confidence, source).
- **Bottom (full width):** selected node detail panel.

### Filters

- Entity type
- Confidence ≥ 0.8
- Has source
- Document, model, relationship

## 3. Agent Console

### Layout

- **Top row:** four agent cards with state and manual run/pause controls.
- **Middle:** live logs (auto-scrolling) and run history table.

### Agents

- Indexer
- Reasoner
- Summarizer
- Orchestrator

## 4. Knowledge Inspector

### Layout

- **Main:** document table with format, chunk count, embedding, and index status.
- **Side cards:** embedding status and index health.
- **Bottom:** action buttons (re-ingest selected, rebuild entire index).

## 5. Settings / Infra Panel

### Layout

- **Service cards:** Ollama, ChromaDB, FastAPI backend.
- **Deployment card:** Terraform / GCP / Cloudflare status.
- **Backup card:** last backup timestamps for all four layers.

## Design Tokens

- Background: `#0A0A0A`
- Panels: `#111111` with `#222222` borders
- Primary: `#4D9FFF`
- Secondary: `#A44DFF`
- Success / data flow: `#3FF0D1`
- Body text: `#D0D0D0`

## Typography

- Headings: Inter 600
- Body: Inter 400
- Mono: JetBrains Mono / Fira Code
