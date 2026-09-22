from __future__ import annotations

import base64
import os
import re
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from desktop_agent.tools.base import Tool, ToolResult, VerificationResult

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_OUTLOOK_PS = r"""
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$query = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:DA_PERSON_B64))
function Out-Person($name, $email, $source) {
  if ([string]::IsNullOrWhiteSpace($email) -or ($email -notmatch '@')) { return }
  $n = (($name | Out-String) -replace '[\t\r\n]', ' ').Trim()
  $e = (($email | Out-String) -replace '[\t\r\n\s]', '').Trim()
  Write-Output ("{0}`t{1}`t{2}" -f $n, $e, $source)
}
function Smtp-FromEntry($ae) {
  if ($null -eq $ae) { return $null }
  try {
    $ex = $ae.GetExchangeUser()
    if ($ex -and $ex.PrimarySmtpAddress) { return [string]$ex.PrimarySmtpAddress }
  } catch {}
  try {
    $smtp = $ae.PropertyAccessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x39FE001F')
    if ($smtp) { return [string]$smtp }
  } catch {}
  $addr = [string]$ae.Address
  if ($addr -match '@') { return $addr }
  return $null
}
try {
  $ol = New-Object -ComObject Outlook.Application
  $ns = $ol.GetNamespace('MAPI')
} catch {
  Write-Error $_.Exception.Message
  exit 2
}
try {
  $r = $ns.CreateRecipient($query)
  if ($r.Resolve()) {
    $ae = $r.AddressEntry
    $smtp = Smtp-FromEntry $ae
    $display = [string]$ae.Name
    try {
      $ex = $ae.GetExchangeUser()
      if ($ex -and $ex.Name) { $display = [string]$ex.Name }
    } catch {}
    Out-Person $display $smtp 'gal'
  }
} catch {}
try {
  $folder = $ns.GetDefaultFolder(10)
  $items = $folder.Items
  $max = [Math]::Min($items.Count, 500)
  $tokens = @($query.ToLower() -split '\W+' | Where-Object { $_.Length -gt 1 })
  for ($i = 1; $i -le $max; $i++) {
    try {
      $item = $items.Item($i)
      $full = [string]$item.FullName
      $e1 = [string]$item.Email1Address
      $hay = ($full + ' ' + $e1).ToLower()
      $ok = $true
      foreach ($t in $tokens) {
        if ($hay.IndexOf($t) -lt 0) { $ok = $false; break }
      }
      if ($ok) { Out-Person $full $e1 'contacts' }
    } catch {}
  }
} catch {}
"""


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def is_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch((value or "").strip()))


def names_match(query: str, candidate: str) -> int:
    """Fuzzy name score so 'Janapati Thanusree' matches 'Janapati Thanu Sree'."""
    return score_person(candidate, "", query)


def score_person(name: str, email: str, query: str) -> int:
    q = query.lower().strip()
    n = (name or "").lower()
    compact_q = _compact(q)
    compact_n = _compact(n)
    score = 0
    if q == n or compact_q and compact_q == compact_n:
        score = 100
    elif compact_q and (compact_q in compact_n or compact_n in compact_q):
        score = 90
    tokens = [token for token in re.split(r"\W+", q) if len(token) > 1]
    if tokens and all(_compact(token) in compact_n for token in tokens):
        score = max(score, 86)
    if tokens and all(token in n for token in tokens):
        score = max(score, 84)
    local = (email or "").split("@")[0].lower()
    compact_local = _compact(local)
    if compact_q and compact_q in compact_local:
        score = max(score, 78)
    if tokens and all(token in local for token in tokens):
        score = max(score, 72)
    return score


def _parse_outlook_rows(stdout: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in (stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 2:
            continue
        name, email = parts[0].strip(), parts[1].strip()
        source = parts[2].strip() if len(parts) > 2 else "outlook"
        key = email.lower()
        if not is_email(email) or key in seen:
            continue
        seen.add(key)
        rows.append({"name": name or email, "email": email, "source": source})
    return rows


def query_outlook(query: str, timeout_s: float = 25) -> tuple[list[dict[str, str]], str | None]:
    if not query.strip():
        return [], None
    env = os.environ.copy()
    env["DA_PERSON_B64"] = base64.b64encode(query.strip().encode("utf-8")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _OUTLOOK_PS,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if completed.returncode not in (0, None) and not completed.stdout.strip():
        err = (completed.stderr or "").strip() or f"Outlook lookup exited {completed.returncode}"
        return [], err
    return _parse_outlook_rows(completed.stdout), None


def pick_person(query: str, matches: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    q = query.strip()
    if is_email(q):
        chosen = {"name": q, "email": q, "source": "email", "score": 100}
        return chosen, [chosen]
    scored: list[dict[str, Any]] = []
    for item in matches:
        value = score_person(str(item.get("name") or ""), str(item.get("email") or ""), q)
        if not value:
            continue
        scored.append({**item, "score": value})
    scored.sort(key=lambda item: (-int(item["score"]), str(item.get("name") or "").lower()))
    if not scored:
        return None, []
    top = scored[0]
    second = scored[1]["score"] if len(scored) > 1 else 0
    if top["score"] >= 84 and top["score"] - second >= 8:
        return top, scored
    if len(scored) == 1:
        return top, scored
    return None, scored[:6]


def resolve_recipient(query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if is_email(q):
        chosen = {"name": q, "email": q, "source": "email", "score": 100}
        return {"query": q, "chosen": chosen, "matches": [chosen], "error": None}
    matches, error = query_outlook(q)
    chosen, ranked = pick_person(q, matches)
    return {"query": q, "chosen": chosen, "matches": ranked or matches, "error": error}


class PersonQueryArgs(BaseModel):
    query: str = Field(description="Person's name as spoken, e.g. Janapati Thanusree")


def build_people_tools() -> list[Tool]:
    def lookup_person(args: PersonQueryArgs) -> ToolResult:
        result = resolve_recipient(args.query)
        chosen = result.get("chosen")
        return ToolResult(
            ok=True,
            data={
                "query": args.query,
                "found": bool(chosen or result.get("matches")),
                "chosen": chosen,
                "matches": result.get("matches") or [],
                "error": result.get("error"),
            },
            evidence={
                "found": bool(chosen),
                "email": None if not chosen else chosen.get("email"),
                "name": None if not chosen else chosen.get("name"),
            },
        )

    def verify(_args: PersonQueryArgs, result: ToolResult) -> VerificationResult:
        return VerificationResult(verified=True, evidence=result.evidence, reason="directory lookup")

    return [
        Tool(
            name="lookup_person",
            description=(
                "Look up a coworker in Outlook. Do NOT use this when the user wants to send a Teams message; "
                "call send_teams_message instead, which searches Teams chats directly."
            ),
            parameters=PersonQueryArgs,
            timeout_s=30,
            handler=lookup_person,
            verifier=verify,
        )
    ]
