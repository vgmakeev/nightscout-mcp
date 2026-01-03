# Nightscout MCP Server

Access your CGM data from [Nightscout](https://nightscout.github.io/) in AI assistants like Claude, Cursor, etc.

## Quick Start

```bash
uvx nightscout-mcp
```

## Setup

Add to your MCP config (e.g. `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "nightscout": {
      "command": "uvx",
      "args": ["nightscout-mcp"],
      "env": {
        "NIGHTSCOUT_URL": "https://YOUR_TOKEN@your-site.nightscout.com"
      }
    }
  }
}
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `NIGHTSCOUT_URL` | Your Nightscout URL. Can include token: `https://token@site.com` |
| `NIGHTSCOUT_API_SECRET` | API secret (optional if using token in URL) |

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
