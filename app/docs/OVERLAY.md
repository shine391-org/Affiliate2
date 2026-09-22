# Overlay extension contract

ViralMint OSS is the source of truth for the entire app. The desktop installer
sold at [viralmint.net](https://viralmint.net) is built by combining this OSS
codebase with a small **proprietary overlay** that adds the Visual Style preset,
AI Music tab, Tools page (Quick Chain, AI Image, Voice-over, Reframe, Watermark,
Translate + Dub, Hook Detector), the Cron Jobs scheduler, and the autonomous
agent.

This document describes the seam — what OSS exposes, and what an overlay
package must provide. **You don't need this if you're contributing to OSS.**
It exists so the maintainer can ship a downstream desktop bundle without
forking, and so any third party who wants to ship their own AGPL-compatible
bundle has a documented path.

## Goals

- OSS is **complete and self-contained** — `python run.py` works with zero
  overlay installed.
- The overlay **adds** features; it never silently replaces or weakens OSS
  behavior.
- The seam is **small and stable**: a Python package import + a JS module
  replacement. No reflection, no eval, no plugin manifest format to maintain.

## Backend seam

`backend/core/plugins.py` exposes three registration helpers:

```python
from backend.core import plugins

plugins.register_router(my_router)            # FastAPI APIRouter, mounted at /api
plugins.register_planner_action("my_action", handler)
plugins.register_config_key("my_key", value)  # served at /api/config/{key}
```

At app boot, `backend/main.py` calls `plugins.load_overlay()`, which imports
the package named by the `VIRALMINT_OVERLAY` env var (default:
`viralmint_overlay`). If that import succeeds, every router that the package
registered during import gets mounted under `/api`. If the import fails (no
package installed, etc.), boot continues silently — the OSS app runs unchanged.

### Minimal overlay package

```
viralmint_overlay/
  __init__.py           # registers routers + planner actions on import
  api/
    tools.py            # APIRouter for /api/tools/*
    cron.py             # APIRouter for /api/cron/*
  services/
    cloud_image_service.py
    ...
```

`viralmint_overlay/__init__.py`:

```python
from backend.core import plugins
from .api import tools, cron

plugins.register_router(tools.router)
plugins.register_router(cron.router)
```

## Frontend seam

`frontend/src/plugins/index.js` exports two arrays — both empty in OSS:

```js
export const pluginRoutes = []   // [{ path, element }] mounted inside <Layout/>
export const pluginNavItems = [] // [{ to, icon, label, position?: "top"|"bottom" }]
```

`App.jsx` spreads `pluginRoutes` into the router; `Layout.jsx` spreads
`pluginNavItems` into the sidebar. The downstream build replaces this single
file with one that imports proprietary pages and registers them.

### Minimal overlay frontend file

```js
// frontend/src/plugins/index.js (overlay version)
import { lazy } from "react"
import BuildIcon from "@mui/icons-material/BuildOutlined"
import ScheduleIcon from "@mui/icons-material/ScheduleOutlined"

const Tools = lazy(() => import("./proprietary/Tools"))
const CronJobs = lazy(() => import("./proprietary/CronJobs"))

export const pluginRoutes = [
  { path: "tools", element: <Tools /> },
  { path: "cron",  element: <CronJobs /> },
]

export const pluginNavItems = [
  { to: "/tools", icon: <BuildIcon />,    label: "Tools" },
  { to: "/cron",  icon: <ScheduleIcon />, label: "Cron Jobs" },
]
```

`./proprietary/` lives outside this OSS tree — point Vite's resolver at it
(via an alias in `vite.config.js`) or symlink it in during the desktop build.

## Recommended downstream layout

A clean way to ship the desktop bundle without forking OSS:

```
ViralMint-Desktop/                    # private repo
├── viralmint-oss/                    # git submodule of the OSS repo
├── overlay/
│   ├── viralmint_overlay/            # Python package (the backend overlay)
│   │   ├── __init__.py
│   │   ├── api/
│   │   └── services/
│   └── frontend/
│       ├── plugins.js                # replaces viralmint-oss/frontend/src/plugins/index.js
│       └── proprietary/              # Tools.jsx, CronJobs.jsx, SmartVideo.jsx, ...
├── desktop/                          # Electron / Tauri / PyInstaller packaging
└── build.sh                          # 1. submodule sync 2. cp overlay/frontend → oss/frontend/src/plugins
                                      # 3. pip install -e overlay/  4. npm run build  5. package
```

The build script is the only place where OSS and overlay merge. Day-to-day
development still happens in OSS, and `cd viralmint-oss && python run.py` runs
the open-source app cleanly.

## What the overlay must NOT do

- Modify OSS files in place. If you need to change OSS behavior, send a PR
  upstream. Forks defeat the point of the seam.
- Register a router or nav item that shadows an OSS path. The overlay should
  add new routes (`/tools`, `/cron`, …), not redirect or replace existing
  ones.
- Read or write OSS database tables that aren't part of the public ORM models
  in `backend/models/`. If overlay features need persistent state, create new
  tables in the overlay's own migration set.

## License note

The seam itself (this file, `backend/core/plugins.py`,
`frontend/src/plugins/index.js`) is AGPL-3.0 like the rest of OSS. The overlay
package can be any license **its author wants**, as long as it complies with
AGPL when distributing the combined work — i.e., if you ship a binary that
includes both OSS and a proprietary overlay over a network service, AGPL
section 13 still applies to the OSS portion. Most overlay authors will want
to either keep the overlay private and only ship binaries, or open it under a
permissive license. Consult a lawyer for your specific case.
