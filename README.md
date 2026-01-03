# Nightscout MCP Server

Access your CGM data from [Nightscout](https://nightscout.github.io/) in AI assistants like Claude, Cursor, etc.

## Quick Start

```bash
uvx --from git+https://github.com/vgmakeev/nightscout-mcp nightscout-mcp
```

## Setup

Add to your MCP config (e.g. `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "nightscout": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/vgmakeev/nightscout-mcp", "nightscout-mcp"],
      "env": {
        "NIGHTSCOUT_URL": "https://YOUR_TOKEN@your-site.nightscout.com"
      }
    }
  }
}
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `NIGHTSCOUT_URL` | Nightscout URL (can include token: `https://token@site.com`) | Required |
| `NIGHTSCOUT_API_SECRET` | API secret (optional if using token in URL) | - |
| `GLUCOSE_UNITS` | Display units: `mmol` or `mgdl` | `mmol` |
| `GLUCOSE_LOW` | TIR lower bound (auto-detects units: <30 = mmol) | `3.9` (70 mg/dL) |
| `GLUCOSE_HIGH` | TIR upper bound (auto-detects units: <30 = mmol) | `7.8` (140 mg/dL) |

### Example with custom TIR range

```json
{
  "nightscout": {
    "command": "uvx",
    "args": ["--from", "git+https://github.com/vgmakeev/nightscout-mcp", "nightscout-mcp"],
    "env": {
      "NIGHTSCOUT_URL": "https://TOKEN@your-site.nightscout.com",
      "GLUCOSE_UNITS": "mmol",
      "GLUCOSE_LOW": "4.0",
      "GLUCOSE_HIGH": "10.0"
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `glucose_current` | Current glucose reading |
| `glucose_history` | History for last N hours |
| `analyze` | TIR, CV, HbA1c for any date range |
| `analyze_monthly` | Monthly breakdown for a year |
| `treatments` | Insulin and carbs log |
| `status` | Nightscout server status |
| `devices` | Pump, CGM, uploader status |

## Examples

Ask your AI assistant:
- "What's my current glucose?"
- "Show my glucose history for the last 6 hours"
- "Analyze my glucose control for December 2025"
- "Give me a monthly breakdown for 2025"

## License

MIT
