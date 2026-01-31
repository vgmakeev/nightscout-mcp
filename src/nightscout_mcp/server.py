"""Nightscout MCP Server - Access CGM data from Nightscout."""

import os
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configuration from environment
NIGHTSCOUT_URL = os.environ.get("NIGHTSCOUT_URL", "")
NIGHTSCOUT_API_SECRET = os.environ.get("NIGHTSCOUT_API_SECRET", "")

# Glucose units: "mgdl" or "mmol"
GLUCOSE_UNITS = os.environ.get("GLUCOSE_UNITS", "mmol").lower()

# TIR range from environment (in mg/dL, will convert if mmol specified)
def parse_glucose_value(env_var: str, default_mgdl: float) -> float:
    """Parse glucose value from env, auto-detect units."""
    val = os.environ.get(env_var, "")
    if not val:
        return default_mgdl
    try:
        num = float(val)
        # If value < 30, assume it's mmol/L and convert to mg/dL
        if num < 30:
            return num * 18.0182
        return num
    except ValueError:
        return default_mgdl

# TIR range: default 70-140 mg/dL (3.9-7.8 mmol/L)
GLUCOSE_LOW = parse_glucose_value("GLUCOSE_LOW", 70)   # 3.9 mmol/L
GLUCOSE_HIGH = parse_glucose_value("GLUCOSE_HIGH", 140)  # 7.8 mmol/L

# Minimum valid glucose reading (below this is sensor error)
# 40 mg/dL = 2.2 mmol/L - readings below this are almost certainly sensor artifacts
GLUCOSE_MIN_VALID = 40  # mg/dL

# Direction arrows
DIRECTION_ARROWS = {
    "DoubleUp": "⇈",
    "SingleUp": "↑",
    "FortyFiveUp": "↗",
    "Flat": "→",
    "FortyFiveDown": "↘",
    "SingleDown": "↓",
    "DoubleDown": "⇊",
    "NOT COMPUTABLE": "?",
    "RATE OUT OF RANGE": "⚠️",
}

def parse_nightscout_url(url_str: str) -> dict:
    """Parse Nightscout URL to extract credentials."""
    try:
        parsed = urlparse(url_str)
        return {
            "base_url": f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else ""),
            "username": parsed.username or "",
            "password": parsed.password or "",
        }
    except Exception:
        return {"base_url": url_str, "username": "", "password": ""}


def mgdl_to_mmol(mgdl: float) -> float:
    """Convert mg/dL to mmol/L."""
    return mgdl / 18.0182

def format_glucose(mgdl: float) -> str:
    """Format glucose value based on configured units."""
    if GLUCOSE_UNITS == "mgdl":
        return f"{int(round(mgdl))} mg/dL"
    else:
        return f"{mgdl_to_mmol(mgdl):.1f} mmol/L"

def format_glucose_short(mgdl: float) -> str:
    """Format glucose value (short, no units)."""
    if GLUCOSE_UNITS == "mgdl":
        return str(int(round(mgdl)))
    else:
        return f"{mgdl_to_mmol(mgdl):.1f}"

def get_tir_range_label() -> str:
    """Get TIR range label in configured units."""
    if GLUCOSE_UNITS == "mgdl":
        return f"{int(GLUCOSE_LOW)}-{int(GLUCOSE_HIGH)} mg/dL"
    else:
        return f"{mgdl_to_mmol(GLUCOSE_LOW):.1f}-{mgdl_to_mmol(GLUCOSE_HIGH):.1f} mmol/L"


def filter_valid_sgv(entries: list) -> list[int]:
    """Extract valid SGV values, filtering out sensor errors."""
    return [
        e["sgv"] for e in entries 
        if e.get("sgv") and e["sgv"] >= GLUCOSE_MIN_VALID
    ]


