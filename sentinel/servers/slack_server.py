"""MCP server: Slack. Sends ONLY approved, unmodified messages. Success is reported only when Slack confirms (ok + ts)."""
from __future__ import annotations
from .. import db
from ..config import env
from ..schemas import SlackMessage
from .common import err, make, ok, require_approval

mcp = make("sentinel-slack", "slack")

def _client():
    from slack_sdk import WebClient
    return WebClient(token=env("SLACK_BOT_TOKEN"), timeout=15)

@mcp.tool()
def slack_status() -> dict:
    """Health check: validates the bot token with Slack auth.test."""
    if not env("SLACK_BOT_TOKEN"):
        db.set_integration("slack", "disconnected", "disconnected", "SLACK_BOT_TOKEN not set")
        return err("SLACK_BOT_TOKEN is not set – Slack is disconnected (messages cannot be sent).")
    try:
        r = _client().auth_test()
        d = f"Workspace {r.get('team')} as {r.get('user')}"
        db.set_integration("slack", "live", "connected", d)
        return ok({"detail": d}, "live", "Slack")
    except Exception as e:
        db.set_integration("slack", "live", "error", str(e))
        return err(f"Slack authentication failed: {str(e)[:160]}", "live")

@mcp.tool()
def send_slack_notification(channel: str, text: str, approval_id: int) -> dict:
    """CONTROLLED ACTION: post a message to Slack. Requires an approved 'send_slack' action whose channel and text match exactly."""
    try:
        req = SlackMessage(channel=channel, text=text, approval_id=approval_id)
    except Exception as e:
        return err(f"Invalid message: {e}", "live")
    bad = require_approval(req.approval_id, "send_slack", {"channel": req.channel, "text": req.text})
    if bad:
        return err(bad, "live")
    if not env("SLACK_BOT_TOKEN"):
        return err("Slack is not connected (SLACK_BOT_TOKEN missing). Nothing was sent.", "disconnected")
    try:
        from slack_sdk.errors import SlackApiError
        try:
            r = _client().chat_postMessage(channel=req.channel, text=req.text)
        except SlackApiError as e:
            if e.response.status_code == 429:
                return err(f"Slack rate limit – retry after {e.response.headers.get('Retry-After', '?')}s. Nothing was sent.", "live")
            return err(f"Slack rejected the message: {e.response.get('error', 'unknown')}. Nothing was sent.", "live")
        if r.get("ok") and r.get("ts"):
            return ok({"channel": r.get("channel"), "ts": r.get("ts")}, "live", "Slack", confirmed=True)
        return err("Slack did not confirm delivery.", "live")
    except Exception as e:
        return err(f"Slack call failed: {str(e)[:160]}. Nothing was sent.", "live")

if __name__ == "__main__":
    db.init_db()
    mcp.run(transport="streamable-http")
