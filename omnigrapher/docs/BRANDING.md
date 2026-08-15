# OmniGrapher Branding & Identity

## Taglines

- **Primary:** “Understand Everything. Connect Everything.”
- **Alternative:** “AI that thinks in graphs.”

## Core Philosophy

```text
Meaning → Structure → Intelligence → Automation
```

- **Everything is a node.** Documents, ideas, facts, memories, agents, and infrastructure all exist as nodes in a unified graph.
- **Connection is intelligence.** The value of information is in its relationships.
- **Local-first, always on.** The graph should be available on the desktop first; cloud is an extension.

## Color Palette

| Name | Hex | Role |
|---|---|---|
| Neon Blue | `#4D9FFF` | Primary accent, links, active states, hero glow |
| Cyber Purple | `#A44DFF` | Secondary accent, agent highlights, gradients |
| Deep Black | `#0A0A0A` | Background, panels, code blocks |
| Graph Cyan | `#3FF0D1` | Success, data-flow, graph edges, highlights |
| Soft Gray | `#D0D0D0` | Body text, secondary labels, borders |

## Gradients

```css
--gradient-primary: linear-gradient(135deg, #4D9FFF 0%, #A44DFF 100%);
--gradient-glow: radial-gradient(circle, rgba(77,159,255,0.3) 0%, rgba(164,77,255,0.0) 70%);
```

## Typography

- **Headings:** Inter, Poppins, SF Pro
- **Body:** Inter, Roboto
- **Mono / Code:** JetBrains Mono, Fira Code

## Logo Assets

All SVG logo variants live in `omnigrapher/assets/logo/`:

- `graph_pulse.svg` — central glowing node with radial graph lines
- `omni_eye.svg` — abstract eye, iris of graph nodes, LLM core pupil
- `infinite_graph.svg` — infinity loop with embedded micro-nodes

## Usage Rules

1. Always use the SVG variants for web or print.
2. The primary logo works on dark backgrounds (`#0A0A0A`).
3. For light backgrounds, invert to the cyan/blue combo or use the black logomark.
4. Do not distort, rotate, or recolor the brand marks outside the palette.

## Voice & Tone

- Direct, technical, and concise.
- No false certainty — explain the chain of thought.
- Graph-first language: *nodes*, *edges*, *relationships*, *confidence*, *path*.