def calculate_stats(sgv_values: list[int]) -> dict | None:
    """Calculate glucose statistics."""
    if not sgv_values:
        return None
    
    n = len(sgv_values)
    avg = sum(sgv_values) / n
    variance = sum((v - avg) ** 2 for v in sgv_values) / n
    std_dev = variance ** 0.5
    cv = (std_dev / avg * 100) if avg > 0 else 0
    
    # Fixed ranges in mg/dL
    very_low = sum(1 for v in sgv_values if v < 54)           # <3.0 mmol/L
    low = sum(1 for v in sgv_values if 54 <= v < 70)          # 3.0-3.9 mmol/L
    # TIR uses configurable range
    in_range = sum(1 for v in sgv_values if GLUCOSE_LOW <= v <= GLUCOSE_HIGH)
    # Above target: from GLUCOSE_HIGH to 180 mg/dL (10 mmol/L)
    above_target = sum(1 for v in sgv_values if GLUCOSE_HIGH < v <= 180)
    high = sum(1 for v in sgv_values if 180 < v <= 250)       # 10.0-13.9 mmol/L
    very_high = sum(1 for v in sgv_values if v > 250)         # >13.9 mmol/L
    
    return {
        "count": n,
        "avg": round(avg, 1),
        "avg_formatted": format_glucose_short(avg),
        "std_dev": round(std_dev, 1),
        "std_dev_formatted": format_glucose_short(std_dev),
        "cv": round(cv, 1),
        "min": min(sgv_values),
        "max": max(sgv_values),
        "tir": round(in_range / n * 100, 1),
        "very_low_pct": round(very_low / n * 100, 1),
        "low_pct": round(low / n * 100, 1),
        "above_target_pct": round(above_target / n * 100, 1),
        "high_pct": round(high / n * 100, 1),
        "very_high_pct": round(very_high / n * 100, 1),
        "a1c": round((avg + 46.7) / 28.7, 1),
    }


def parse_date_to_timestamp(date_str: str) -> int:
    """Parse date string to timestamp (ms)."""
    import re
    
    # Relative: 7d, 2w, 3m, 1y
    match = re.match(r"^(\d+)([dwmy])$", date_str, re.I)
    if match:
        num = int(match.group(1))
        unit = match.group(2).lower()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        multipliers = {"d": 86400000, "w": 604800000, "m": 2592000000, "y": 31536000000}
        return now - num * multipliers[unit]
    
    # YYYY-MM
    if re.match(r"^\d{4}-\d{2}$", date_str):
        dt = datetime.strptime(date_str + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    
    # YYYY-MM-DD
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class NightscoutClient:
    """HTTP client for Nightscout API."""
    
    def __init__(self):
        config = parse_nightscout_url(NIGHTSCOUT_URL)
        self.base_url = config["base_url"]
        self.username = config["username"]
        self.password = config["password"]
        self.api_secret = NIGHTSCOUT_API_SECRET
    
    def _get_headers(self) -> dict:
        headers = {}
        if self.username:
            import base64
            creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        # Try api-secret header (works with hashed secrets)
        if self.api_secret and len(self.api_secret) == 64:
            # Looks like SHA256 hash, use as header
            headers["api-secret"] = self.api_secret
        return headers
    
    def _add_token_param(self, params: dict | None) -> dict:
        """Add token query parameter for authentication."""
        result = dict(params) if params else {}
        # If api_secret looks like a readable token (not a hash), add as query param
        if self.api_secret and len(self.api_secret) < 64:
            result["token"] = self.api_secret
        return result
    
    async def fetch(self, endpoint: str, params: dict | None = None) -> list | dict:
        if not self.base_url:
            raise ValueError("NIGHTSCOUT_URL environment variable is not set")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}{endpoint}",
                params=self._add_token_param(params),
                headers=self._get_headers(),
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
    
    async def fetch_entries_in_range(self, start_ts: int, end_ts: int, max_per_request: int = 10000) -> list:
        """Fetch all entries in date range with pagination."""
        all_entries = []
        current_end = end_ts
        
        for _ in range(100):  # Safety limit
            params = {
                "count": max_per_request,
                "find[date][$gte]": start_ts,
                "find[date][$lt]": current_end,
                "find[type]": "sgv",
            }
            
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/entries.json",
                    params=self._add_token_param(params),
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                resp.raise_for_status()
                entries = resp.json()
            
            if not entries:
                break
            
            all_entries.extend(entries)
            oldest_date = min(e["date"] for e in entries)
            
            if len(entries) < max_per_request or oldest_date <= start_ts:
                break
            
            current_end = oldest_date
        
        return all_entries


# Create server
server = Server("nightscout")
client = NightscoutClient()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="glucose_current",
            description="Get the current blood glucose reading from Nightscout",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="glucose_history",
            description="Get blood glucose history for a specified time period",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Number of hours of history (1-720, i.e. up to 30 days)",
                        "default": 6,
                        "minimum": 1,
                        "maximum": 720,
                    },
                    "count": {
                        "type": "number",
                        "description": "Maximum readings to show in output",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
            },
        ),
        Tool(
            name="analyze",
            description="Analyze glucose patterns for any date range. Supports dates (YYYY-MM-DD), months (YYYY-MM), or relative periods (7d, 2w, 3m, 1y)",
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Start date: YYYY-MM-DD, YYYY-MM, or relative (7d, 2w, 3m, 1y)",
                        "default": "7d",
                    },
                    "to": {
                        "type": "string",
                        "description": "End date (optional, defaults to now): YYYY-MM-DD or YYYY-MM",
                    },
                    "tirGoal": {
                        "type": "number",
                        "description": "TIR goal percentage",
                        "default": 70,
                        "minimum": 50,
                        "maximum": 100,
                    },
                },
            },
        ),
        Tool(
            name="analyze_monthly",
            description="Analyze glucose data broken down by month. Great for yearly reviews.",
            inputSchema={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "number",
                        "description": "Year to analyze",
                        "minimum": 2015,
                        "maximum": 2030,
                    },
                    "fromMonth": {
                        "type": "number",
                        "description": "Starting month (1-12)",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 12,
                    },
                    "toMonth": {
                        "type": "number",
                        "description": "Ending month (1-12)",
                        "default": 12,
                        "minimum": 1,
                        "maximum": 12,
                    },
                    "tirGoal": {
                        "type": "number",
                        "description": "TIR goal percentage",
                        "default": 85,
                        "minimum": 50,
                        "maximum": 100,
                    },
                },
                "required": ["year"],
            },
        ),
        Tool(
            name="treatments",
            description="Get recent treatments (insulin doses, carbs, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Hours of history (up to 7 days)",
                        "default": 24,
                        "minimum": 1,
                        "maximum": 168,
                    },
                    "count": {
                        "type": "number",
                        "description": "Maximum treatments to return",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 200,
                    },
                },
            },
        ),
        Tool(
            name="status",
            description="Get Nightscout server status and settings",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="devices",
            description="Get status of connected devices (pump, CGM, phone)",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "number",
                        "description": "Number of device status entries",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "glucose_current":
            return await glucose_current()
        elif name == "glucose_history":
            return await glucose_history(
                arguments.get("hours", 6),
                arguments.get("count", 100),
            )
        elif name == "analyze":
            return await analyze(
                arguments.get("from", "7d"),
                arguments.get("to"),
                arguments.get("tirGoal", 70),
            )
        elif name == "analyze_monthly":
            return await analyze_monthly(
                arguments["year"],
                arguments.get("fromMonth", 1),
                arguments.get("toMonth", 12),
                arguments.get("tirGoal", 85),
            )
        elif name == "treatments":
            return await treatments(
                arguments.get("hours", 24),
                arguments.get("count", 50),
            )
        elif name == "status":
            return await status()
        elif name == "devices":
            return await devices(arguments.get("count", 5))
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def glucose_current() -> list[TextContent]:
    entries = await client.fetch("/api/v1/entries.json", {"count": 1})
    if not entries:
        return [TextContent(type="text", text="No glucose readings available")]
    
    e = entries[0]
    arrow = DIRECTION_ARROWS.get(e.get("direction", ""), e.get("direction", ""))
    dt = datetime.fromtimestamp(e["date"] / 1000, tz=timezone.utc)
    delta = e.get('delta', 0)
    delta_formatted = format_glucose_short(abs(delta)) if GLUCOSE_UNITS == "mmol" else str(int(delta))
    
    text = f"""🩸 Current glucose: {format_glucose(e['sgv'])} {arrow}
📅 Time: {dt.strftime('%Y-%m-%d %H:%M')} UTC
📈 Delta: {'+' if delta >= 0 else '-'}{delta_formatted}
📱 Device: {e.get('device', 'N/A')}"""
    
    return [TextContent(type="text", text=text)]


async def glucose_history(hours: int, count: int) -> list[TextContent]:
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = now - hours * 60 * 60 * 1000
    
    entries = await client.fetch_entries_in_range(start_ts, now)
    if not entries:
        return [TextContent(type="text", text=f"No data for the last {hours} hours")]
    
    sgv_values = filter_valid_sgv(entries)
    stats = calculate_stats(sgv_values)
    
    text = f"""📊 Glucose history for {hours}h ({len(sgv_values)} readings)

📈 Statistics:
• Average: {stats['avg_formatted']}
• Min/Max: {format_glucose_short(stats['min'])}–{format_glucose_short(stats['max'])}
• TIR ({get_tir_range_label()}): {stats['tir']}%
• CV: {stats['cv']}%

📋 Recent readings:"""
    
    # Filter out sensor errors for display
    valid_entries = [e for e in entries if e.get("sgv") and e["sgv"] >= GLUCOSE_MIN_VALID]
    for e in valid_entries[:min(count, 15)]:
        dt = datetime.fromtimestamp(e["date"] / 1000, tz=timezone.utc)
        arrow = DIRECTION_ARROWS.get(e.get("direction", ""), "")
        text += f"\n• {dt.strftime('%m-%d %H:%M')}: {format_glucose_short(e['sgv'])} {arrow}"
    
    if len(valid_entries) > 15:
        text += f"\n... and {len(valid_entries) - 15} more readings"
    
    return [TextContent(type="text", text=text)]


async def analyze(from_date: str, to_date: str | None, tir_goal: int) -> list[TextContent]:
    start_ts = parse_date_to_timestamp(from_date)
    end_ts = int(datetime.now(timezone.utc).timestamp() * 1000) if not to_date else parse_date_to_timestamp(to_date)
    
    # Adjust end date if month format
    if to_date and len(to_date) == 7:  # YYYY-MM
        year, month = map(int, to_date.split("-"))
        if month == 12:
            end_ts = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        else:
            end_ts = int(datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    elif to_date and len(to_date) == 10:  # YYYY-MM-DD
        end_ts += 86400000  # End of day
    
    entries = await client.fetch_entries_in_range(start_ts, end_ts)
    if len(entries) < 10:
        return [TextContent(type="text", text="Not enough data for analysis")]
    
    sgv_values = filter_valid_sgv(entries)
    stats = calculate_stats(sgv_values)
    
    from_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    to_dt = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
    days = (end_ts - start_ts) // 86400000
    
    tir_status = "✅" if stats["tir"] >= tir_goal else "⚠️" if stats["tir"] >= 70 else "❌"
    cv_status = "✅" if stats["cv"] <= 33 else "⚠️" if stats["cv"] <= 36 else "❌"
    
    tir_label = get_tir_range_label()
    
    text = f"""📊 Glucose Analysis: {from_dt.strftime('%Y-%m-%d')} — {to_dt.strftime('%Y-%m-%d')} ({days} days, {stats['count']:,} readings)

📈 Key Metrics:
• Average glucose: {stats['avg_formatted']}
• Min/Max: {format_glucose_short(stats['min'])}–{format_glucose_short(stats['max'])}
• Standard deviation: {stats['std_dev_formatted']}
• CV: {stats['cv']}% {cv_status}
• Estimated HbA1c: {stats['a1c']}%

🎯 Time in Ranges:
• 🔴 Severe hypo (<3.0 mmol): {stats['very_low_pct']}% (goal <1%)
• 🟠 Hypoglycemia (3.0-3.9 mmol): {stats['low_pct']}% (goal <4%)
• 🟢 In target ({tir_label}): {stats['tir']}% {tir_status} (goal ≥{tir_goal}%)
• 🟡 Above target: {stats['above_target_pct']}%
• 🟠 High (10.0-13.9 mmol): {stats['high_pct']}%
• 🔴 Very high (>13.9 mmol): {stats['very_high_pct']}% (goal <5%)

💡 Assessment:"""
    
    if stats["tir"] >= tir_goal:
        text += f"\n• ✅ TIR goal of {tir_goal}% achieved!"
    else:
        text += f"\n• ⚠️ {tir_goal - stats['tir']:.1f}% away from TIR goal of {tir_goal}%"
    
    if stats["cv"] <= 33:
        text += "\n• ✅ Excellent glucose stability"
    elif stats["cv"] <= 36:
        text += "\n• 📊 Good stability"
    else:
        text += "\n• ⚠️ High variability"
    
    return [TextContent(type="text", text=text)]


async def analyze_monthly(year: int, from_month: int, to_month: int, tir_goal: int) -> list[TextContent]:
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    results = []
    
    tir_label = get_tir_range_label()
    
    text = f"📊 Glucose Analysis for {year} (TIR goal: {tir_goal}%)\n"
    text += "=" * 80 + "\n"
    text += f"Month │  TIR ({tir_label})  │  Avg  │   CV   │  A1c  │ Readings\n"
    text += "-" * 80 + "\n"
    
    for month in range(from_month, to_month + 1):
        start_dt = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_dt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
        
        try:
            entries = await client.fetch_entries_in_range(start_ts, end_ts)
            sgv_values = filter_valid_sgv(entries)
            stats = calculate_stats(sgv_values)
            
            if stats and stats["count"] > 0:
                results.append({"month": month, "stats": stats})
                tir_emoji = "✅" if stats["tir"] >= tir_goal else "⚠️" if stats["tir"] >= 70 else "❌"
                cv_emoji = "✅" if stats["cv"] <= 33 else "⚠️" if stats["cv"] <= 36 else "❌"
                text += f"{month_names[month]:5} │ {stats['tir']:6.1f}% {tir_emoji}    │ {stats['avg_formatted']:>5} │ {stats['cv']:5.1f}% {cv_emoji} │ {stats['a1c']:4.1f}% │ {stats['count']:>8,}\n"
            else:
                text += f"{month_names[month]:5} │ No data\n"
        except Exception as e:
            text += f"{month_names[month]:5} │ Error: {str(e)[:40]}\n"
    
    text += "=" * 80 + "\n"
    
    if results:
        avg_tir = sum(r["stats"]["tir"] for r in results) / len(results)
        avg_cv = sum(r["stats"]["cv"] for r in results) / len(results)
        avg_glucose = sum(r["stats"]["avg"] for r in results) / len(results)
        avg_a1c = sum(r["stats"]["a1c"] for r in results) / len(results)
        total_count = sum(r["stats"]["count"] for r in results)
        
        tir_status = "✅ GOAL MET" if avg_tir >= tir_goal else f"⚠️ {tir_goal - avg_tir:.1f}% to goal"
        
        text += f"\n📈 SUMMARY ({len(results)} months, {total_count:,} readings)\n"
        text += "-" * 60 + "\n"
        text += f"🎯 Average TIR ({tir_label}): {avg_tir:.1f}% — {tir_status}\n"
        text += f"📊 Average glucose: {format_glucose(avg_glucose)}\n"
        text += f"📉 Average CV: {avg_cv:.1f}% — {'✅ Stable' if avg_cv <= 33 else '📊 OK' if avg_cv <= 36 else '⚠️ High'}\n"
        text += f"🩸 Estimated HbA1c: {avg_a1c:.1f}%\n"
        
        # Best/worst
        best = max(results, key=lambda r: r["stats"]["tir"])
        worst = min(results, key=lambda r: r["stats"]["tir"])
        text += f"\n🏆 Best TIR: {month_names[best['month']]} — {best['stats']['tir']:.1f}%\n"
        text += f"📉 Worst TIR: {month_names[worst['month']]} — {worst['stats']['tir']:.1f}%\n"
    
    return [TextContent(type="text", text=text)]


async def treatments(hours: int, count: int) -> list[TextContent]:
    now = datetime.now(timezone.utc)
    start_dt = now.timestamp() * 1000 - hours * 60 * 60 * 1000
    
    params = {
        "count": count,
        "find[created_at][$gte]": datetime.fromtimestamp(start_dt / 1000, tz=timezone.utc).isoformat(),
    }
    
    data = await client.fetch("/api/v1/treatments.json", params)
    if not data:
        return [TextContent(type="text", text=f"No treatments in the last {hours} hours")]
    
    total_insulin = 0
    total_carbs = 0
    text = f"💉 Treatments for {hours}h:\n"
    
    for t in data:
        dt = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
        line = f"• {dt.strftime('%m-%d %H:%M')}: "
        if t.get("eventType"):
            line += f"[{t['eventType']}] "
        if t.get("insulin"):
            line += f"💉 {t['insulin']} U "
            total_insulin += t["insulin"]
        if t.get("carbs"):
            line += f"🍞 {t['carbs']} g "
            total_carbs += t["carbs"]
        if t.get("notes"):
            line += f"📝 {t['notes']}"
        text += line + "\n"
    
    text += f"\n📊 Totals:"
    if total_insulin > 0:
        text += f" 💉 {total_insulin:.1f} U"
    if total_carbs > 0:
        text += f" 🍞 {total_carbs} g"
    
    return [TextContent(type="text", text=text)]


async def status() -> list[TextContent]:
    data = await client.fetch("/api/v1/status.json")
    
    text = f"""⚙️ Nightscout Status:
• Name: {data.get('name', 'N/A')}
• Version: {data.get('version', 'N/A')}
• Server time: {data.get('serverTime', 'N/A')}
• Units: {data.get('settings', {}).get('units', 'mg/dl')}"""
    
    thresholds = data.get("settings", {}).get("thresholds")
    if thresholds:
        text += f"""

🎯 Thresholds:
• High: {thresholds.get('bgHigh')} mg/dL
• Target top: {thresholds.get('bgTargetTop')} mg/dL
• Target bottom: {thresholds.get('bgTargetBottom')} mg/dL
• Low: {thresholds.get('bgLow')} mg/dL"""
    
    return [TextContent(type="text", text=text)]


async def devices(count: int) -> list[TextContent]:
    data = await client.fetch("/api/v1/devicestatus.json", {"count": count})
    if not data:
        return [TextContent(type="text", text="No device data available")]
    
    text = "📱 Device Status:\n"
    
    for d in data:
        dt = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
        text += f"\n⏰ {dt.strftime('%H:%M')}:"
        if d.get("uploader"):
            text += f"\n  📱 Uploader: battery {d['uploader'].get('battery', '?')}%"
        if d.get("pump"):
            pump = d["pump"]
            text += f"\n  💉 Pump: reservoir {pump.get('reservoir', '?')}U, battery {pump.get('battery', {}).get('percent', '?')}%"
        if d.get("device"):
            text += f"\n  📡 Device: {d['device']}"
    
    return [TextContent(type="text", text=text)]


def main():
    """Main entry point."""
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
